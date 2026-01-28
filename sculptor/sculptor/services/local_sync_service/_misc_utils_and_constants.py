import threading
from contextlib import ExitStack
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from time import monotonic as _monotonic
from time import time as _time
from typing import Callable
from typing import ContextManager
from typing import Final
from typing import Generator
from typing import Generic
from typing import Iterable
from typing import ParamSpec
from typing import Sequence
from typing import TypeGuard
from typing import TypeVar

from loguru import logger
from pydantic import Field
from pydantic import PrivateAttr
from watchdog.events import DirCreatedEvent
from watchdog.events import DirDeletedEvent
from watchdog.events import DirModifiedEvent
from watchdog.events import DirMovedEvent
from watchdog.events import FileClosedEvent
from watchdog.events import FileClosedNoWriteEvent
from watchdog.events import FileCreatedEvent
from watchdog.events import FileDeletedEvent
from watchdog.events import FileModifiedEvent
from watchdog.events import FileMovedEvent
from watchdog.events import FileOpenedEvent
from watchdog.events import FileSystemEvent
from watchdog.events import FileSystemMovedEvent

from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.concurrency_group import ConcurrencyGroupState
from imbue_core.constants import ExceptionPriority
from imbue_core.pydantic_serialization import MutableModel
from sculptor.interfaces.agents.agent import LocalSyncNonPausingNoticeUnion
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion

P = ParamSpec("P")
ReturnT = TypeVar("ReturnT")

NoticeTuple = tuple[LocalSyncNoticeUnion, ...]


EVENT_TYPES_TO_IGNORE: Final = (FileOpenedEvent, FileClosedEvent, FileClosedNoWriteEvent)
EVENT_TYPES_TO_WATCH: Final = (
    FileSystemMovedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileCreatedEvent,
    FileMovedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirCreatedEvent,
    DirMovedEvent,
)

WatchedEventType = (
    FileSystemMovedEvent
    | FileDeletedEvent
    | FileModifiedEvent
    | FileCreatedEvent
    | FileMovedEvent
    | DirDeletedEvent
    | DirModifiedEvent
    | DirCreatedEvent
    | DirMovedEvent
)


def is_event_type_to_watch(event: FileSystemEvent) -> TypeGuard[WatchedEventType]:
    # note: not is a bit cheeky here, leveraging fact we know the api
    return not isinstance(event, EVENT_TYPES_TO_IGNORE)


def is_path_under_any(query_path: Path, search_paths: Sequence[Path]) -> bool:
    query_path = query_path
    return any(query_path.is_relative_to(ignore_path) for ignore_path in search_paths)


def is_any_path_under(query_paths: Iterable[Path], root_path: Path) -> bool:
    return any(query_path.is_relative_to(root_path) for query_path in query_paths)


def extract_touched_paths(event: FileSystemEvent) -> set[Path]:
    touched = {Path(str(event.src_path))}
    if hasattr(event, "dest_path") and event.dest_path:
        touched.add(Path(str(event.dest_path)))
    return touched


def simplify_root_watcher_paths(paths_to_watch: Sequence[Path]) -> tuple[Path, ...]:
    simplified_paths: list[Path] = []
    shortest_to_longest = sorted(paths_to_watch, key=lambda path: len(path.parts))
    for path in shortest_to_longest:
        if is_path_under_any(path, simplified_paths):
            continue
        simplified_paths.append(path)
    return tuple(simplified_paths)


def is_pause_necessary(sync_notices: NoticeTuple) -> bool:
    return any(isinstance(notice, LocalSyncNoticeOfPause) for notice in sync_notices)


def separate_pause_notices(
    sync_notices: NoticeTuple,
) -> tuple[tuple[LocalSyncNoticeOfPause, ...], tuple[LocalSyncNonPausingNoticeUnion, ...]]:
    pauses: list[LocalSyncNoticeOfPause] = []
    nonpauses: list[LocalSyncNonPausingNoticeUnion] = []
    for notice in sync_notices:
        if isinstance(notice, LocalSyncNoticeOfPause):
            pauses.append(notice)
        else:
            nonpauses.append(notice)
    return tuple(pauses), tuple(nonpauses)


def join_background_threads_and_log_exceptions(threads: Iterable[threading.Thread], join_timeout: float) -> None:
    for thread in threads:
        try:
            thread.join(timeout=join_timeout)
        except Exception as e:
            # ObservableThreads
            log_exception(
                e,
                "LOCAL_SYNC: {thread_name} failed to join cleanly",
                thread_name=thread.name,
                priority=ExceptionPriority.LOW_PRIORITY,
            )
            continue

        if thread.is_alive():
            logger.error("LOCAL_SYNC: {}.is_alive()=False", thread.name)
        else:
            logger.debug("LOCAL_SYNC: {} joined cleanly", thread.name)


# sorry maciek
class LazilySetCallback(Generic[P, ReturnT]):
    _callback: Callable[P, ReturnT] | None = None

    def set_once(self, callback: Callable[P, ReturnT]) -> None:
        assert self._callback is None, f"{self} can only be set once"
        self._callback = callback

    @cached_property
    def _cached_callback(self) -> Callable[P, ReturnT]:
        assert self._callback is not None, f"must call LazilySetCallback.set_once() before accessing {self}"
        return self._callback

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> ReturnT:
        return self._cached_callback(*args, **kwargs)


class ConcurrencyGroupController(MutableModel):
    _stack: ExitStack = PrivateAttr(default_factory=ExitStack)
    concurrency_group: ConcurrencyGroup = Field()

    def _start(self) -> None:
        self._stack.enter_context(self.concurrency_group)

    @contextmanager
    def close_on_failure(self) -> Generator[None, None, None]:
        try:
            yield
        except Exception:
            self.stop()
            raise

    def start_but_close_on_failure(self) -> ContextManager[None]:
        self._start()
        return self.close_on_failure()

    @property
    def active_group(self) -> ConcurrencyGroup:
        state = self.concurrency_group.state
        assert state == ConcurrencyGroupState.ACTIVE, f"Concurrency group {self.name} must be active here, not {state}"
        return self.concurrency_group

    def stop(self) -> None:
        try:
            self.concurrency_group.shutdown()
        finally:
            self._stack.close()

    @property
    def name(self) -> str:
        return self.concurrency_group.name

    def make_controlled_child(self, name: str) -> "ConcurrencyGroupController":
        child = self.concurrency_group.make_concurrency_group(name=name)
        return ConcurrencyGroupController(concurrency_group=child)


class MonotonicEpochClock:
    def __init__(self):
        self.init_time = _time()
        self.init_monotonic = _monotonic()

    def __call__(self) -> float:
        return self.init_time + (_monotonic() - self.init_monotonic)


monotonic_epoch_time: Final = MonotonicEpochClock()
