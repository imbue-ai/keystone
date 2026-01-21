import json
import logging
import shlex
import shutil
import subprocess

import pytest
from pathlib import Path

from process_runner import run_process

logger = logging.getLogger(__name__)


def test_cli_help() -> None:
    result = subprocess.run(
        ["python3", "bootstrap_devcontainer.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "bootstrap_devcontainer.py [OPTIONS] PROJECT_ROOT" in result.stdout


def test_e2e_with_fake_agent(tmp_path: Path) -> None:
    """
    Test the full Docker mechanics using a deterministic fake agent.
    This tests the devcontainer build and test execution without LLM dependencies.
    """

    # Copy sample project to tmp_path
    original_project_root = Path(__file__).parent.parent / "samples/python_project"
    project_root = tmp_path / "project"
    shutil.copytree(original_project_root, project_root)

    test_artifacts_dir = tmp_path / "test_artifacts"
    fake_agent = Path(__file__).parent / "fake_agent.py"
    cache_file = tmp_path / "cache.sqlite"

    logger.info("=" * 60)
    logger.info("E2E Test with Fake Agent Starting")
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
    logger.info("=" * 60)

    cmd = [
        "python3", "-u",
        "bootstrap_devcontainer.py",
        str(project_root),
        "--test-artifacts-dir", str(test_artifacts_dir),
        "--agent-cmd", f"python3 {shlex.quote(str(fake_agent))}",
        "--sqlite-cache-file", str(cache_file),
    ]

    logger.info("Running: %s", ' '.join(cmd))

    result = run_process(cmd, log_prefix="[fake-agent]")

    logger.info("Return code: %s", result.returncode)

    assert result.returncode == 0, f"Process failed: {result.stderr}"
    assert "CACHE MISS" in result.stderr, "Expected cache miss on first run"

    # Check that status lines were emitted to stdout (rich prints in blue)
    assert "BOOTSTRAP_DEVCONTAINER_STATUS:" in result.stdout, \
        "Expected status lines in stdout"

    # Parse the JSON output (last line after status messages)
    # Find the JSON object in stdout (it spans multiple lines)
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break
    assert json_start is not None, "Could not find JSON output"
    json_str = "\n".join(stdout_lines[json_start:])
    output = json.loads(json_str)
    assert output["success"], f"Test failed: {output}"

    # Check artifacts
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()
    assert (test_artifacts_dir / "pytest-json-report.json").exists()
    assert (test_artifacts_dir / "final_result.json").exists()

    # Test cache hit: copy fresh project, run again with same cache
    logger.info("=" * 60)
    logger.info("Testing cache hit")
    logger.info("=" * 60)

    project_root2 = tmp_path / "project2"
    shutil.copytree(original_project_root, project_root2)
    test_artifacts_dir2 = tmp_path / "test_artifacts2"

    cmd2 = [
        "python3", "-u",
        "bootstrap_devcontainer.py",
        str(project_root2),
        "--test-artifacts-dir", str(test_artifacts_dir2),
        "--agent-cmd", f"python3 {shlex.quote(str(fake_agent))}",
        "--sqlite-cache-file", str(cache_file),
    ]

    result2 = run_process(cmd2, log_prefix="[fake-agent-cached]")

    assert result2.returncode == 0, f"Cached run failed: {result2.stderr}"
    assert "CACHE HIT" in result2.stderr, "Expected cache hit on second run"
    # Verify devcontainer was restored from cache
    assert (project_root2 / ".devcontainer" / "devcontainer.json").exists()


def test_e2e_fake_agent_fails_on_rust_project(tmp_path: Path) -> None:
    """
    Test that the fake agent (which generates Python devcontainer) fails
    when used against a Rust project, demonstrating proper failure detection.
    """

    # Copy Rust sample project to tmp_path
    original_project_root = Path(__file__).parent.parent / "samples/rust_project"
    project_root = tmp_path / "project"
    shutil.copytree(original_project_root, project_root)

    test_artifacts_dir = tmp_path / "test_artifacts"
    fake_agent = Path(__file__).parent / "fake_agent.py"

    logger.info("=" * 60)
    logger.info("E2E Test: Fake Agent on Rust Project (Expected Failure)")
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
    logger.info("=" * 60)

    cmd = [
        "python3", "-u",
        "bootstrap_devcontainer.py",
        str(project_root),
        "--test-artifacts-dir", str(test_artifacts_dir),
        "--agent-cmd", f"python3 {shlex.quote(str(fake_agent))}",
    ]

    logger.info("Running: %s", ' '.join(cmd))

    result = run_process(cmd, log_prefix="[fake-agent-rust]")

    logger.info("Return code: %s", result.returncode)

    # The script should complete but report failure since Python devcontainer
    # won't have Rust toolchain to run cargo test
    # Parse the JSON output (find it after status messages)
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break
    if json_start is not None:
        json_str = "\n".join(stdout_lines[json_start:])
        output = json.loads(json_str)
        assert result.returncode != 0 or not output.get("success", True), \
            "Expected failure: Python devcontainer cannot run Rust tests"
    else:
        # If we can't parse JSON, the process must have failed
        assert result.returncode != 0, \
            "Expected failure: Python devcontainer cannot run Rust tests"

    # Verify the devcontainer was created (agent ran successfully)
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()


@pytest.mark.manual
def test_e2e_sample_project(tmp_path: Path) -> None:
    # Copy sample project to tmp_path to avoid modifying the original source tree
    original_project_root = Path(__file__).parent.parent / "samples/python_project"
    project_root = tmp_path / "project"
    shutil.copytree(original_project_root, project_root)

    test_artifacts_dir = tmp_path / "test_artifacts"
    cache_file = Path.home() / ".cache" / "bootstrap_devcontainer.sqlite"

    logger.info("=" * 60)
    logger.info("E2E Test Starting")
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
    logger.info("=" * 60)

    # Use -u for unbuffered Python output
    cmd = [
        "python3", "-u",
        "bootstrap_devcontainer.py",
        str(project_root),
        "--test-artifacts-dir",
        str(test_artifacts_dir),
        "--sqlite-cache-file",
        str(cache_file),
    ]

    logger.info("Running: %s", ' '.join(cmd))

    result = run_process(cmd, log_prefix="[e2e]")

    assert result.returncode == 0

    # Parse the JSON output (find the JSON object in stdout)
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break
    assert json_start is not None, "Could not find JSON output"
    json_str = "\n".join(stdout_lines[json_start:])
    output = json.loads(json_str)

    assert "success" in output
    assert "total_time" in output
    assert "token_spending" in output

    # Check if .devcontainer was created
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()



@pytest.mark.manual
def test_max_budget_zero_fails(tmp_path: Path) -> None:
    """
    Test that setting --max-budget-usd 0 causes the claude agent to fail
    immediately since it cannot make any API calls.
    """
    # Copy sample project to tmp_path
    original_project_root = Path(__file__).parent.parent / "samples/python_project"
    project_root = tmp_path / "project"
    shutil.copytree(original_project_root, project_root)

    test_artifacts_dir = tmp_path / "test_artifacts"

    logger.info("=" * 60)
    logger.info("Testing max-budget-usd=0 causes failure")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "python3", "-u",
        "bootstrap_devcontainer.py",
        str(project_root),
        "--test-artifacts-dir", str(test_artifacts_dir),
        "--max-budget-usd", "0",
    ]

    logger.info("Running: %s", ' '.join(cmd))

    result = run_process(cmd, log_prefix="[budget-zero]")

    logger.info("Return code: %s", result.returncode)

    # Parse JSON output if present
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break

    if json_start is not None:
        json_str = "\n".join(stdout_lines[json_start:])
        output = json.loads(json_str)
        assert not output.get("success", True), \
            "Expected failure with zero budget"
    else:
        # If no JSON output, process should have failed
        assert result.returncode != 0, \
            "Expected failure with zero budget"

