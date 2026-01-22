"""Utility for running subprocesses with streaming stdout/stderr capture."""

import logging
import os
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    """Result of a process run with captured output."""

    returncode: int
    stdout: str
    stderr: str


def _stream_reader(
    stream: Iterable[str],
    lines_list: list[str],
    line_callback: Callable[[str], None] | None,
) -> None:
    """Read lines from stream, optionally calling callback for each line."""
    for line in stream:
        line = line.rstrip("\n")
        if line_callback:
            line_callback(line)
        lines_list.append(line)


def run_process(
    cmd: list[str],
    log_prefix: str = "",
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    stdout_callback: Callable[[str], None] | None = None,
    stderr_callback: Callable[[str], None] | None = None,
) -> ProcessResult:
    """
    Run a subprocess with multi-threaded stdout/stderr capture.

    Args:
        cmd: Command and arguments to run.
        log_prefix: Prefix for log messages. If empty, no logging (unless callbacks provided).
        env: Environment variables. If None, inherits current env with PYTHONUNBUFFERED=1.
        cwd: Working directory for the process.
        stdout_callback: Called for each stdout line (after stripping newline).
        stderr_callback: Called for each stderr line (after stripping newline).

    Returns:
        ProcessResult with returncode, stdout, and stderr.
    """
    if env is None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
        cwd=cwd,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    # Use explicit callbacks if provided, otherwise fall back to log_prefix logging
    effective_stdout_cb: Callable[[str], None] | None = stdout_callback
    effective_stderr_cb: Callable[[str], None] | None = stderr_callback

    def _log_stdout(line: str) -> None:
        logger.info("%s STDOUT: %s", log_prefix, line)

    def _log_stderr(line: str) -> None:
        logger.info("%s STDERR: %s", log_prefix, line)

    if log_prefix and not stdout_callback:
        effective_stdout_cb = _log_stdout
    if log_prefix and not stderr_callback:
        effective_stderr_cb = _log_stderr

    stdout_thread = threading.Thread(
        target=_stream_reader,
        args=(process.stdout, stdout_lines, effective_stdout_cb),
        name="stdout-reader",
    )
    stderr_thread = threading.Thread(
        target=_stream_reader,
        args=(process.stderr, stderr_lines, effective_stderr_cb),
        name="stderr-reader",
    )

    stdout_thread.start()
    stderr_thread.start()
    stdout_thread.join()
    stderr_thread.join()
    process.wait()

    return ProcessResult(
        returncode=process.returncode,
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
    )
