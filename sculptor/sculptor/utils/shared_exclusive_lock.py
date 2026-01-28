import threading
import time
from contextlib import contextmanager
from typing import Generator


class SharedExclusiveLock:
    """A shared/exclusive lock that allows multiple sharers or a single excluder.

    Sharers (sometimes called readers) share the lock unless an excluder (writer) is active or waiting.
    Excluders gain exclusive access and block new sharers until they release the lock.

    AKA: ReadWriteLock.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_sharers = 0
        self._active_excluder = False
        self._waiting_excluders = 0

    @contextmanager
    def _shared_lock(self, timeout: float | None) -> Generator[bool, None, None]:
        """Acquire the lock which can be shared by multiple threads, unless an excluder is active."""
        start_time = time.monotonic()
        with self._condition:
            while self._waiting_excluders > 0 or self._active_excluder:
                self._condition.wait(timeout)
                # only reachable if timeout is not None
                is_timed_out = timeout is not None and time.monotonic() - start_time >= timeout
                if is_timed_out:
                    yield False
                    return

            self._active_sharers += 1
        try:
            yield True
        finally:
            with self._condition:
                self._active_sharers -= 1
                if self._active_sharers == 0:
                    self._condition.notify_all()

    @contextmanager
    def shared_lock(self) -> Generator[None, None, None]:
        with self._shared_lock(timeout=None) as is_acquired:
            assert is_acquired, "should never happen"
            yield

    @contextmanager
    def shared_lock_or_timeout(self, timeout: float) -> Generator[bool, None, None]:
        with self._shared_lock(timeout=timeout) as is_acquired:
            yield is_acquired

    @contextmanager
    def exclusive_lock(self) -> Generator[None, None, None]:
        """Acquire an exclusive lock which can be held by at most one thread and excludes shared locks."""
        with self._condition:
            self._waiting_excluders += 1
            while self._active_excluder or self._active_sharers > 0:
                self._condition.wait()
            self._active_excluder = True
            self._waiting_excluders -= 1
        try:
            yield
        finally:
            with self._condition:
                self._active_excluder = False
                self._condition.notify_all()
