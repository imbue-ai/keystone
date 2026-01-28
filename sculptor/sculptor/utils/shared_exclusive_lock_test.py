import threading
import time

from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.utils.shared_exclusive_lock import SharedExclusiveLock


def _sharer_task(lock: SharedExclusiveLock, barrier: threading.Barrier) -> None:
    """Ensure all sharers can enter together."""
    with lock.shared_lock():
        barrier.wait(timeout=1.0)


def test_sharers_run_concurrently(test_root_concurrency_group: ConcurrencyGroup) -> None:
    lock = SharedExclusiveLock()
    num_sharers = 3
    barrier = threading.Barrier(num_sharers)

    with ObservableThreadPoolExecutor(test_root_concurrency_group, max_workers=num_sharers) as executor:
        futures = [executor.submit(_sharer_task, lock, barrier) for _ in range(num_sharers)]
        for future in futures:
            future.result()


def _excluder_with_signals(
    lock: SharedExclusiveLock,
    events: list[str],
    release_event: threading.Event,
    acquired_event: threading.Event,
) -> None:
    with lock.exclusive_lock():
        acquired_event.set()
        events.append("excluder_start")
        release_event.wait()
        events.append("excluder_end")


def _sharer_with_record(
    lock: SharedExclusiveLock,
    events: list[str],
    sharer_id: int,
    started_event: threading.Event,
) -> None:
    started_event.set()
    with lock.shared_lock():
        events.append(f"sharer_{sharer_id}")
        time.sleep(0.001)


def test_excluder_blocks_sharers() -> None:
    lock = SharedExclusiveLock()
    events: list[str] = []
    release_excluder = threading.Event()
    excluder_acquired = threading.Event()

    excluder_thread = threading.Thread(
        target=_excluder_with_signals,
        args=(lock, events, release_excluder, excluder_acquired),
    )
    excluder_thread.start()
    excluder_acquired.wait()

    sharer_started = threading.Event()
    sharer_thread = threading.Thread(
        target=_sharer_with_record,
        args=(lock, events, 1, sharer_started),
    )
    sharer_thread.start()
    sharer_started.wait()

    assert events == ["excluder_start"]

    release_excluder.set()
    excluder_thread.join()
    sharer_thread.join()

    assert events == ["excluder_start", "excluder_end", "sharer_1"]


def _excluder_task(lock: SharedExclusiveLock, events: list[str], hold_event: threading.Event | None = None) -> None:
    with lock.exclusive_lock():
        events.append("excluder_start")
        if hold_event:
            hold_event.wait()
        else:
            time.sleep(0.01)
        events.append("excluder_end")


def test_multiple_excluders_serialize(test_root_concurrency_group: ConcurrencyGroup) -> None:
    lock = SharedExclusiveLock()
    events: list[str] = []

    with ObservableThreadPoolExecutor(test_root_concurrency_group, max_workers=3) as executor:
        futures = [executor.submit(_excluder_task, lock, events) for _ in range(3)]
        for future in futures:
            future.result()

    expected = ["excluder_start", "excluder_end"] * 3
    assert events == expected


def test_sharers_wait_for_excluder_then_proceed() -> None:
    lock = SharedExclusiveLock()
    events: list[str] = []
    release_excluder = threading.Event()
    excluder_acquired = threading.Event()

    excluder_thread = threading.Thread(
        target=_excluder_with_signals,
        args=(lock, events, release_excluder, excluder_acquired),
    )
    excluder_thread.start()
    excluder_acquired.wait()

    sharer_threads: list[threading.Thread] = []
    sharer_started_events: list[threading.Event] = []
    for i in range(3):
        started = threading.Event()
        thread = threading.Thread(
            target=_sharer_with_record,
            args=(lock, events, i, started),
        )
        thread.start()
        sharer_threads.append(thread)
        sharer_started_events.append(started)

    for started in sharer_started_events:
        started.wait()

    assert events == ["excluder_start"]

    release_excluder.set()
    excluder_thread.join()
    for thread in sharer_threads:
        thread.join()

    assert events[0] == "excluder_start"
    assert events[1] == "excluder_end"
    assert set(events[2:]) == {"sharer_0", "sharer_1", "sharer_2"}
    assert len(events) == 5


def _blocking_sharer(
    lock: SharedExclusiveLock,
    entered_event: threading.Event,
    release_event: threading.Event,
) -> None:
    with lock.shared_lock():
        entered_event.set()
        release_event.wait()


def _waiting_excluder(
    lock: SharedExclusiveLock, started_event: threading.Event, entered_event: threading.Event
) -> None:
    started_event.set()
    with lock.exclusive_lock():
        entered_event.set()


def test_excluder_waits_until_sharer_releases() -> None:
    lock = SharedExclusiveLock()
    sharer_entered = threading.Event()
    sharer_release = threading.Event()
    excluder_entered = threading.Event()

    sharer_thread = threading.Thread(
        target=_blocking_sharer,
        args=(lock, sharer_entered, sharer_release),
    )
    sharer_thread.start()
    sharer_entered.wait()

    excluder_started = threading.Event()
    excluder_thread = threading.Thread(
        target=_waiting_excluder,
        args=(lock, excluder_started, excluder_entered),
    )
    excluder_thread.start()
    excluder_started.wait()
    assert not excluder_entered.is_set()

    sharer_release.set()
    sharer_thread.join()
    excluder_thread.join()
    assert excluder_entered.is_set()


def _blocking_excluder(
    lock: SharedExclusiveLock,
    started_event: threading.Event,
    entered_event: threading.Event,
    release_event: threading.Event,
) -> None:
    started_event.set()
    with lock.exclusive_lock():
        entered_event.set()
        release_event.wait()


def _waiting_sharer(
    lock: SharedExclusiveLock,
    started_event: threading.Event,
    entered_event: threading.Event,
) -> None:
    started_event.set()
    with lock.shared_lock():
        entered_event.set()


def test_new_sharers_block_while_excluder_waits() -> None:
    lock = SharedExclusiveLock()
    first_sharer_entered = threading.Event()
    release_first_sharer = threading.Event()
    excluder_entered = threading.Event()
    excluder_released = threading.Event()
    late_sharer_started = threading.Event()
    late_sharer_entered = threading.Event()

    first_sharer_thread = threading.Thread(
        target=_blocking_sharer,
        args=(lock, first_sharer_entered, release_first_sharer),
    )
    first_sharer_thread.start()
    first_sharer_entered.wait()

    excluder_started = threading.Event()
    excluder_thread = threading.Thread(
        target=_blocking_excluder,
        args=(lock, excluder_started, excluder_entered, excluder_released),
    )
    excluder_thread.start()
    excluder_started.wait()
    assert not excluder_entered.is_set()

    late_sharer_thread = threading.Thread(
        target=_waiting_sharer,
        args=(lock, late_sharer_started, late_sharer_entered),
    )
    late_sharer_thread.start()
    late_sharer_started.wait()
    assert not late_sharer_entered.is_set()

    release_first_sharer.set()
    excluder_entered.wait(timeout=1.0)
    assert excluder_entered.is_set()

    excluder_released.set()
    late_sharer_thread.join(timeout=1.0)
    assert late_sharer_entered.is_set()
    excluder_thread.join()
    first_sharer_thread.join()


def test_shared_lock_or_timeout_acquires_with_other_sharers() -> None:
    """Test that shared_lock_or_timeout acquires when other sharers hold the lock."""
    lock = SharedExclusiveLock()
    sharer_entered = threading.Event()
    sharer_release = threading.Event()
    sharer_thread = threading.Thread(
        target=_blocking_sharer,
        args=(lock, sharer_entered, sharer_release),
    )
    sharer_thread.start()
    sharer_entered.wait()
    # Should be able to acquire shared lock even with another sharer
    with lock.shared_lock_or_timeout(timeout=1.0) as acquired:
        assert acquired, "Should acquire shared lock alongside other sharers"
    sharer_release.set()
    sharer_thread.join()


def test_shared_lock_or_timeout_times_out_when_excluder_holds_lock() -> None:
    """Test that shared_lock_or_timeout times out when an excluder holds the lock."""
    lock = SharedExclusiveLock()
    excluder_entered = threading.Event()
    excluder_release = threading.Event()
    excluder_started = threading.Event()
    excluder_thread = threading.Thread(
        target=_blocking_excluder,
        args=(lock, excluder_started, excluder_entered, excluder_release),
    )
    excluder_thread.start()
    excluder_entered.wait()
    start = time.monotonic()
    with lock.shared_lock_or_timeout(timeout=0.1) as acquired:
        elapsed = time.monotonic() - start
        assert not acquired, "Should timeout when excluder holds lock"
        assert elapsed >= 0.1, "Should wait for at least the timeout duration"
        assert elapsed < 0.5, "Should not wait much longer than timeout"
    excluder_release.set()
    excluder_thread.join()


def test_shared_lock_or_timeout_times_out_when_excluder_waiting() -> None:
    """Test that shared_lock_or_timeout times out when an excluder is waiting."""
    lock = SharedExclusiveLock()
    first_sharer_entered = threading.Event()
    release_first_sharer = threading.Event()
    excluder_started = threading.Event()
    excluder_entered = threading.Event()
    excluder_release = threading.Event()
    # First sharer acquires
    first_sharer_thread = threading.Thread(
        target=_blocking_sharer,
        args=(lock, first_sharer_entered, release_first_sharer),
    )
    first_sharer_thread.start()
    first_sharer_entered.wait()
    # Excluder starts waiting
    excluder_thread = threading.Thread(
        target=_blocking_excluder,
        args=(lock, excluder_started, excluder_entered, excluder_release),
    )
    excluder_thread.start()
    excluder_started.wait()
    time.sleep(0.01)  # Give excluder time to start waiting
    # New sharer with timeout should timeout because excluder is waiting
    start = time.monotonic()
    with lock.shared_lock_or_timeout(timeout=0.1) as acquired:
        elapsed = time.monotonic() - start
        assert not acquired, "Should timeout when excluder is waiting"
        assert elapsed >= 0.1, "Should wait for at least the timeout duration"
    # Cleanup
    release_first_sharer.set()
    excluder_entered.wait()
    excluder_release.set()
    first_sharer_thread.join()
    excluder_thread.join()


def test_shared_lock_or_timeout_acquires_after_excluder_releases() -> None:
    """Test that shared_lock_or_timeout acquires if excluder releases before timeout."""
    lock = SharedExclusiveLock()
    excluder_entered = threading.Event()
    excluder_release = threading.Event()
    excluder_started = threading.Event()
    excluder_thread = threading.Thread(
        target=_blocking_excluder,
        args=(lock, excluder_started, excluder_entered, excluder_release),
    )
    excluder_thread.start()
    excluder_entered.wait()

    # Release excluder after a short delay
    def release_excluder() -> None:
        time.sleep(0.05)
        excluder_release.set()

    release_thread = threading.Thread(target=release_excluder)
    release_thread.start()
    # Should acquire within the timeout after excluder releases
    start = time.monotonic()
    with lock.shared_lock_or_timeout(timeout=1.0) as acquired:
        elapsed = time.monotonic() - start
        assert acquired, "Should acquire after excluder releases"
        assert elapsed < 0.5, "Should acquire reasonably quickly after release"
    excluder_thread.join()
    release_thread.join()


def test_shared_lock_or_timeout_zero_timeout_fails_when_blocked() -> None:
    """Test that a zero timeout fails immediately when lock is blocked."""
    lock = SharedExclusiveLock()
    excluder_entered = threading.Event()
    excluder_release = threading.Event()
    excluder_started = threading.Event()
    excluder_thread = threading.Thread(
        target=_blocking_excluder,
        args=(lock, excluder_started, excluder_entered, excluder_release),
    )
    excluder_thread.start()
    excluder_entered.wait()
    start = time.monotonic()
    with lock.shared_lock_or_timeout(timeout=0.0) as acquired:
        elapsed = time.monotonic() - start
        assert not acquired, "Should fail immediately with zero timeout"
        assert elapsed < 0.1, "Should return almost immediately"
    excluder_release.set()
    excluder_thread.join()
