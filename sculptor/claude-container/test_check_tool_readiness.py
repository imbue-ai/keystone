"""Unit tests for check_tool_readiness.sh script.

Tests the bash script in isolation using subprocess, without requiring Docker.
"""

import json
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent / "check_tool_readiness.sh"


def create_script_process(ready_file_path: str, timeout: int | None = None) -> subprocess.Popen[str]:
    """Helper to start the readiness script as a subprocess."""
    env = {}
    if timeout is not None:
        env["SCULPTOR_TOOL_READINESS_TIMEOUT"] = str(timeout)

    if ready_file_path == "":
        args = ["bash", str(SCRIPT_PATH)]
    else:
        args = ["bash", str(SCRIPT_PATH), ready_file_path]

    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return process


def test_script_exists():
    """Verify the script file exists and is executable."""
    assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
    assert SCRIPT_PATH.is_file()


def test_script_requires_argument():
    """Script should fail if no ready file path provided."""
    proc = create_script_process("")
    _, stderr = proc.communicate(timeout=5)

    assert proc.returncode == 2
    assert "ready file path argument is required" in stderr
    assert '"decision": "deny"' in stderr


def test_script_succeeds_when_file_exists():
    """Script should succeed immediately if ready file exists."""
    with tempfile.NamedTemporaryFile() as ready_file:
        proc = create_script_process(ready_file.name)
        stdout, _ = proc.communicate(timeout=5)

        assert proc.returncode == 0
        assert '"decision": "allow"' in stdout


def test_script_waits_for_file_creation():
    """Script should wait and succeed when file is created after delay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ready_file = Path(tmpdir) / "ready"

        # Start script in background
        proc = create_script_process(str(ready_file))

        # Wait a bit then create file
        time.sleep(0.3)
        ready_file.touch()

        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode == 0
        assert '"decision": "allow"' in stdout


def test_script_output_format():
    """Verify script outputs valid JSON in expected format."""

    with tempfile.NamedTemporaryFile() as ready_file:
        proc = create_script_process(ready_file.name)
        stdout, _ = proc.communicate(timeout=5)

        # Should be valid JSON
        output = json.loads(stdout)
        assert "decision" in output
        assert output["decision"] == "allow"


def test_script_timeout_output_format():
    """Verify timeout error outputs valid JSON with reason."""

    with tempfile.TemporaryDirectory() as tmpdir:
        ready_file = Path(tmpdir) / "ready"

        proc = create_script_process(str(ready_file), timeout=1)
        _, stderr = proc.communicate(timeout=5)

        # Error output should be valid JSON
        output = json.loads(stderr)
        assert "decision" in output
        assert output["decision"] == "deny"
        assert "reason" in output
        assert len(output["reason"]) > 0


@pytest.mark.parametrize("timeout_value", [1, 2, 5])
def test_script_various_timeouts(timeout_value: int):
    """Test script works correctly with various timeout values."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ready_file = Path(tmpdir) / "ready"

        start = time.monotonic()
        proc = create_script_process(str(ready_file), timeout=timeout_value)
        returncode = proc.wait(timeout=timeout_value + 5)
        elapsed = time.monotonic() - start

        assert returncode == 2
        # Verify timing is approximately correct (with tolerance)
        expected = float(timeout_value)
        assert expected * 0.9 < elapsed < expected * 1.2


def test_script_polls_at_expected_frequency():
    """Verify script polls approximately every 0.1s as documented."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ready_file = Path(tmpdir) / "ready"

        proc = create_script_process(str(ready_file))

        # Create file after a specific delay
        delay = 0.55  # 5-6 poll cycles at 0.1s intervals
        time.sleep(delay)
        ready_file.touch()

        stdout, _ = proc.communicate(timeout=5)

        # Should succeed in reasonable time (within a few poll cycles)
        assert proc.returncode == 0
        assert '"decision": "allow"' in stdout
