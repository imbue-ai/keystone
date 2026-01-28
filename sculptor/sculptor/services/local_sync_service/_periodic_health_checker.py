import re
import shlex
import textwrap
import threading
from collections import deque
from enum import auto
from pathlib import Path
from time import localtime
from time import strftime
from typing import Any
from typing import Callable
from typing import Collection
from typing import Final
from typing import Generic
from typing import TypeVar
from typing import assert_never

from loguru import logger
from pydantic import Field
from pydantic import PrivateAttr
from watchdog.events import FileCreatedEvent

from imbue_core.constants import ExceptionPriority
from imbue_core.itertools import first
from imbue_core.itertools import remove_none
from imbue_core.pydantic_serialization import FrozenModel
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.thread_utils import ObservableThread
from imbue_core.thread_utils import log_exception
from imbue_core.upper_case_str_enum import UpperCaseStrEnum
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion
from sculptor.interfaces.environments.base import Environment
from sculptor.services.local_sync_service._misc_utils_and_constants import LazilySetCallback
from sculptor.services.local_sync_service._misc_utils_and_constants import NoticeTuple
from sculptor.services.local_sync_service._misc_utils_and_constants import WatchedEventType
from sculptor.services.local_sync_service._misc_utils_and_constants import monotonic_epoch_time
from sculptor.services.local_sync_service._watchmedo_via_environment import FileModifiedEvent
from sculptor.services.local_sync_service.path_batch_scheduler import NoticeBasedHealthCheck
from sculptor.utils.build import get_sculptor_folder

DirT = TypeVar("DirT", Path, str)

HEALTHCHECK_TAG: Final = "local_sync_healthchecks"
HEALTHCHECK_ROUGH_INTERVAL_SECONDS: Final = 5.0
HEALTHCHECK_TIMEOUT_SECONDS: Final = 5.0
HEALTHCHECK_HISTORY_LENGTH: Final = 10

# File paths for healthcheck files
ENVIRONMENT_HEALTHCHECK_DIR: Final = f"/imbue_addons/sculptor_{HEALTHCHECK_TAG}"

_ERR_PRIORITY = ExceptionPriority.LOW_PRIORITY
_HEALTHCHECK_ID_PATTERN: Final = re.compile(r"\d{10,}+\.\d+")


def get_local_healthcheck_dir() -> Path:
    """Get the local healthcheck file path."""
    return (get_sculptor_folder() / HEALTHCHECK_TAG).resolve()


def _get_history_deque(length: int = HEALTHCHECK_HISTORY_LENGTH) -> deque[Any]:
    return deque(maxlen=length)


class _Check(FrozenModel):
    signal_id: str
    signaled_timestamp: float

    def describe(self) -> str:
        ts = strftime("%H:%M:%S", localtime(self.signaled_timestamp))
        return f"{self.signal_id} @ {ts}"


class _PendingCheck(_Check):
    def succeed(self, latency: float) -> "_SuccessfulCheck":
        return _SuccessfulCheck(signal_id=self.signal_id, signaled_timestamp=self.signaled_timestamp, latency=latency)

    def fail(self, latency: float) -> "_FailedCheck":
        return _FailedCheck(signal_id=self.signal_id, signaled_timestamp=self.signaled_timestamp, latency=latency)

    def describe(self) -> str:
        return f"{super().describe()} PENDING"


class _CompletedCheck(_Check):
    latency: float  # from acknowledgement or timeout

    def describe(self) -> str:
        sec_ago = int(monotonic_epoch_time() - self.signaled_timestamp)
        ago = f"{sec_ago}s ago"
        if sec_ago > 60:
            min_ago = sec_ago // 60
            ago = f"{min_ago}m ago"
        return f"{super().describe()}, {ago}"


class _SuccessfulCheck(_CompletedCheck):
    @property
    def seen_timestamp(self) -> float:
        return self.signaled_timestamp + self.latency

    def describe(self) -> str:
        return f"{super().describe()} OK (ACK took {self.latency:.3f})"


class _FailedCheck(_CompletedCheck):
    @property
    def timeout_timestamp(self) -> float:
        return self.signaled_timestamp + self.latency

    def describe(self) -> str:
        return f"{super().describe()}, FAILED"


class _Severity(UpperCaseStrEnum):
    ALL_OK = auto()
    FLAKY = auto()
    DISCONNECTED = auto()


class _HealthCheckStatus(FrozenModel):
    latest_successes: tuple[_SuccessfulCheck, ...]
    latest_checks: tuple[_SuccessfulCheck | _FailedCheck, ...]

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)
        assert len(self.latest_checks) > 0, "should only produce status after first check"

    @property
    def is_most_recent_check_failed(self) -> bool:
        return isinstance(self.latest_checks[-1], _FailedCheck)

    @property
    def severity(self) -> _Severity:
        """
        Implements the following ratchet policy espoused as AMZN Enterprise Approved TM:
        - 0/3 Most recent failed -> ALL_OK
        - FLAKY is currenty unused
        - 2/3 Most recent failed -> DISCONNECTED
        """
        last_three = self.latest_checks[-3:]
        recently_failed = sum(1 if isinstance(check, _FailedCheck) else 0 for check in last_three)
        is_starting_disconnected = recently_failed > 0 and len(last_three) == 1
        if recently_failed >= 2 or is_starting_disconnected:
            return _Severity.DISCONNECTED
        return _Severity.ALL_OK

    @property
    def _describe_recent_success_rate(self) -> str:
        succeeded = sum(1 if isinstance(check, _SuccessfulCheck) else 0 for check in self.latest_checks)
        total = len(self.latest_checks)
        failed = total - succeeded
        return f"{succeeded} OK and {failed} FAILED out of {total} recent checks"

    def describe(self) -> str:
        if len(self.latest_successes) > 0:
            last_success = "Last successful check: " + self.latest_successes[-1].describe()
        else:
            last_success = "No successful checks seen"
        return f"{self.severity.value}: {self._describe_recent_success_rate}. {last_success}."


class _BaseHealthChecker(MutableModel, Generic[DirT]):
    """Writes unix timestamps to a healthcheck file periodically.

    Owns a background thread that writes the current timestamp every HEALTHCHECK_INTERVAL_SECONDS.

    Maintains a _lock to ensures state changes are thread-safe, as they can be triggered from either:
    * the background thread (do_periodic_healthcheck)
    * or called directly from a file handler (maybe_process_event)
    """

    healthcheck_dir: DirT

    stop_event: threading.Event
    check_count: int = 0
    max_round_trip_wait_seconds: float = HEALTHCHECK_TIMEOUT_SECONDS

    _most_recent_successful_checks: deque[_SuccessfulCheck] = PrivateAttr(default_factory=_get_history_deque)
    _never_seen_failed_checks: deque[_FailedCheck] = PrivateAttr(default_factory=lambda: _get_history_deque(100))
    _most_recent_checks: deque[_SuccessfulCheck | _FailedCheck] = PrivateAttr(default_factory=_get_history_deque)
    _pending_checks: list[_PendingCheck] = PrivateAttr(default_factory=list)

    # see docstring - protects mutations that can be triggered from background_thread or file event handler.
    # Currently, no attributes should be mutated without the lock (except stop_event externally)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    background_thread: ObservableThread | None = Field(init=False, default=None)

    healthcheck_signal_side_effect: LazilySetCallback[[], None] = Field(
        default_factory=lambda: LazilySetCallback[[], None]()
    )

    # shouldn't need a lock b/c is frozen I think?
    latest_status: _HealthCheckStatus | None = None

    def _maybe_complete_signal(
        self, pending_check: _PendingCheck, acknowledged_ids: set[str]
    ) -> _SuccessfulCheck | _FailedCheck | None:
        """Get the last written timestamp (thread-safe)."""
        assert self._lock.locked(), "should be called within lock"
        latency_so_far = monotonic_epoch_time() - pending_check.signaled_timestamp
        if pending_check.signal_id in acknowledged_ids:
            acknowledged_ids.remove(pending_check.signal_id)
            self._pending_checks.remove(pending_check)
            return pending_check.succeed(latency_so_far)

        if latency_so_far > self.max_round_trip_wait_seconds:
            self._pending_checks.remove(pending_check)
            return pending_check.fail(latency_so_far)

    def _debug_late_signal_acknowledgements(self, acknowledged_signal_ids: set[str]) -> None:
        for ack_remaining in [*acknowledged_signal_ids]:
            matching_timeout = first(
                check for check in self._never_seen_failed_checks if check.signal_id == ack_remaining
            )
            if matching_timeout is not None:
                self._never_seen_failed_checks.remove(matching_timeout)
                logger.debug(
                    "{}.{}: timeout {} was late by {}s",
                    HEALTHCHECK_TAG,
                    str(self.__class__.__name__),
                    matching_timeout.describe(),
                    int(monotonic_epoch_time() - matching_timeout.timeout_timestamp),
                )
                acknowledged_signal_ids.remove(ack_remaining)

    def _resolve_any_completed_checks(self, acknowledged_signal_ids: set[str] | None) -> None:
        assert self._lock.locked(), "should be called within lock"
        _acked = acknowledged_signal_ids or set()

        for pending_timestamp in self._pending_checks[:]:
            check = self._maybe_complete_signal(pending_timestamp, _acked)
            if check is None:
                continue
            self._most_recent_checks.append(check)
            if isinstance(check, _SuccessfulCheck):
                self._most_recent_successful_checks.append(check)
            elif isinstance(check, _FailedCheck):
                self._never_seen_failed_checks.append(check)

        self._debug_late_signal_acknowledgements(_acked)

    def _resolve_latest_status(self, acknowledged_signal_ids: set[str] | None = None) -> None:
        with self._lock:
            self._resolve_any_completed_checks(acknowledged_signal_ids)
            if not self._most_recent_checks:
                logger.trace("{}: No completed healthchecks yet (including timeouts)", HEALTHCHECK_TAG)
                return
            self.latest_status = _HealthCheckStatus(
                latest_successes=tuple(self._most_recent_successful_checks),
                latest_checks=tuple(self._most_recent_checks),
            )

    def _resolve_latest_status_from_event_paths(self, event_paths: Collection[Path]) -> tuple[bool, set[str]]:
        """
        1. claim the event if it is in our healthcheck dir
        2. match healthcheck filenames in the event and add them to our batch for processing

        NOTE: no mutations, only expected non-healthcheck path is the healthcheck dir itself
        """
        is_claimed = False
        signals = set()
        for path in event_paths:
            if not path.is_relative_to(self.healthcheck_dir):
                continue
            is_claimed = True
            if _HEALTHCHECK_ID_PATTERN.match(path.name):
                signals.add(path.name)
        return is_claimed, signals

    def do_periodic_healthcheck(self, just_sent_check: _PendingCheck) -> None:
        self._resolve_latest_status()
        self.healthcheck_signal_side_effect()

    def maybe_process_event(self, event_paths: Collection[Path]) -> bool:
        is_claimed, acknowledged_signal_ids = self._resolve_latest_status_from_event_paths(event_paths)
        if is_claimed:
            # we want to resolve statuses even if we didn't see any acks, because timeouts may have occurred
            self._resolve_latest_status(acknowledged_signal_ids)
        return is_claimed


_AnyChecker = _BaseHealthChecker[str] | _BaseHealthChecker[Path]


class _LocalHealthChecker(_BaseHealthChecker[Path]):
    healthcheck_dir: Path = Field(default_factory=get_local_healthcheck_dir)

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)
        self.healthcheck_dir.mkdir(parents=True, exist_ok=True)
        self.background_thread = ObservableThread(
            target=self._write_local_healthcheck_at_interval,
            name=f"{HEALTHCHECK_TAG}_on_local",
            daemon=True,
        )

    def _wait_time(self, last_timestamp: float | None) -> float:
        if last_timestamp is None:
            return HEALTHCHECK_ROUGH_INTERVAL_SECONDS
        time_since_last = monotonic_epoch_time() - last_timestamp
        return max(HEALTHCHECK_ROUGH_INTERVAL_SECONDS - time_since_last, 0.1)

    def _write_local_healthcheck_at_interval(self) -> None:
        """Main loop that writes timestamp to file every interval."""
        logger.trace("{}.local: starting healthcheck writer thread for {}", HEALTHCHECK_TAG, self.healthcheck_dir)
        last_timestamp = None
        while not self.stop_event.wait(self._wait_time(last_timestamp)):
            if not self.healthcheck_dir.exists():
                # if this happens we're probably crashing and there are bigger fish to fry
                logger.error("{}.local dir {} went missing: pausing checks", HEALTHCHECK_TAG, self.healthcheck_dir)
                last_timestamp = None
                continue
            last_timestamp = monotonic_epoch_time()
            last_marker = self.healthcheck_dir / str(last_timestamp)
            check: _PendingCheck
            try:
                check = _PendingCheck(signal_id=last_marker.name, signaled_timestamp=last_timestamp)
                with self._lock:
                    self.check_count += 1
                    self._pending_checks.append(check)
                last_marker.write_text(str(last_timestamp))
                last_marker.unlink()  # all we need is a write, no longer needed.
            except Exception as e:
                log_exception(
                    e,
                    "Failure in _write_and_monitor_local_healthcheck_latency (fp={fp})",
                    _ERR_PRIORITY,
                    fp=self.healthcheck_dir,
                )
            self.do_periodic_healthcheck(check)
        logger.debug("{}.local: healthcheck writer thread has exited", HEALTHCHECK_TAG)


def _pipe_healthcheck_signals_from_environment_into_sink(
    environment: Environment,
    stopped_event: threading.Event,
    healthcheck_dir: str,
    sink: Callable[[_PendingCheck], None],
) -> None:
    healthcheck_dir = shlex.quote(healthcheck_dir)
    logger.debug("{}.agent: starting healthcheck writer thread for {}", HEALTHCHECK_TAG, healthcheck_dir)
    # Write a new file every HEALTHCHECK_ROUGH_INTERVAL_SECONDS with the current timestamp and clear old files every 5 min.
    # This way we can measure each latency without having to start a nwe process each time.
    #
    # TODO: Consider insta-deleting the files after writing, since we only care about the fs event,
    #       and that way we'd only really have one ack per check
    touch_timestamp_named_files_deleting_all_every_5_min = textwrap.dedent(
        f"""
        mkdir -p {healthcheck_dir}
        while true; do
            sleep {HEALTHCHECK_ROUGH_INTERVAL_SECONDS - 1}
            healthcheck_timestamp="$(date +%s.%N)"
            echo $healthcheck_timestamp
            sleep 1
            echo $healthcheck_timestamp > {healthcheck_dir}/$healthcheck_timestamp
            rm -f {healthcheck_dir}/$healthcheck_timestamp
        done;
        """
    ).strip()
    cmd = ("/imbue/nix_bin/bash", "-c", touch_timestamp_named_files_deleting_all_every_5_min)
    healthcheck_process = environment.run_process_in_background(
        cmd, {}, shutdown_event=stopped_event, run_as_root=True
    )
    for single_line, is_stdout in healthcheck_process.stream_stdout_and_stderr():
        single_line = single_line.strip()
        if not is_stdout:
            logger.error("{} error: {}", HEALTHCHECK_TAG, single_line)
            continue
        sink(_PendingCheck(signal_id=single_line, signaled_timestamp=monotonic_epoch_time()))
    logger.debug("{}.agent: healthcheck writer thread has exited", HEALTHCHECK_TAG)


class _EnvironmentHealthChecker(_BaseHealthChecker[str]):
    environment: Environment
    healthcheck_dir: str = ENVIRONMENT_HEALTHCHECK_DIR

    def model_post_init(self, context: Any) -> None:
        self.background_thread = ObservableThread(
            target=_pipe_healthcheck_signals_from_environment_into_sink,
            args=(
                self.environment,
                self.stop_event,
                self.healthcheck_dir,
                self._consume_and_pass_healthcheck_signal_side_effect,
            ),
            name=f"{HEALTHCHECK_TAG}_on_env",
            daemon=True,
        )

    def _consume_and_pass_healthcheck_signal_side_effect(self, signal: _PendingCheck) -> None:
        with self._lock:
            self.check_count += 1
            self._pending_checks.append(signal)
        self.do_periodic_healthcheck(just_sent_check=signal)


class LocalSyncHealthChecker(NoticeBasedHealthCheck):
    """Tracks when health checkers create their markers and complains if either of latest timeout waiting for an event"""

    tag: str = HEALTHCHECK_TAG
    local_checker: _LocalHealthChecker
    environment_checker: _EnvironmentHealthChecker
    grace_period_seconds: float
    _init_time: float = PrivateAttr(default_factory=monotonic_epoch_time)

    _proactively_flag_notices: Callable[[NoticeTuple], None] | None = PrivateAttr(default=None)

    # TODO: Consider making the subcheckers private and only passing these into the bg thread constructor
    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)
        assert self.local_checker.stop_event == self.environment_checker.stop_event, "Should only have one stop event"
        for checker in (self.local_checker, self.environment_checker):
            checker.healthcheck_signal_side_effect.set_once(self._periodic_proactive_healthcheck)

    @property
    def local_dirs_to_watch(self) -> tuple[Path, ...]:
        return (self.local_checker.healthcheck_dir,)

    @property
    def environment_dirs_to_watch(self) -> tuple[Path, ...]:
        return (Path(self.environment_checker.healthcheck_dir),)

    @property
    def _ignore_failures_before(self) -> float:
        return self._init_time + self.grace_period_seconds

    def _extract_notice_if_not_ok(self, tag: str, checker: _AnyChecker) -> LocalSyncNoticeUnion | None:
        status = checker.latest_status
        if status is None:
            logger.trace("{} watcher: status is None, shouldn't be for long checker={}", tag, checker)
            return None
        match status.severity:
            case _Severity.ALL_OK:
                return None
            case _Severity.FLAKY:
                desc = f"{tag} watcher flaky, status=" + status.describe()
                return LocalSyncNoticeOfPause(source_tag=self.tag, reason=desc)
            case _Severity.DISCONNECTED:
                desc = f"{tag} watcher disconnected, status=" + status.describe()
                return LocalSyncNoticeOfPause(source_tag=self.tag, reason=desc)
            case _ as unreachable:
                assert_never(unreachable)  # pyre-ignore[6]: pyre doesn't understand exhaustive matching on enums

    def get_notices(self) -> NoticeTuple:
        """Check if healthcheck files are stale and return pause notices."""
        # During grace period, don't report any failures to allow system to stabilize
        if monotonic_epoch_time() < self._ignore_failures_before:
            return ()

        problems = (
            self._extract_notice_if_not_ok("Local", self.local_checker),
            self._extract_notice_if_not_ok("Agent", self.environment_checker),
        )
        for problem in problems:
            if problem is not None:
                logger.debug("{}.{}", HEALTHCHECK_TAG, problem.reason)
        return tuple(remove_none(problems))

    def _periodic_proactive_healthcheck(self) -> None:
        """Gets registered as a callback on both checkers.

        Idea is we piggieback on the background thread scheduling so if either thread is ok we will flag notices if necessary.
        The scheduler itself will know whether it needs to do anything or not, so we don't have to worry about calling it excessively.
        """
        notices = self.get_notices()
        if len(notices) > 0:
            self.flag_notices_out_of_band(notices)

    def maybe_intercept_event(self, event: WatchedEventType, paths: Collection[Path]) -> tuple[NoticeTuple, bool]:
        """Update tracking when healthcheck files are modified."""
        # TODO: actual integration tests
        # Use for testing disconnect and recovery
        # pause_ever_other_n_seconds = 30
        # is_currently_swallowing_events = int(_get_timestamp() // pause_ever_other_n_seconds) % 2 == 0
        # if is_currently_swallowing_events:
        #    logger.debug("{}.local: testing preempting all events for 30 sec", HEALTHCHECK_TAG)
        #    return (self.get_notices(), True)

        # TODO monitor more kinds of events?
        if not isinstance(event, (FileCreatedEvent, FileModifiedEvent)):
            return ((), False)

        for checker in (self.local_checker, self.environment_checker):
            is_event_claimed = checker.maybe_process_event(paths)
            if is_event_claimed:
                return (self.get_notices(), True)

        return ((), False)

    @property
    def background_threads(self) -> tuple[ObservableThread, ...]:
        assert self.local_checker.background_thread is not None, "should be set in post init"
        assert self.environment_checker.background_thread is not None, "should be set in post init"
        return (self.local_checker.background_thread, self.environment_checker.background_thread)

    @classmethod
    def build(
        cls, stop_event: threading.Event, agent_environment: Environment, grace_period_seconds: float = 0.0
    ) -> "LocalSyncHealthChecker":
        return cls(
            local_checker=_LocalHealthChecker(stop_event=stop_event),
            environment_checker=_EnvironmentHealthChecker(stop_event=stop_event, environment=agent_environment),
            grace_period_seconds=grace_period_seconds,
        )
