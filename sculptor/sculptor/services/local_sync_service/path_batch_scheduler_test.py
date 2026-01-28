import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable
from typing import Collection
from typing import Generator
from typing import Mapping

import pytest
import sentry_sdk
from pydantic import Field
from watchdog.events import FileCreatedEvent
from watchdog.events import FileModifiedEvent
from watchdog.events import FileMovedEvent

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.pydantic_serialization import MutableModel
from sculptor.interfaces.agents.agent import LocalSyncNonPausingNoticeUnion
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import BundledThreadingContext
from sculptor.services.local_sync_service._misc_utils_and_constants import extract_touched_paths
from sculptor.services.local_sync_service._misc_utils_and_constants import is_path_under_any
from sculptor.services.local_sync_service._misc_utils_and_constants import simplify_root_watcher_paths
from sculptor.services.local_sync_service.local_sync_session import SlightlySaferObserver
from sculptor.services.local_sync_service.path_batch_scheduler import BatchLifecycleCallbacks
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncBatchReconciler
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncPathBatchScheduler
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncPathBatchSchedulerStatus
from sculptor.services.local_sync_service.path_batch_scheduler import register_batch_scheduler_with_observer


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir).resolve()


@pytest.fixture
def task_id() -> TaskID:
    """Create a test task ID."""
    return TaskID()


class PathRecordingReconciler(LocalSyncBatchReconciler):
    """A flexible reconciler that records paths and can be configured for different test scenarios."""

    watch_dirs: tuple[Path, ...]
    path_filter: Callable[[Path], bool] = Field(default=lambda path: True)
    should_fail: bool = False
    recorded_paths: list[tuple[Path, ...]] = Field(default_factory=list)
    notices: list[LocalSyncNoticeUnion] = Field(default_factory=list)

    is_initial_flush_complete: bool = False

    def get_notices(self) -> tuple[LocalSyncNoticeUnion, ...]:
        return tuple(self.notices)

    def is_relevant_subpath(self, path: Path) -> bool:
        return self.path_filter(path)  # pyre-ignore[19]

    @property
    def local_dirs_to_watch(self) -> tuple[Path, ...]:
        return self.watch_dirs

    @property
    def environment_dirs_to_watch(self) -> tuple[Path, ...]:
        return ()

    def handle_path_changes(self, relevant_paths: tuple[Path, ...], is_force_flush: bool) -> None:
        if self.should_fail:
            raise RuntimeError("Reconciler failed")

        self.recorded_paths.append(relevant_paths)


class CallbackRecorder(MutableModel, BatchLifecycleCallbacks):
    on_batch_update_calls: list[tuple[Mapping[str, Collection[Path]], tuple[LocalSyncNonPausingNoticeUnion, ...]]] = (
        Field(default_factory=list)
    )
    on_new_batch_pending_calls: list[tuple[Mapping[str, Collection[Path]], ...]] = Field(default_factory=list)
    on_handling_paused_calls: list[tuple[tuple[str, ...], tuple[LocalSyncNoticeOfPause, ...]]] = Field(
        default_factory=list
    )

    def on_new_batch_pending(self, path_batch_by_tag: Mapping[str, Collection[Path]]) -> None:
        self.on_new_batch_pending_calls.append((path_batch_by_tag,))

    def on_batch_complete(
        self,
        path_batch_by_tag: Mapping[str, Collection[Path]],
        nonpause_notices: tuple[LocalSyncNonPausingNoticeUnion, ...],
        prior_status: LocalSyncPathBatchSchedulerStatus,
    ) -> None:
        self.on_batch_update_calls.append((path_batch_by_tag, nonpause_notices))

    def on_handling_paused(
        self,
        pending_reconciler_tags: tuple[str, ...],
        nonpause_notices: tuple[LocalSyncNonPausingNoticeUnion, ...],
        pause_notices: tuple[LocalSyncNoticeOfPause, ...],
    ) -> None:
        self.on_handling_paused_calls.append((pending_reconciler_tags, pause_notices))


def build_batch_scheduler(
    subpath_reconcilers: tuple[LocalSyncBatchReconciler, ...],
    debounce_seconds: float = 0.1,
    max_debounce_seconds: float = 5.0,
    threading_context: BundledThreadingContext | None = None,
) -> LocalSyncPathBatchScheduler:
    return LocalSyncPathBatchScheduler(
        threading_context=threading_context or BundledThreadingContext(stop_event=threading.Event()),
        lifecycle_callbacks=CallbackRecorder(),
        subpath_reconcilers=subpath_reconcilers,
        debounce_seconds=debounce_seconds,
        max_debounce_seconds=max_debounce_seconds,
        healthcheckers=(),
    )


@contextmanager
def batch_scheduler_context(
    subpath_reconcilers: tuple[LocalSyncBatchReconciler, ...],
    debounce_seconds: float = 0.1,
    max_debounce_seconds: float = 5.0,
    threading_context: BundledThreadingContext | None = None,
) -> Generator[LocalSyncPathBatchScheduler, None, None]:
    """Context manager that ensures batch scheduler is properly cleaned up.

    This prevents leaked schedulers when tests fail, ensuring the stop event
    is always set even if an exception occurs.
    """
    scheduler = build_batch_scheduler(
        subpath_reconcilers=subpath_reconcilers,
        debounce_seconds=debounce_seconds,
        max_debounce_seconds=max_debounce_seconds,
        threading_context=threading_context,
    )
    start_time = time.time()
    while scheduler._is_initial_flush_pending and not scheduler.status.is_paused:
        if time.time() - start_time > 5.0:
            raise TimeoutError("Batch scheduler did not finish initial flush in time")
        time.sleep(0.01)
    try:
        yield scheduler
    finally:
        scheduler._stop_event.set()


def build_successful_reconciler(temp_dir: Path, tag: str) -> PathRecordingReconciler:
    return PathRecordingReconciler(
        tag=tag, watch_dirs=(temp_dir,), path_filter=lambda path: str(path).endswith("success.txt")
    )


@pytest.fixture
def failing_reconciler(temp_dir: Path) -> PathRecordingReconciler:
    return PathRecordingReconciler(
        tag="failing_reconciler",
        watch_dirs=(temp_dir,),
        should_fail=True,
        path_filter=lambda path: "fail" in str(path),
    )


@pytest.fixture
def noticeful_reconciler(temp_dir: Path) -> PathRecordingReconciler:
    return PathRecordingReconciler(
        tag="noticeful_reconciler",
        watch_dirs=(temp_dir,),
        notices=[LocalSyncNoticeOfPause(source_tag="noticeful_reconciler", reason="Test notice")],
    )


# Helper function tests
def test_is_path_under_any(temp_dir: Path) -> None:
    """Test _is_path_under_any with various path relationships."""
    # Create test directory structure
    subdir = temp_dir / "subdir"
    subdir.mkdir()
    nested_file = subdir / "file.txt"
    nested_file.write_text("test")
    other_dir = temp_dir / "other"
    other_dir.mkdir()

    # Test that a nested file is under the parent directory
    assert is_path_under_any(nested_file, [temp_dir]), f"Expected {nested_file} to be under {temp_dir}"

    # Test that a file is not under unrelated directories
    assert not is_path_under_any(nested_file, [other_dir]), f"Expected {nested_file} not to be under {other_dir}"

    # Test with multiple ignore paths
    dir1 = temp_dir / "dir1"
    dir2 = temp_dir / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    file_in_dir1 = dir1 / "file.txt"
    file_in_dir2 = dir2 / "file.txt"
    file_in_dir1.write_text("test")
    file_in_dir2.write_text("test")

    ignore_paths = [dir1, dir2]

    assert is_path_under_any(file_in_dir1, ignore_paths), f"Expected {file_in_dir1} to be under {dir1} or {dir2}"
    assert is_path_under_any(file_in_dir2, ignore_paths), f"Expected {file_in_dir2} to be under {dir1} or {dir2}"

    # Test with a file not in any ignored directory
    other_file = temp_dir / "other.txt"
    other_file.write_text("test")
    assert not is_path_under_any(other_file, ignore_paths), f"Expected {other_file} not to be under {dir1} or {dir2}"


def test_extract_touched_paths() -> None:
    """Test _extract_touched_paths with various event types."""
    # Test with a basic file modified event
    event = FileModifiedEvent("/test/path.txt")
    touched = extract_touched_paths(event)
    assert touched == {Path("/test/path.txt")}

    # Test with a move event that has both src and dest
    event = FileMovedEvent("/test/old.txt", "/test/new.txt")
    touched = extract_touched_paths(event)
    assert touched == {Path("/test/old.txt"), Path("/test/new.txt")}

    # Test with a create event
    event = FileCreatedEvent("/test/new.txt")
    touched = extract_touched_paths(event)
    assert touched == {Path("/test/new.txt")}


def test_simplify_root_watcher_paths(temp_dir: Path) -> None:
    """Test _simplify_root_watcher_paths with various path relationships."""
    # Create nested directory structure
    subdir = temp_dir / "subdir"
    subdir.mkdir()
    nested_dir = subdir / "nested"
    nested_dir.mkdir()

    # Test that nested paths are simplified
    paths = [temp_dir, subdir, nested_dir]
    simplified = simplify_root_watcher_paths(paths)
    assert simplified == (temp_dir,)

    # Test when paths don't overlap
    dir1 = temp_dir / "dir1"
    dir2 = temp_dir / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    paths = [dir1, dir2]
    simplified = simplify_root_watcher_paths(paths)
    assert set(simplified) == {dir1, dir2}

    # Test with partial overlaps
    nested_in_dir1 = dir1 / "nested"
    nested_in_dir1.mkdir()

    paths = [dir1, dir2, nested_in_dir1]
    simplified = simplify_root_watcher_paths(paths)
    assert set(simplified) == {dir1, dir2}


def test_batch_scheduler_path_routing(temp_dir: Path) -> None:
    """Test that LocalSyncPathBatchScheduler correctly routes paths to reconcilers."""
    reconciler1 = PathRecordingReconciler(
        tag="reconciler1", watch_dirs=(temp_dir,), path_filter=lambda path: str(path).endswith("reconciler1.txt")
    )
    reconciler2 = PathRecordingReconciler(
        tag="reconciler2", watch_dirs=(temp_dir,), path_filter=lambda path: str(path).endswith("reconciler2.txt")
    )
    with batch_scheduler_context((reconciler1, reconciler2), debounce_seconds=0.01) as batch_scheduler:
        file1, file2, file3 = (temp_dir / "reconciler1.txt", temp_dir / "reconciler2.txt", temp_dir / "other.txt")
        for file in (file1, file2, file3):
            file.touch()
            file.write_text("test")
            batch_scheduler.on_any_event(FileModifiedEvent(str(file)))

        # Wait for debounce
        time.sleep(0.1)

        # Check that reconcilers were called with correct paths
        assert len(reconciler1.recorded_paths) == 2, (
            f"Expected reconciler1 to record 2 calls (initial flush, then 1 path), got {reconciler1.recorded_paths}"
        )
        assert len(reconciler2.recorded_paths) == 2, (
            f"Expected reconciler2 to record 2 calls (initial flush, then 1 path), got {reconciler2.recorded_paths}"
        )

        assert file1 in reconciler1.recorded_paths[1]
        assert file2 in reconciler2.recorded_paths[1]

        assert file3 not in reconciler1.recorded_paths[1]
        assert file3 not in reconciler2.recorded_paths[1]


def test_batch_scheduler_debouncing(temp_dir: Path) -> None:
    """Test that debouncing works correctly."""
    reconciler = PathRecordingReconciler(tag="test", watch_dirs=(temp_dir,))
    with batch_scheduler_context((reconciler,), debounce_seconds=1.0, max_debounce_seconds=5.0) as batch_scheduler:
        # Create test file
        test_file = temp_dir / "test.txt"
        test_file.write_text("test")

        # Trigger multiple events quickly
        for i in range(5):
            event = FileModifiedEvent(str(test_file))
            batch_scheduler.on_any_event(event)
            time.sleep(0.05)  # Short delay, less than debounce time
        assert len(reconciler.recorded_paths) == 1, (
            "Shouldn't be called after initial flush yet because 0.05s < debounce time (1.0s)"
        )

        # Wait for debounce
        time.sleep(2.0)

        # Should only be called once due to debouncing
        assert len(reconciler.recorded_paths) == 2, "Should be called once moer after debounce period"


# A CI issue apparently - passes locally. I don't recall what the problem was
@pytest.mark.skip
def test_batch_scheduler_max_debouncing(temp_dir: Path) -> None:
    """Test that batches don't pause for longer than max debounce."""
    reconciler = PathRecordingReconciler(tag="test", watch_dirs=(temp_dir,))
    max_sec = 0.0
    debounce_sec = 1.0
    with batch_scheduler_context(
        (reconciler,), debounce_seconds=debounce_sec, max_debounce_seconds=max_sec
    ) as batch_scheduler:
        # Create test file
        test_file = temp_dir / "test.txt"
        test_file.write_text("test")

        event = FileModifiedEvent(str(test_file))
        batch_scheduler.on_any_event(event)
        timestamp = batch_scheduler.debounce._first_debounced_timestamp
        batch_scheduler.on_any_event(event)
        assert batch_scheduler.debounce.is_max_debounce_exceeded, (
            "0 max debounce should prevent other events from rescheduling"
        )
        assert timestamp == batch_scheduler.debounce._first_debounced_timestamp, (
            "0 max debounce should prevent other events from rescheduling"
        )
        time.sleep(0.5)
        batch_scheduler.on_any_event(event)
        assert len(reconciler.recorded_paths) == 1, "Should not be called yet after initial flush"
        assert timestamp == batch_scheduler.debounce._first_debounced_timestamp, (
            "0 max debounce should prevent other events from rescheduling"
        )
        time.sleep(1.0)
        assert len(reconciler.recorded_paths) == 2, "Should be called despite rapid events due to max debounce"


def test_scheduler_pauses_based_on_known_subpath_notices(
    temp_dir: Path, noticeful_reconciler: PathRecordingReconciler
) -> None:
    shouldnt_run_reconciler = build_successful_reconciler(temp_dir, "success")
    with batch_scheduler_context(
        (shouldnt_run_reconciler, noticeful_reconciler), debounce_seconds=0.05
    ) as batch_scheduler:
        callbacks = batch_scheduler._lifecycle_callbacks
        assert isinstance(callbacks, CallbackRecorder), "is just casting"

        batch_scheduler.on_any_event(FileModifiedEvent(str(temp_dir / "success.txt")))
        time.sleep(0.1)

        assert len(shouldnt_run_reconciler.recorded_paths) == 0, (
            "noticeful reconciler should pause the batch before processing"
        )
        assert len(noticeful_reconciler.recorded_paths) == 0, (
            "noticeful reconciler should not process paths due to pause"
        )

        assert len(callbacks.on_new_batch_pending_calls) == 1, (
            "Should have recorded new batch pending once and only once"
        )
        assert len(callbacks.on_batch_update_calls) == 0, "known notices should always cause pause preemtively"
        seen_pause_call_count = len(callbacks.on_handling_paused_calls)
        assert seen_pause_call_count >= 1, "Should have paused due to known notice"

        time.sleep(0.5)
        assert len(callbacks.on_handling_paused_calls) > seen_pause_call_count, (
            f"Should have retried after debounce {seen_pause_call_count=} {len(callbacks.on_handling_paused_calls)=}"
        )


def test_observer_doesnt_stop_on_pausing_reconciler_exception(
    temp_dir: Path, failing_reconciler: PathRecordingReconciler, task_id: TaskID
) -> None:
    """Test exception handling with multiple reconcilers where some fail."""

    observer = SlightlySaferObserver(name="test_observer")
    # TODO: Consider testing watchmedo here also

    successful_reconciler = build_successful_reconciler(temp_dir, "success")
    trailing_reconciler = build_successful_reconciler(temp_dir, "should_not_run")
    batch_scheduler = build_batch_scheduler(
        (successful_reconciler, failing_reconciler, trailing_reconciler),
        debounce_seconds=0.1,
        threading_context=observer.threading_context,
    )
    register_batch_scheduler_with_observer(observer, batch_scheduler)

    callbacks = batch_scheduler._lifecycle_callbacks
    assert isinstance(callbacks, CallbackRecorder), "is just casting"

    observer.start()

    # the exception will be raised once, then logger.info()ed to avoid spam.
    # NOTE: spam reduction is implicitly tested here, so if you're here because of unexpected error logging,
    # it is probably an error in _handle_exception_by_pausing
    success_file = temp_dir / "success.txt"
    success_file.write_text("success")
    fail_file = temp_dir / "fail.txt"
    fail_file.write_text("fail")
    time.sleep(0.75)

    assert observer.is_alive(), "Observer should still be running after reconciler failure"
    observer.stop()
    observer.join(timeout=5)

    # this is here to ensure that any resulting sentry events finish uploading. They can be rather slow
    # this is not a perfect way of fixing this -- would be better to know when the pause handling is done
    sentry_sdk.flush()
    time.sleep(1.0)

    assert len(successful_reconciler.recorded_paths) >= 2, (
        "Expected successful reconciler to process paths once+for initial flush"
    )
    assert len(failing_reconciler.recorded_paths) == 0, "Expected failing reconciler to not process any paths"
    assert len(trailing_reconciler.recorded_paths) == 0, (
        "Trailing reconciler should not have run due to failure in previous reconciler"
    )

    assert len(callbacks.on_new_batch_pending_calls) == 1, "Should have recorded new batch pending once and only once"
    assert len(callbacks.on_batch_update_calls) == 0, "failure should always cause pause"
    assert len(callbacks.on_handling_paused_calls) >= 1, "Should have paused due to failing reconciler"
    assert len(successful_reconciler.recorded_paths) == len(callbacks.on_handling_paused_calls), (
        "Should have same pauses as successful reconciler calls"
    )

    seen_notice = callbacks.on_handling_paused_calls[0][1][0]
    assert seen_notice.source_tag == "failing_reconciler", "Should have seen notice from failing reconciler"
    assert isinstance(seen_notice, LocalSyncNoticeOfPause), "Should have paused on failing reconciler"
    assert "Reconciler failed" in seen_notice.reason, "exception text should be in reason"


@pytest.mark.skip
def test_all_reconciler_methods_have_errors_handled():
    # Should mock each method
    # get_notices, is_relevant_subpath, handle_path_changes
    pass


@pytest.mark.skip
def test_all_healthcheck_methods_have_errors_handled():
    # Should mock each method
    # get_notices, maybe_intercept_event
    pass
