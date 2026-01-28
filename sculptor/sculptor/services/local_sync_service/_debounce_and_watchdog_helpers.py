import datetime
import threading
import time
from contextlib import ExitStack
from contextlib import contextmanager
from enum import auto
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Final
from typing import Generator
from typing import Iterable
from typing import Protocol
from typing import Sequence
from typing import TYPE_CHECKING
from typing import TypeVar

from loguru import logger
from pydantic import PrivateAttr
from watchdog.observers import Observer
from watchdog.observers.api import DEFAULT_OBSERVER_TIMEOUT
from watchdog.observers.api import EventEmitter

from imbue_core.async_monkey_patches import log_exception
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.upper_case_str_enum import UpperCaseStrEnum
from sculptor.utils.shared_exclusive_lock import SharedExclusiveLock

if TYPE_CHECKING:
    # just for type checking b/c Observer is a runtime-resolved class reference
    from watchdog.observers.polling import PollingObserver as Observer


# was getting some issue due to threading.Lock not being a type or something
class Acquirable(Protocol):
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...
    def release(self) -> None: ...


DEFAULT_LOCAL_SYNC_DEBOUNCE_SECONDS: Final = 0.25
DEFAULT_LOCAL_SYNC_MAX_DEBOUNCE_SECONDS: Final = 2.0
DEFAULT_LOCAL_SYNC_PAUSE_LOG_: Final = 2.0

T = TypeVar("T")


@contextmanager
def acquire_lock_or_timeout(lock: Acquirable, timeout: float) -> Generator[bool, None, None]:
    is_acquired = lock.acquire(timeout=timeout)
    try:
        yield is_acquired
    finally:
        if is_acquired:
            lock.release()


@contextmanager
def _every_conditional_context_successful(contexts: Iterable[ContextManager[bool]]) -> Generator[bool, None, None]:
    with ExitStack() as stack:
        for ctx in contexts:
            is_acquired = stack.enter_context(ctx)
            if not is_acquired:
                yield False
                return
        yield True


def acquire_locks_or_timeout(
    locks: Sequence[Acquirable | SharedExclusiveLock], timeout: float
) -> ContextManager[bool]:
    contexts = []
    for lock in locks:
        if isinstance(lock, SharedExclusiveLock):
            contexts.append(lock.shared_lock_or_timeout(timeout))
        else:
            contexts.append(acquire_lock_or_timeout(lock, timeout))
    return _every_conditional_context_successful(contexts)


@contextmanager
def poll_for_locks_or_give_up_on_stop_event(
    locks: Sequence[Acquirable | SharedExclusiveLock],
    stop_event: threading.Event,
    lock_acquisition_timeout: float,
) -> Generator[bool, None, None]:
    """Try to acquire the lock, but give up if the stop_event is set or we time out.

    Returns is_lock_acquired: bool
    """
    while not stop_event.is_set():
        with acquire_locks_or_timeout(locks, lock_acquisition_timeout) as is_acquired:
            if is_acquired:
                yield True
                return
    yield False


class BundledThreadingContext:
    "just for top-down control at stop time"

    def __init__(self, stop_event: threading.Event) -> None:
        self.stop_event: threading.Event = stop_event
        self.timers: dict[str, threading.Timer] = {}
        self._lock: threading.RLock = threading.RLock()

    def register_and_start_timer(self, timer: threading.Timer) -> bool:
        assert timer.name is not None, "Timer must have a name to be registered"
        with self._lock:
            if self.stop_event.is_set():
                return False
            self.timers[timer.name] = timer
            # this way we don't race with stop_all_timers ever
            timer.start()
        return True

    def unregister_timer(self, timer: threading.Timer) -> None:
        assert timer.name is not None, "Timer must have a name to be registered"
        with self._lock:
            self.timers.pop(timer.name, None)

    def stop_all_timers(self) -> None:
        with self._lock:
            for timer in self.timers.values():
                timer.cancel()
            self.timers.clear()


def elapsed_seconds(start_time_monotonic_sec: float | None) -> float:
    """Calculate elapsed seconds from a start time."""
    if start_time_monotonic_sec is None:
        return 0.0
    return time.monotonic() - start_time_monotonic_sec


class DebounceController(MutableModel):
    threading_context: BundledThreadingContext
    name: str
    callback: Callable[[], Any]
    debounce_seconds: float
    max_debounce_seconds: float

    _first_debounced_timestamp: float | None = None
    _latest_debounced_timestamp: float | None = None
    _bounces: int = 0
    _timer: threading.Timer | None = None
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def total_elapsed_seconds(self) -> float:
        return elapsed_seconds(self._first_debounced_timestamp)

    @property
    def elapsed_since_last_debounce_seconds(self) -> float:
        return elapsed_seconds(self._latest_debounced_timestamp)

    @property
    def is_pending(self) -> bool:
        return self._timer is not None and self._timer.is_alive() and not self._timer.finished.is_set()

    @property
    def is_max_debounce_exceeded(self) -> bool:
        return self.total_elapsed_seconds > self.max_debounce_seconds

    def _new_timer(self, debounce_seconds: float) -> threading.Timer:
        "separated for easier mocking in scenario testing"
        timer = threading.Timer(debounce_seconds, self.callback)
        timer.name = self.name
        return timer

    def _build_and_start_new_timer_unless_stopping(self, debounce_seconds: float) -> threading.Timer | None:
        timer = self._new_timer(debounce_seconds)
        is_started = self.threading_context.register_and_start_timer(timer)
        if not is_started:
            logger.trace("Skipping debounce: {}.new_timer() after stop_event is set", self.name)
            return None

        return timer

    def _clear_timer(self) -> None:
        timer = self._timer
        if timer is None:
            return
        self.threading_context.unregister_timer(timer)
        timer.cancel()
        self._timer = None

    def clear(self) -> None:
        with self._lock:
            self._first_debounced_timestamp = None
            self._latest_debounced_timestamp = None
            self._bounces = 0
            self._clear_timer()

    def _bounce(self) -> None:
        self._bounces += 1
        self._clear_timer()
        self._timer = self._build_and_start_new_timer_unless_stopping(self.debounce_seconds)

    def trigger_callback_immediately(self) -> None:
        self.clear()
        thousandth_of_a_second_from_now = 0.001  # just defensive in case 0.0 has some weird interaction
        self._timer = self._build_and_start_new_timer_unless_stopping(thousandth_of_a_second_from_now)

    def start_or_bounce(self) -> None:
        timestamp = time.monotonic()
        if not self.is_pending:
            return self.restart()
        with self._lock:
            if self._first_debounced_timestamp is None:
                self._first_debounced_timestamp = timestamp
            elif self.is_max_debounce_exceeded:
                logger.trace(
                    "skipping debounce: {}.is_max_debounce_exceeded after max_debounce_seconds={} (currently {:.3f}s)",
                    self.name,
                    self.max_debounce_seconds,
                    self.total_elapsed_seconds,
                )
                return
            self._latest_debounced_timestamp = timestamp
            self._bounce()

    def restart(self) -> None:
        with self._lock:
            self._first_debounced_timestamp = time.monotonic()
            self._latest_debounced_timestamp = self._first_debounced_timestamp
            self._bounce()

    def describe(self) -> str:
        fields = {
            "name": self.name,
            "state": "pending" if self.is_pending else "externally_cancelled" if self._timer else "clear",
            "total_elapsed_seconds": f"{self.total_elapsed_seconds:.4f}s",
            "elapsed_since_last_debounce_seconds": f"{self.elapsed_since_last_debounce_seconds:.4f}s",
            "bounces": self._bounces,
        }
        return "DebounceController({})".format(", ".join(f"{k}={v}" for k, v in fields.items()))


class ObserverLifecycle(UpperCaseStrEnum):
    INITIALIZED = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()

    def is_step_exactly_after(self, previous_step: "ObserverLifecycle") -> bool:
        members = tuple(ObserverLifecycle.__members__.values())
        index_after_previous = members.index(previous_step) + 1
        if index_after_previous >= len(members):
            return False
        return self == members[index_after_previous]


class SlightlySaferObserver(Observer):  # type: ignore
    """Watchdog observer with some exception handling and lifecycle guarantees.

    NOTE: This observer isn't deeply involved in the underlying pause/lifecycle mechanism -
    instead that is all handled by the LocalSyncPathBatchScheduler.
    """

    def __init__(self, name: str, *, timeout: float = DEFAULT_OBSERVER_TIMEOUT) -> None:
        super().__init__(timeout=timeout)
        logger.debug("watchdog selected observer class: {}", type(self).__base__)
        self.name: str = name
        self._lifecycle: ObserverLifecycle = ObserverLifecycle.INITIALIZED
        self._start_time: datetime.datetime | None = None
        self._stop_time: datetime.datetime | None = None
        self.threading_context: BundledThreadingContext = BundledThreadingContext(stop_event=self.stopped_event)

    # TODO: IDK if these locks are necessary
    @property
    def lifecycle(self) -> ObserverLifecycle:
        return self._lifecycle

    @property
    def start_time(self) -> datetime.datetime | None:
        return self._start_time

    @property
    def stop_time(self) -> datetime.datetime | None:
        return self._stop_time

    def _is_transition_valid(self, desired_state: ObserverLifecycle) -> bool:
        if desired_state.is_step_exactly_after(self._lifecycle):
            return True
        is_skipping_to_stopped = (
            self._lifecycle == ObserverLifecycle.INITIALIZED and desired_state == ObserverLifecycle.STOPPED
        )
        if is_skipping_to_stopped:
            return True
        return False

    def is_running(self) -> bool:
        return self._lifecycle == ObserverLifecycle.RUNNING

    # TODO when did is_failure_fatal ever do anything? Leaving it in for now b/c maybe it should explode but don't want to tamper too much atm
    def _attempt_lifecycle_transition(self, desired_state: ObserverLifecycle, is_failure_fatal: bool) -> bool:
        """Attempt to transition to the desired state. Returns is_valid_and_successful."""
        if not self._is_transition_valid(desired_state):
            logger.debug("Invalid lifecycle transition attempted from {} to {}", self._lifecycle, desired_state)
            if self._lifecycle == ObserverLifecycle.STOPPING:
                logger.debug(
                    "Multiple .stop() calls on {}! This is an indication of Observer mishandling/racing", self.name
                )
            return False
        with self._lock:
            self._lifecycle = desired_state

            if desired_state == ObserverLifecycle.STOPPED:
                self._stop_time = datetime.datetime.now()
            elif desired_state == ObserverLifecycle.RUNNING:
                self._start_time = datetime.datetime.now()
        return True

    def stop(self) -> None:
        self._attempt_lifecycle_transition(ObserverLifecycle.STOPPING, is_failure_fatal=False)
        self._stopped_event.set()
        self.threading_context.stop_all_timers()
        super().stop()
        self._attempt_lifecycle_transition(ObserverLifecycle.STOPPED, is_failure_fatal=True)

    def ensure_stopped(self, source: str) -> None:
        with self._lock:
            if not self._is_transition_valid(ObserverLifecycle.STOPPING):
                logger.trace("Ignored repeat stop request for {} from {} ({})", self.name, source, self._lifecycle)
                return
            logger.trace("Stopping {} at request from source {} ({})", self.name, source, self._lifecycle)
            self.stop()

    def run(self) -> None:
        self._attempt_lifecycle_transition(ObserverLifecycle.RUNNING, is_failure_fatal=True)
        try:
            super().run()
        except Exception as e:
            log_exception(e, "Exception in {name}.run()", name=self.name)
        try:
            self.ensure_stopped(source=f"{self.name}.run")
        except Exception as e:
            log_exception(e, "Exception in {name}.ensure_stopped() called in run()", name=self.name)

    def _add_emitter(self, emitter: EventEmitter) -> None:
        emitter.name = f"watchdog_emitter_{len(self._emitters)}"
        super()._add_emitter(emitter)
