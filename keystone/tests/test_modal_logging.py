"""Tests for modal logging output format."""

import logging
from collections.abc import Iterator

import pytest

from bootstrap_devcontainer.modal.modal_runner import ManagedProcess


class FakeProcess:
    """Fake Modal process that yields predefined lines."""

    def __init__(self, stdout_lines: list[str], stderr_lines: list[str]) -> None:
        self._stdout = iter(stdout_lines)
        self._stderr = iter(stderr_lines)

    @property
    def stdout(self) -> Iterator[str]:
        return self._stdout

    @property
    def stderr(self) -> Iterator[str]:
        return self._stderr

    def wait(self) -> None:
        pass

    @property
    def returncode(self) -> int:
        return 0


def test_managed_process_log_format(caplog: pytest.LogCaptureFixture) -> None:
    """Test that ManagedProcess logs in the expected format: [name] line."""
    fake_proc = FakeProcess(
        stdout_lines=["hello from stdout\n"],
        stderr_lines=["hello from stderr\n"],
    )

    with caplog.at_level(logging.INFO):
        mp = ManagedProcess(fake_proc, prefix="test-proc", capture=False)
        mp.wait()

    # Check log messages
    log_messages = [r.message for r in caplog.records]

    # stdout should be "[test-proc] STDOUT: hello from stdout"
    # stderr should be "[test-proc] STDERR: hello from stderr"
    assert any("[test-proc] STDOUT: hello from stdout" in m for m in log_messages), (
        f"Got: {log_messages}"
    )
    assert any("[test-proc] STDERR: hello from stderr" in m for m in log_messages), (
        f"Got: {log_messages}"
    )
