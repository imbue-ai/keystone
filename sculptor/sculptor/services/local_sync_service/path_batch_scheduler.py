import json
import threading
from abc import ABC
from abc import abstractmethod
from contextlib import contextmanager
from enum import auto
from functools import cached_property
from pathlib import Path
from typing import Collection
from typing import Final
from typing import Generator
from typing import Mapping
from typing import ParamSpec
from typing import TypeVar
from typing import cast

from loguru import logger
from pydantic import Field
from watchdog.events import FileSystemEvent
from watchdog.events import FileSystemEventHandler

from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import truncate_string
from imbue_core.constants import ExceptionPriority
from imbue_core.itertools import generate_flattened
from imbue_core.itertools import only
from imbue_core.itertools import remove_none
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.upper_case_str_enum import UpperCaseStrEnum
from sculptor.interfaces.agents.agent import LocalSyncNonPausingNoticeUnion
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import BundledThreadingContext
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import DEFAULT_LOCAL_SYNC_DEBOUNCE_SECONDS
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import DEFAULT_LOCAL_SYNC_MAX_DEBOUNCE_SECONDS
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import DebounceController
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import SlightlySaferObserver
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import poll_for_locks_or_give_up_on_stop_event
from sculptor.services.local_sync_service._misc_utils_and_constants import EVENT_TYPES_TO_WATCH
from sculptor.services.local_sync_service._misc_utils_and_constants import LazilySetCallback
from sculptor.services.local_sync_service._misc_utils_and_constants import NoticeTuple
from sculptor.services.local_sync_service._misc_utils_and_constants import WatchedEventType
from sculptor.services.local_sync_service._misc_utils_and_constants import extract_touched_paths
from sculptor.services.local_sync_service._misc_utils_and_constants import is_event_type_to_watch
from sculptor.services.local_sync_service._misc_utils_and_constants import is_pause_necessary
from sculptor.services.local_sync_service._misc_utils_and_constants import separate_pause_notices
from sculptor.services.local_sync_service._misc_utils_and_constants import simplify_root_watcher_paths
from sculptor.services.local_sync_service.errors import NewNoticesInSyncHandlingError
from sculptor.utils.shared_exclusive_lock import SharedExclusiveLock
from sculptor.utils.timeout import log_runtime

P = ParamSpec("P")
ReturnT = TypeVar("ReturnT")

SCHEDULER_CAUGHT_EXCEPTION: Final = "scheduler_caught_exception"


def _unhandled_exception_issue_identifier(tag: str, exception: Exception) -> tuple[str, str]:
    "A suitable identifier for an exception caught by the scheduler itself"
    return (tag, str(type(exception)))


# NOTE: Top-level reconciler state combines this and the _ObserverLifecycle enum,
# resulting in the state graph in sculptor/docs/proposals/local_sync_lifecycle.md
class LocalSyncPathBatchSchedulerStatus(UpperCaseStrEnum):
    IDLE = auto()  # Waiting for events
    HANDLING_PENDING = auto()  # Waiting for debounce to complete
    RECONCILING = auto()
    PAUSED_ON_KNOWN_NOTICE = auto()
    PAUSED_ON_UNEXPECTED_EXCEPTION = auto()
    PAUSED_AWAITING_RESTART = auto()
    STOPPING = auto()  # external event set (TODO: idk if used actually)

    @property
    def is_active(self) -> bool:
        return self in (
            LocalSyncPathBatchSchedulerStatus.HANDLING_PENDING,
            LocalSyncPathBatchSchedulerStatus.IDLE,
            LocalSyncPathBatchSchedulerStatus.RECONCILING,
        )

    @property
    def is_paused(self) -> bool:
        return self in (
            LocalSyncPathBatchSchedulerStatus.PAUSED_ON_KNOWN_NOTICE,
            LocalSyncPathBatchSchedulerStatus.PAUSED_ON_UNEXPECTED_EXCEPTION,
            LocalSyncPathBatchSchedulerStatus.PAUSED_AWAITING_RESTART,
        )


class BatchLifecycleCallbacks(ABC):
    """correspond to the different outcomes that can occur in _reconcile_batch"""

    @abstractmethod
    def on_new_batch_pending(self, path_batch_by_tag: Mapping[str, Collection[Path]]) -> None:
        """Called when an event moves the scheduler from IDLE to HANDLING_PENDING.

        NOTE: Doesn't currently have any notice or pause info - that is only computed at batch resolution time
        """
        raise NotImplementedError()

    @abstractmethod
    def on_batch_complete(
        self,
        path_batch_by_tag: Mapping[str, Collection[Path]],
        nonpause_notices: tuple[LocalSyncNonPausingNoticeUnion, ...],
        prior_status: LocalSyncPathBatchSchedulerStatus,
    ) -> None:
        """Called when a batch of path changes is complete with no PAUSE notices."""
        raise NotImplementedError()

    @abstractmethod
    def on_handling_paused(
        self,
        pending_reconciler_tags: tuple[str, ...],
        nonpause_notices: tuple[LocalSyncNonPausingNoticeUnion, ...],
        pause_notices: tuple[LocalSyncNoticeOfPause, ...],
    ) -> None:
        """Called when handling is paused due to notices (all_notices can include NONBLOCKING notices)."""
        raise NotImplementedError()


class LocalSyncBaseWatcher(MutableModel, ABC):
    tag: str

    @property
    def dirs_to_watch(self) -> tuple[Path, ...]:
        # TODO we split watchers because of the watchmedo hack, but it is bad that we are treating both as Paths.
        # Eventually it would be nice to have ContainerPaths or even genericize message types,
        # so we could include ref content in git watch messages to skip a round-trip
        return tuple([*self.local_dirs_to_watch, *self.environment_dirs_to_watch])

    @property
    @abstractmethod
    def local_dirs_to_watch(self) -> tuple[Path, ...]:
        raise NotImplementedError()

    @property
    @abstractmethod
    def environment_dirs_to_watch(self) -> tuple[Path, ...]:
        raise NotImplementedError()

    # not exactly about watching but didn't want to make a new interface
    def get_notices(self) -> NoticeTuple:
        """notices can be blocking (PAUSE) or non-blocking (NONBLOCKING)"""
        return tuple()


# Each reconciler filters events from the stream, reports notices, and handles path changes in _reconcile_batch
#
# Subclasses are in git_branch_sync.py and mutagen_filetree_sync.py
class LocalSyncBatchReconciler(LocalSyncBaseWatcher, ABC):
    def is_relevant_subpath(self, path: Path) -> bool:
        raise NotImplementedError()

    # TODO: neither of the batched reconcilers _really_ end up caring about the specific paths that much,
    #       if we can get watcher perf gains by only tracking tags and avoiding recursive watching we prob should
    def handle_path_changes(self, relevant_paths: tuple[Path, ...], is_force_flush: bool) -> None:
        """Handle changes to the paths that are relevant, filtered and batched by LocalSyncPathBatchScheduler based on is_relevant_subpath."""
        raise NotImplementedError()


# TODO(mjr): Squish this away if LocalSyncHealthCheck is the only interface mid-term
class NoticeBasedHealthCheck(LocalSyncBaseWatcher, ABC):
    # Returns whether the flagger triggered a status change
    flag_notices_out_of_band: LazilySetCallback[[NoticeTuple], None] = Field(
        default_factory=lambda: LazilySetCallback[[NoticeTuple], None]()
    )

    @property
    def is_current_state_fatal(self) -> bool:
        return False

    def maybe_intercept_event(self, event: WatchedEventType, paths: Collection[Path]) -> tuple[NoticeTuple, bool]:
        raise NotImplementedError()


class _PathBatcher:
    """Collects paths for processing, managing thread safety and returning failed batches.

    This lets us decouple path collection from processing,
    so on_any_event can avoid waiting for the lock while we're processing a batch.

    NOTE: this means we also have to reschedule a pending batch at the end of processing if is_new_batch_ready.
    """

    def __init__(self, tags: tuple[str, ...]) -> None:
        self._lock = threading.Lock()
        self.tags = tags
        self._pending_path_batch_by_tag: dict[str, set[Path]] = {tag: set() for tag in tags}

    def update_batch(self, updates_by_subpath: Mapping[str, Collection[Path]]) -> None:
        with self._lock:
            for tag, path_batch in updates_by_subpath.items():
                self._pending_path_batch_by_tag[tag].update(path_batch)

    @property
    def pending_batch_by_tag(self) -> Mapping[str, Collection[Path]] | None:
        with self._lock:
            if not any(len(paths) > 0 for paths in self._pending_path_batch_by_tag.values()):
                return None
            return {tag: frozenset(paths) for tag, paths in self._pending_path_batch_by_tag.items()}

    @property
    def pending_tags(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(tag for tag, paths in self._pending_path_batch_by_tag.items() if len(paths) > 0)

    @property
    def is_new_batch_ready(self) -> bool:
        "Returns true if any paths have been seen since the last checkpoint (if any)"
        return len(self.pending_tags) > 0

    @contextmanager
    def checkpoint_batch_for_processing(self) -> Generator[Mapping[str, Collection[Path]], None, None]:
        with self._lock:
            batch = self._pending_path_batch_by_tag
            self._pending_path_batch_by_tag = {tag: set() for tag in self.tags}
        try:
            yield batch
        except Exception:
            # we failed to process the batch successfully - put it back for next time
            self.update_batch(batch)
            raise

    @property
    def unique_path_count(self) -> int:
        with self._lock:
            return sum(len(paths) for paths in self._pending_path_batch_by_tag.values())

    def describe_json(self) -> str:
        with self._lock:
            buffer_json = {tag: sorted(map(str, paths)) for tag, paths in self._pending_path_batch_by_tag.items()}
        return json.dumps(buffer_json, indent=4, default=str)


class LocalSyncPathBatchScheduler(FileSystemEventHandler):
    """Batches all source and target paths into a set of touched paths, debounced by debounce_seconds, for no more than max_debounce_seconds.

    Debounce timer is shared between all reconcilers!

    When the callback fires, all registered SubpathReconcilers are called with their respective filtered paths.

    NOTE: The lifecycle management ie pausing etc keeps falling down to this layer,
    so it has accrued a lot of responsibilities.
    """

    def __init__(
        self,
        threading_context: BundledThreadingContext,
        lifecycle_callbacks: BatchLifecycleCallbacks,
        subpath_reconcilers: tuple[LocalSyncBatchReconciler, ...],
        healthcheckers: tuple[NoticeBasedHealthCheck, ...],
        environment_interaction_lock: SharedExclusiveLock | None = None,
        debounce_seconds: float = DEFAULT_LOCAL_SYNC_DEBOUNCE_SECONDS,
        max_debounce_seconds: float = DEFAULT_LOCAL_SYNC_MAX_DEBOUNCE_SECONDS,
    ) -> None:
        # Validate that all reconciler tags are unique
        assert len(set(reconciler.tag for reconciler in subpath_reconcilers)) == len(subpath_reconcilers), (
            "tags must be unique"
        )
        self._stop_event = threading_context.stop_event
        self._lifecycle_callbacks = lifecycle_callbacks
        self.debounce = DebounceController(
            threading_context=threading_context,
            debounce_seconds=debounce_seconds,
            max_debounce_seconds=max_debounce_seconds,
            name="fire_reconciler_callbacks",
            callback=self._reconcile_batch,
        )

        # Used for coordination around notices, _reconcile_batch, out-of-band healthchecks -
        # basically everything that isn't a file path handling/buffering
        #
        # NOTE: currently only _reconcile_batch takes this lock for actual mutations
        self._reconciler_lock = threading.Lock()
        self._environment_interaction_lock = environment_interaction_lock

        self._batch_reconciler_by_tag: dict[str, LocalSyncBatchReconciler] = {
            reconciler.tag: reconciler for reconciler in subpath_reconcilers
        }
        self._batcher = _PathBatcher(tags=tuple(self._batch_reconciler_by_tag.keys()))

        for healthchecker in healthcheckers:
            healthchecker.flag_notices_out_of_band.set_once(self.handle_out_of_band_reconciliation_request)
        self._healthchecks = healthcheckers

        # if we're in a paused state due to an exception, the reconciler will probably spam it repeatedly.
        # this lets us de-escalate the notice to info level after the first time we see it.
        possible_notice_tags = (r.tag for r in subpath_reconcilers + self._healthchecks)
        self._last_seen_notices_by_tag: dict[str, NoticeTuple] = {tag: () for tag in possible_notice_tags}
        self._last_seen_notices_by_tag[SCHEDULER_CAUGHT_EXCEPTION] = ()
        self._scheduler_caught_exception_identifier: tuple[str, str] | None = None

        # we want to force flush all reconcilers on every build:
        # 1. On restarts our state is unknown
        # 2. On first startup we might as well, in case the setup checks missed a pause condition
        #    (or other yet-unplanned logic makes first setup state incomplete, ie non-pause notices).
        self._is_initial_flush_pending: bool = True
        self._lifecycle_callbacks.on_new_batch_pending({})
        self.debounce.trigger_callback_immediately()

    @cached_property
    def _all_notifiers_by_tag(self) -> Mapping[str, LocalSyncBaseWatcher]:
        notifiers = tuple(self._batch_reconciler_by_tag.values()) + self._healthchecks
        return {notifier.tag: notifier for notifier in notifiers}

    @property
    def _all_notifiers(self) -> tuple[LocalSyncBaseWatcher, ...]:
        return tuple(self._all_notifiers_by_tag.values())

    @property
    def _last_seen_notices(self) -> NoticeTuple:
        # this should be thread safe I think
        return tuple(generate_flattened(self._last_seen_notices_by_tag.values()))

    @property
    def is_current_state_fatal(self) -> bool:
        """This is currently how we get at environment.concurrency_group exit codes"""
        return any(healthcheck.is_current_state_fatal for healthcheck in self._healthchecks)

    @property
    def _is_reconciliation_relevant(self) -> bool:
        """Returns true if any new paths have been seen or if we've encountered a pausing notice.

        Either of these cases means we should be taking some action based on our state,
        whether it's running the sync reconcilers or checking pause state periodically.
        """
        return self._batcher.is_new_batch_ready or is_pause_necessary(self._last_seen_notices)

    def _ensure_next_batch_will_be_scheduled(self) -> bool:
        if not self._is_reconciliation_relevant:
            return False
        # if the _reconciler_lock is held, it is either in:
        # 1. _reconcile_batch - in which case we will re-check _is_batch_in_need_of_scheduling at the end of the reconciliation
        # 2. wait_for_final_batch_for_graceful_shutdown - in which case we're done anyways
        #
        # _reconcile_batch has more details on rationale here.
        if self._reconciler_lock.locked():
            return False

        self.debounce.start_or_bounce()
        return True

    @property
    def status(self) -> LocalSyncPathBatchSchedulerStatus:
        if self.is_current_state_fatal:
            return LocalSyncPathBatchSchedulerStatus.PAUSED_AWAITING_RESTART
        elif self._scheduler_caught_exception_identifier is not None:
            return LocalSyncPathBatchSchedulerStatus.PAUSED_ON_UNEXPECTED_EXCEPTION
        elif is_pause_necessary(self._last_seen_notices):
            return LocalSyncPathBatchSchedulerStatus.PAUSED_ON_KNOWN_NOTICE
        elif self._reconciler_lock.locked():
            return LocalSyncPathBatchSchedulerStatus.RECONCILING
        elif self.debounce.is_pending:
            return LocalSyncPathBatchSchedulerStatus.HANDLING_PENDING
        return LocalSyncPathBatchSchedulerStatus.IDLE

    def describe_current_state(self) -> str:
        """Describe the current state of the reconciler, including the number of paths buffered."""
        debounce = self.debounce.describe()
        notices = tuple(sorted((notice.describe() for notice in self._last_seen_notices))) or "none"
        ongoing_error = self._scheduler_caught_exception_identifier
        status = self.status.value
        state_message = (
            f"LocalSyncPathBatchScheduler ({status=}):",
            f"buffered unique paths: {self._batcher.unique_path_count}",
            f"buffer state: {self._batcher.describe_json()}",
            f"notices: {notices}" + (f", last seen tagged exception: {ongoing_error})" if ongoing_error else ""),
            debounce,
        )
        return "\n".join(state_message)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if self._stop_event.is_set() or not is_event_type_to_watch(event):
            return
        paths = extract_touched_paths(event)
        is_healthcheck = self._maybe_intercept_buffering_with_healthcheck(event, paths)
        if is_healthcheck:
            # preempt call handles pausing if necessary
            logger.trace("scheduler event intercepted by healthcheck: {}", event)
            return
        self._buffer_relevant_paths(paths)

    def _buffer_relevant_paths(self, touched_paths: Collection[Path]) -> None:
        updates_by_subpath = {
            tag: {relevant for relevant in touched_paths if reconciler.is_relevant_subpath(relevant)}
            for tag, reconciler in self._batch_reconciler_by_tag.items()
        }
        is_any_path_relevant = any(updates_by_subpath.values())
        if not is_any_path_relevant:
            return
        # if we debounce existing pause state it isn't really a new pending batch
        is_new_batch = not self._is_reconciliation_relevant
        self._batcher.update_batch(updates_by_subpath)
        was_action_taken = self._ensure_next_batch_will_be_scheduled()
        if is_new_batch and was_action_taken:
            self._lifecycle_callbacks.on_new_batch_pending(updates_by_subpath)

    def handle_out_of_band_reconciliation_request(self, notices: NoticeTuple) -> None:
        "returns whether the healthcheck triggered a status change"
        if self._stop_event.is_set() or self.is_current_state_fatal:
            return
        tag = only({n.source_tag for n in notices})
        self._last_seen_notices_by_tag[tag] = notices
        self._ensure_next_batch_will_be_scheduled()

    def _maybe_intercept_buffering_with_healthcheck(self, event: WatchedEventType, touched_paths: set[Path]) -> bool:
        for healthcheck in self._healthchecks:
            refreshed_notices, did_intercept = healthcheck.maybe_intercept_event(event, touched_paths)
            if not did_intercept:
                continue
            # TODO I think doing this outside a lock is fine...
            self._last_seen_notices_by_tag[healthcheck.tag] = refreshed_notices
            self._ensure_next_batch_will_be_scheduled()
            return True
        return False

    def _reconcile_batch(self) -> None:
        """Handle sync for the current path batch:

        1. If we can't acquire the lock we're shutting down, so return immediately.
        2. Refresh notices from all reconcilers so we can pause if there's a known issue.
        3. Take a checkpoint of the current batch and call each reconciler if their watched paths have changes.
        4. Call on_batch_complete call back to notify frontend and artifact system
        5. At the end, if we started collecting a new batch, we need to schedule it.
        6. If 3-5 fail unexpectedly, we pause, reporting the exception as a notice.

        While paused, the scheduler will retry periodically until the issue is resolved externally.

        NOTE(mjr, on 5 above):
        Before, paths that now call `_ensure_next_batch_will_be_scheduled()` would block for the lock.
        I changed this after implementing the `_periodic_heath_checker.py` because blocking in `on_any_event`
        meant we couldn't rely on healthcheck timeliness at all.
        The cost of this is that our implementation in `_ensure_next_batch_will_be_scheduled()`
        now relies on the knowledge that `_reconcile_batch` will re-check for pending batches right before letting go of the lock.
        """
        with poll_for_locks_or_give_up_on_stop_event(
            remove_none((self._reconciler_lock, self._environment_interaction_lock)),
            self._stop_event,
            lock_acquisition_timeout=2.5,
        ) as is_lock_acquired:
            # TODO: Add test for being able to shutdown session while "racing" with snapshot lock or whatever
            # We _only_ give up on stop_event - essentially we're saying "guarantee we're not snapshotting" while reconciling so we can kill the session safely
            if not is_lock_acquired:
                return

            if self.is_current_state_fatal:
                self._handle_pausing()
                return

            prior_status = self.status
            debug_phase = "known_notice_check"
            tag = SCHEDULER_CAUGHT_EXCEPTION
            try:
                # pause if the reconcilers _know_ they should pause
                self.refresh_notices_by_tag()
                _last_seen_notices = self._last_seen_notices

                # use this helper for type guard
                if is_pause_necessary(_last_seen_notices):
                    self._handle_pausing()
                    return
                _last_seen_notices = cast(tuple[LocalSyncNonPausingNoticeUnion, ...], _last_seen_notices)

                if self._stop_event.is_set():
                    return

                is_force_flush = self._is_initial_flush_pending or prior_status.is_paused
                if not (self._batcher.is_new_batch_ready or is_force_flush):
                    return

                # once we enter this block we don't want to acknowledge the _stop_event so that file and git syncs happen in unison.
                # otherwise fs and git history will get out of sync.
                debug_phase = "batch_reconciliation"
                if is_force_flush:
                    logger.debug("LOCAL_SYNC force flush ({}, init={})", prior_status, self._is_initial_flush_pending)
                with self._batcher.checkpoint_batch_for_processing() as batch:
                    for reconciler_tag, path_batch in batch.items():
                        tag = reconciler_tag
                        debug_phase = reconciler_tag
                        reconciler = self._batch_reconciler_by_tag[reconciler_tag]
                        # if we were paused, maybe we missed events, so we want to let the handler know even if the batch is empty
                        if (not is_force_flush) and len(path_batch) == 0:
                            continue
                        debounced_by = f"{self.debounce.total_elapsed_seconds:.3f}"
                        logger.trace(
                            "{} handling {} paths (debounced by {}s)", reconciler.tag, len(path_batch), debounced_by
                        )
                        with log_runtime(f"LOCAL_SYNC.{reconciler.tag}.handle_path_changes"):
                            reconciler.handle_path_changes(tuple(path_batch), is_force_flush=is_force_flush)
                # we've successfully reconciled, thus this was outdated if present
                self._scheduler_caught_exception_identifier = None
                self._is_initial_flush_pending = False

                debug_phase = "on_batch_complete"
                self._lifecycle_callbacks.on_batch_complete(batch, _last_seen_notices, prior_status)
                debug_phase = "cleanup"
                self._scheduler_caught_exception_identifier = None

                if self._is_reconciliation_relevant:
                    batch = self._batcher.pending_batch_by_tag
                    if batch is not None:
                        # We just completed a batch, but new pending paths arrived during handling.
                        # this means we're in a frequently active sync situation.
                        #
                        # Frontend should handle smoothing the UI state of the back-to-back messages on its own.
                        self._lifecycle_callbacks.on_new_batch_pending(batch)
                    self.debounce.start_or_bounce()

            except NewNoticesInSyncHandlingError as e:
                self._last_seen_notices_by_tag[tag] = e.notices
                self._handle_pausing()
                return
            except Exception as e:
                # pause if something unexpected happens
                self._handle_exception_by_pausing(debug_phase, e)
                return

    def refresh_notices_by_tag(self) -> None:
        """Refresh notices from sub reconcilers and healthcheck.

        This intentionally excludes the SCHEDULER_CAUGHT_EXCEPTION tag, because it may be a transient error in handling.
        We need to re-try handling to see if it persists.
        """
        self._last_seen_notices_by_tag = {
            tag: reconciler.get_notices() for tag, reconciler in self._all_notifiers_by_tag.items()
        }

    def _handle_exception_by_pausing(self, source_tag: str, exception: Exception) -> None:
        """This is a bit leaky and counter-intuitive but we want to pause even in unknown error states.

        Really we want pause states to be captured by get_notices_without_effecting_state,
        but if the reconciler raises an exception we haven't handled properly,
        we still probably want to pause.
        """
        assert self._reconciler_lock.locked(), "only for use in locks"
        new_notice = LocalSyncNoticeOfPause(
            source_tag=source_tag,
            reason=truncate_string(f"{source_tag} processing failure: {exception}", 300),
        )
        # ensure we only ever have a singleton caught by the scheduler
        self._last_seen_notices_by_tag[SCHEDULER_CAUGHT_EXCEPTION] = (new_notice,)
        self._handle_pausing()
        identifier = _unhandled_exception_issue_identifier(source_tag, exception)
        if self._scheduler_caught_exception_identifier == identifier:
            return

        self._scheduler_caught_exception_identifier = identifier
        priority = ExceptionPriority.LOW_PRIORITY
        log_exception(
            exception,
            "local sync paused due to unexpected exception: {reason}",
            priority,
            reason=new_notice.reason,
        )

    def _handle_pausing(self) -> None:
        assert self._reconciler_lock.locked(), "only for use in locks"

        pauses, nonpauses = separate_pause_notices(self._last_seen_notices)
        self._lifecycle_callbacks.on_handling_paused(
            pending_reconciler_tags=self._batcher.pending_tags,
            nonpause_notices=nonpauses,
            pause_notices=pauses,
        )

        # No reason to debounce if the whole thing has to get hard-rebooted (agent environment restart or crash or docker killed, etc)
        # NOTE: We may ignore other non-fatal notices in the UI, but leaving that as a rendering choice only
        if not self.is_current_state_fatal:
            self.debounce.restart()

    def wait_for_final_batch_for_graceful_shutdown(self, timeout: float) -> bool:
        """
        This means if we acquire the lock, here, we can be confident this scheduler will not schedule or handle any new changes.
        """
        # only describing debounce to avoid lock here as something might be horribly wrong / deadlocky
        assert self._stop_event.is_set(), f"parent context should have sent stop event {self.debounce.describe()=}"
        is_lock_acquired = self._reconciler_lock.acquire(blocking=True, timeout=timeout)
        try:
            assert is_lock_acquired, f"failed to acquire lock within {timeout}s: {self.debounce=}"
            self._reconciler_lock.release()
            return True
        except AssertionError as e:
            log_exception(
                e,
                "wait_for_final_batch_for_graceful_shutdown timeout after {timeout}s",
                ExceptionPriority.HIGH_PRIORITY,
                timeout=timeout,
            )
            return False

    @property
    def all_required_paths(self) -> tuple[Path, ...]:
        """Get all paths that are required by the reconcilers without any simplification."""
        return tuple(generate_flattened(reconciler.dirs_to_watch for reconciler in self._all_notifiers))

    @property
    def all_required_local_paths(self) -> tuple[Path, ...]:
        """Get all local paths that are required by the reconcilers without any simplification."""
        return tuple(generate_flattened(reconciler.local_dirs_to_watch for reconciler in self._all_notifiers))

    @property
    def all_required_environment_paths(self) -> tuple[Path, ...]:
        """Get all in-container paths that are required by the reconcilers without any simplification."""
        return tuple(generate_flattened(reconciler.environment_dirs_to_watch for reconciler in self._all_notifiers))

    @property
    def top_level_local_dirs_to_register(self) -> tuple[Path, ...]:
        """top-level directories to register for the local observer"""
        return simplify_root_watcher_paths(self.all_required_local_paths)

    @property
    def top_level_environment_dirs_to_register(self) -> tuple[str, ...]:
        """top-level directories to register with hacked watchmedo script"""
        return tuple(str(p) for p in simplify_root_watcher_paths(self.all_required_environment_paths))


def register_batch_scheduler_with_observer(
    observer: SlightlySaferObserver, reconciler: LocalSyncPathBatchScheduler
) -> None:
    logger.debug(
        "Registering batched path change reconciler for paths {} (all_required_paths: {})",
        reconciler.top_level_local_dirs_to_register,
        reconciler.all_required_paths,
    )
    for path in reconciler.top_level_local_dirs_to_register:
        # note: this is because watchdog does this in fsevents
        # https://github.com/gorakhargosh/watchdog/blob/1d323bafe80cbbdee3f8a6c2dea9f7e6421190a7/src/watchdog/observers/fsevents.py#L82
        assert path == path.resolve(), f"watch path must be fully absolute/resolved, but: {path=} != {path.resolve()=}"
        observer.schedule(reconciler, str(path), recursive=True, event_filter=list(EVENT_TYPES_TO_WATCH))
