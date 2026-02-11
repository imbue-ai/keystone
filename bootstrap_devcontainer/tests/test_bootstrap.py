import json
import logging
import shlex
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import SAMPLES_DIR, init_git_repo
from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

from bootstrap_devcontainer.bootstrap_devcontainer_cli import app
from bootstrap_devcontainer.constants import DEFAULT_TESTING_LOG_PATH
from bootstrap_devcontainer.process_runner import run_process
from bootstrap_devcontainer.schema import BootstrapResult

logger = logging.getLogger(__name__)


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "[OPTIONS]" in result.stdout
    assert "--project_root" in result.stdout


def test_e2e_fake_agent(tmp_path: Path, project_root: Path) -> None:
    """
    Test the full Docker mechanics using a deterministic fake agent.
    This tests the devcontainer build and test execution without LLM dependencies.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"
    fake_agent = Path(__file__).parent / "fake_agent.py"
    cache_file = tmp_path / "cache.sqlite"

    logger.info("=" * 60)
    logger.info("E2E Test with Fake Agent Starting")
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
    logger.info("=" * 60)

    cmd = [
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--agent_cmd",
        shlex.quote(str(fake_agent)),
        "--log_db",
        str(cache_file),
        "--agent_local",  # Use local runner for fake agent tests
    ]

    logger.info("Running: %s", " ".join(cmd))
    result = CliRunner().invoke(app, cmd)

    # result = run_process(cmd, log_prefix="[fake-agent]")

    assert result.exit_code == 0, f"Process failed: {result.stderr}"
    assert "CACHE MISS" in result.stderr, "Expected cache miss on first run"

    # Check that status lines were emitted to stdout (rich prints in blue)
    if "BOOTSTRAP_DEVCONTAINER_STATUS:" not in result.stdout:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert "BOOTSTRAP_DEVCONTAINER_STATUS:" in result.stdout, "Expected status lines in stdout"

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
    output = BootstrapResult.model_validate_json(json_str)
    assert output.success, f"Test failed: {output}"

    # Verify agent_summary was captured
    assert output.agent.summary is not None, "Expected agent.summary to be set"
    assert output.agent.summary.message == "Created Python devcontainer with pytest support.", (
        f"Expected agent.summary to be captured, got: {output.agent.summary}"
    )

    # Verify status_messages were captured in order
    assert [m.message for m in output.agent.status_messages] == [
        "Exploring repository structure.",
        "Creating devcontainer.json and Dockerfile.",
        "Completed setup of devcontainer files.",
    ], f"Expected status_messages to be captured, got: {output.agent.status_messages}"

    # Verify test_results contents (now nested in verification)
    assert output.verification is not None
    results = output.verification.test_results
    passed = [r for r in results if r.passed and not r.skipped]
    failed = [r for r in results if not r.passed]
    assert len(passed) == 2, f"Expected 2 passed tests: {results}"
    assert len(failed) == 0, f"Expected 0 failed tests: {results}"
    assert any("test_add" in r.name for r in passed), f"Expected test_add in passed: {results}"
    assert any("test_multiply" in r.name for r in passed), (
        f"Expected test_multiply in passed: {results}"
    )

    # Check devcontainer files were created
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()

    # Verify test artifacts were extracted from container via docker cp
    assert (test_artifacts_dir / "pytest-report.xml").exists(), (
        "pytest-report.xml should be extracted from /test_artifacts in container"
    )
    assert (test_artifacts_dir / "final_result.json").exists(), (
        "final_result.json should be extracted from /test_artifacts in container"
    )
    assert (test_artifacts_dir / "pytest" / "stdout.txt").exists(), (
        "pytest/stdout.txt should be extracted from /test_artifacts in container"
    )

    # Verify the content of extracted artifacts
    final_result = json.loads((test_artifacts_dir / "final_result.json").read_text())
    assert final_result["success"] is True, "final_result.json should indicate success"

    # Test cache hit: copy fresh project, run again with same cache
    logger.info("=" * 60)
    logger.info("Testing cache hit")
    logger.info("=" * 60)

    project_root2 = tmp_path / "project2"
    shutil.copytree(SAMPLES_DIR / "python_project", project_root2)
    init_git_repo(project_root2)
    test_artifacts_dir2 = tmp_path / "test_artifacts2"

    cmd2 = [
        # "bootstrap-devcontainer",
        "--project_root",
        str(project_root2),
        "--test_artifacts_dir",
        str(test_artifacts_dir2),
        "--agent_cmd",
        shlex.quote(str(fake_agent)),
        "--log_db",
        str(cache_file),
        "--agent_local",  # Use local runner for fake agent tests
    ]

    result2 = CliRunner().invoke(app, cmd2)

    # result2 = run_process(cmd2, log_prefix="[fake-agent-cached]")

    assert result2.exit_code == 0, f"Cached run failed: {result2.stderr}"
    assert "CACHE HIT" in result2.stderr, "Expected cache hit on second run"
    # Verify devcontainer was restored from cache
    assert (project_root2 / ".devcontainer" / "devcontainer.json").exists()


@pytest.mark.parametrize("project_root", ["rust_project"], indirect=True)
def test_e2e_fake_agent_fails_on_rust_project(tmp_path: Path, project_root: Path) -> None:
    """
    Test that the fake agent (which generates Python devcontainer) fails
    when used against a Rust project, demonstrating proper failure detection.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"
    fake_agent = Path(__file__).parent / "fake_agent.py"

    logger.info("=" * 60)
    logger.info("E2E Test: Fake Agent on Rust Project (Expected Failure)")
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
    logger.info("=" * 60)

    cmd = [
        "bootstrap-devcontainer",
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--agent_cmd",
        shlex.quote(str(fake_agent)),
        "--agent_local",  # Use local runner for fake agent tests
    ]

    logger.info("Running: %s", " ".join(cmd))

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
        output = BootstrapResult.model_validate_json(json_str)
        assert result.returncode != 0 or not output.success, (
            "Expected failure: Python devcontainer cannot run Rust tests"
        )
    else:
        # If we can't parse JSON, the process must have failed
        assert result.returncode != 0, "Expected failure: Python devcontainer cannot run Rust tests"

    # Verify the devcontainer was created (agent ran successfully)
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()


@pytest.mark.manual
@pytest.mark.parametrize(
    "project_root",
    [
        "python_project",
        #        "node_project",
        #        "go_project",
        #        "rust_project",
        "fullstack_project",
        "python_with_failing_test",
        "cmake_vcpkg_project",
    ],
    indirect=True,
)
def test_e2e_sample_projects(
    tmp_path: Path, project_root: Path, snapshot: SnapshotAssertion
) -> None:
    test_artifacts_dir = tmp_path / "test_artifacts"
    cache_file = DEFAULT_TESTING_LOG_PATH

    logger.info("=" * 60)
    logger.info("E2E Test Starting")
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
    logger.info("=" * 60)

    # Use -u for unbuffered Python output
    cmd = [
        "bootstrap-devcontainer",
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--log_db",
        str(cache_file),
    ]

    logger.info("Running: %s", " ".join(cmd))

    # Note: run_process inherits environment variables by default, including:
    # - BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY
    # - BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_USERNAME
    # - BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_PASSWORD
    result = run_process(cmd, log_prefix="[e2e]")

    if "failing" in str(project_root):
        assert result.returncode != 0, "Expected failure for failing project"
    else:
        assert result.returncode == 0, (
            f"f{project_root!s} failed with exit code {result.returncode}"
        )

    # Parse the JSON output (find the JSON object in stdout)
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break
    assert json_start is not None, "Could not find JSON output"
    json_str = "\n".join(stdout_lines[json_start:])
    output = BootstrapResult.model_validate_json(json_str)

    # Check if .devcontainer was created
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()

    # Validate that status messages have proper cumulative costs before stripping
    _validate_status_messages(output)

    # Snapshot test - strip non-deterministic fields
    snapshot_data = _strip_nondeterministic_fields(output)
    assert snapshot_data == snapshot


def _validate_status_messages(output: BootstrapResult) -> None:
    """Validate that status messages have increasing timestamps and non-zero cumulative costs."""
    if not output.agent.status_messages:
        return

    # Verify timestamps are increasing
    prev_ts = None
    for msg in output.agent.status_messages:
        if prev_ts is not None:
            assert msg.timestamp >= prev_ts, (
                f"Timestamps should be non-decreasing: {prev_ts} -> {msg.timestamp}"
            )
        prev_ts = msg.timestamp

    # Verify final cost on the result (not on status messages)
    assert output.agent.cost.cost_usd > 0, f"Result should have non-zero cost: {output.agent.cost}"
    ts = output.agent.cost.token_spending
    assert ts.input > 0 or ts.cached > 0, f"Result should have some input tokens: {ts}"
    assert ts.output > 0, f"Result should have output tokens: {ts}"


def _strip_nondeterministic_fields(output: BootstrapResult) -> dict[str, Any]:
    """Remove timing and cost fields that vary between runs."""
    result = output.model_dump()
    # Remove agent timing/cost fields
    if "agent" in result:
        result["agent"].pop("duration_seconds", None)
        result["agent"].pop("start_time", None)
        result["agent"].pop("end_time", None)
        result["agent"].pop("cost", None)
        # Status messages contain timestamps and cumulative costs that vary
        # Extract just the message text for deterministic comparison
        if "status_messages" in result["agent"]:
            result["agent"]["status_messages"] = [
                msg["message"] for msg in result["agent"]["status_messages"]
            ]
        # Same for summary
        if result["agent"].get("summary") is not None:
            result["agent"]["summary"] = result["agent"]["summary"]["message"]
    # Remove verification timing fields (nested in verification)
    if result.get("verification"):
        result["verification"].pop("image_build_seconds", None)
        result["verification"].pop("test_execution_seconds", None)
    return result


def test_max_budget_zero_fails(tmp_path: Path, project_root: Path) -> None:
    """
    Test that setting --max_budget_usd 0 causes the claude agent to fail
    immediately since it cannot make any API calls. The CLI should return
    a non-zero exit code and include an error_message in the JSON output.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"

    logger.info("=" * 60)
    logger.info("Testing max-budget-usd=0 causes failure")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "bootstrap-devcontainer",
        *("--project_root", str(project_root)),
        *("--test_artifacts_dir", str(test_artifacts_dir)),
        *("--max_budget_usd", "0"),
        "--agent_local",  # Use local runner (budget test uses real claude locally)
    ]

    logger.info("Running: %s", " ".join(cmd))

    result = run_process(cmd, log_prefix="[budget-zero]")

    logger.info("Return code: %s", result.returncode)

    # CLI should return non-zero exit code on failure
    assert result.returncode != 0, "Expected non-zero exit code with zero budget"

    # Parse JSON output - should still be present even on failure
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break

    assert json_start is not None, "Expected JSON output even on failure"
    json_str = "\n".join(stdout_lines[json_start:])
    output = BootstrapResult.model_validate_json(json_str)

    assert not output.success, "Expected success=false with zero budget"
    assert output.error_message, "Expected error_message in output"
    assert (
        "devcontainer" in output.error_message.lower() or "agent" in output.error_message.lower()
    ), f"Expected error message about devcontainer or agent, got: {output.error_message}"


def test_agent_time_limit_causes_timeout(tmp_path: Path, project_root: Path) -> None:
    """
    Test that setting a very short --agent_time_limit_secs causes timeout.
    The CLI should return non-zero exit code and set agent_timed_out=True.

    Uses a slow fake agent that sleeps to ensure timeout triggers.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"

    # Create a slow fake agent that sleeps
    slow_agent = tmp_path / "slow_agent.py"
    slow_agent.write_text("""#!/usr/bin/env python3
import time
time.sleep(10)  # Sleep longer than the timeout
print('{"type": "result"}')
""")
    slow_agent.chmod(0o755)

    logger.info("=" * 60)
    logger.info("Testing agent_time_limit_secs causes timeout")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "bootstrap-devcontainer",
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--agent_cmd",
        str(slow_agent),
        "--agent_local",
        "--agent_time_limit_secs",
        "1",  # 1 second timeout - agent sleeps for 10s so will timeout
    ]

    logger.info("Running: %s", " ".join(cmd))

    result = run_process(cmd, log_prefix="[timeout-test]")

    logger.info("Return code: %s", result.returncode)

    # CLI should return non-zero exit code on timeout
    assert result.returncode != 0, "Expected non-zero exit code with time limit"

    # Parse JSON output - should still be present even on failure
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break

    assert json_start is not None, "Expected JSON output even on timeout"
    json_str = "\n".join(stdout_lines[json_start:])
    output = BootstrapResult.model_validate_json(json_str)

    assert not output.success, "Expected success=false with time limit"
    assert output.agent.timed_out, "Expected agent.timed_out=True"
    assert output.agent.exit_code == 124, "Expected exit code 124 (timeout)"
