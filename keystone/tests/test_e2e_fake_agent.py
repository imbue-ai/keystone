"""End-to-end tests using deterministic fake agents (no real LLM calls).

These tests exercise the full Docker/Modal mechanics — devcontainer build,
test execution, caching, error propagation — without non-deterministic LLM
dependencies.

Markers:
  - local_docker: tests that require a local Docker daemon
  - modal: tests that run on Modal (deterministic)
"""

import json
import logging
import shlex
import shutil
from pathlib import Path

import pytest
from conftest import SAMPLES_DIR, init_git_repo, parse_bootstrap_result
from typer.testing import CliRunner

from keystone.keystone_cli import app
from keystone.process_runner import run_process
from keystone.schema import BootstrapResult

logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "execution_mode",
    [
        pytest.param("local", id="local", marks=pytest.mark.local_docker),
        pytest.param(
            "modal",
            id="modal",
            marks=pytest.mark.modal,
        ),
    ],
)
def test_e2e_fake_agent(
    tmp_path: Path, project_root: Path, execution_mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the full Docker mechanics using a deterministic fake agent.

    This tests the devcontainer build and test execution without LLM dependencies.

    Parameterized to run both locally (--run_agent_locally_with_dangerously_skip_permissions)
    and on Modal (--agent_in_modal with --docker_registry_mirror).
    """
    del caplog
    use_modal = execution_mode == "modal"
    test_artifacts_dir = tmp_path / "test_artifacts"
    fake_agent_src = Path(__file__).parent / "fake_claude_agent.py"
    cache_file = tmp_path / "cache.sqlite"

    # fake_claude_agent.py is baked into the Modal image at /usr/local/bin/fake_claude_agent.py
    agent_cmd_str = "fake_claude_agent.py" if use_modal else str(fake_agent_src)

    logger.info("=" * 60)
    logger.info("E2E Test with Fake Agent Starting (mode=%s)", execution_mode)
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
    logger.info("=" * 60)

    cmd = [
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--agent_cmd",
        shlex.quote(agent_cmd_str),
        "--log_db",
        "--no_evaluator",
        str(cache_file),
    ]
    if use_modal:
        cmd += ["--agent_in_modal", "--docker_registry_mirror", "https://mirror.gcr.io"]
    else:
        cmd += ["--run_agent_locally_with_dangerously_skip_permissions"]

    logger.info("Running: %s", " ".join(cmd))
    result = CliRunner().invoke(app, cmd)

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
    assert (
        output.agent.summary.message
        == "[fake_claude_agent/unknown-model] Created Python devcontainer with pytest support."
    ), f"Expected agent.summary to be captured, got: {output.agent.summary}"

    # Verify status_messages were captured in order
    assert [m.message for m in output.agent.status_messages] == [
        "[fake_claude_agent/unknown-model] Exploring repository structure.",
        "[fake_claude_agent/unknown-model] Creating devcontainer.json and Dockerfile.",
        "[fake_claude_agent/unknown-model] Completed setup of devcontainer files.",
        "[fake_claude_agent/unknown-model] Running guardrail.sh self-check.",
        "[fake_claude_agent/unknown-model] Guardrail self-check passed.",
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
    assert (test_artifacts_dir / "junit" / "pytest.xml").exists(), (
        "junit/pytest.xml should be extracted from /test_artifacts in container"
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
        "--project_root",
        str(project_root2),
        "--test_artifacts_dir",
        str(test_artifacts_dir2),
        "--agent_cmd",
        shlex.quote(agent_cmd_str),
        "--log_db",
        str(cache_file),
    ]
    if use_modal:
        cmd2 += ["--agent_in_modal", "--docker_registry_mirror", "https://mirror.gcr.io"]
    else:
        cmd2 += ["--run_agent_locally_with_dangerously_skip_permissions"]

    result2 = CliRunner().invoke(app, cmd2)

    assert result2.exit_code == 0, f"Cached run failed: {result2.stderr}"
    assert "CACHE HIT" in result2.stderr, "Expected cache hit on second run"
    # Verify devcontainer was restored from cache
    assert (project_root2 / ".devcontainer" / "devcontainer.json").exists()


@pytest.mark.local_docker
@pytest.mark.parametrize("project_root", ["rust_project"], indirect=True)
def test_e2e_fake_agent_fails_on_rust_project(tmp_path: Path, project_root: Path) -> None:
    """Test that the fake agent (which generates Python devcontainer) fails on a Rust project.

    Demonstrates proper failure detection: the Python devcontainer cannot run
    Rust tests, so verification should fail.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"
    fake_agent = Path(__file__).parent / "fake_claude_agent.py"

    logger.info("=" * 60)
    logger.info("E2E Test: Fake Agent on Rust Project (Expected Failure)")
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
        "--run_agent_locally_with_dangerously_skip_permissions",
        "--no_evaluator",
    ]

    logger.info("Running: keystone %s", " ".join(cmd))

    result = CliRunner().invoke(app, cmd)

    logger.info("Exit code: %s", result.exit_code)

    # The script should complete but report failure since Python devcontainer
    # won't have Rust toolchain to run cargo test
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break
    if json_start is not None:
        json_str = "\n".join(stdout_lines[json_start:])
        output = BootstrapResult.model_validate_json(json_str)
        assert result.exit_code != 0 or not output.success, (
            "Expected failure: Python devcontainer cannot run Rust tests"
        )
    else:
        # If we can't parse JSON, the process must have failed
        assert result.exit_code != 0, "Expected failure: Python devcontainer cannot run Rust tests"

    # Verify the devcontainer was created (agent ran successfully)
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()


@pytest.mark.modal
@pytest.mark.parametrize("project_root", ["python_project"], indirect=True)
def test_e2e_agent_error_propagation(tmp_path: Path, project_root: Path) -> None:
    """Verify that agent errors (e.g. prompt rejection) propagate into BootstrapResult.

    Uses the fake_codex_agent.py with --model=fake-error-model to deterministically
    simulate a turn.failed event (like OpenAI's content filter rejection). Verifies:
    1. The CLI exits with non-zero exit code.
    2. The BootstrapResult JSON has success=False.
    3. The error_message includes both the verification failure AND the agent error.
    4. agent.error_messages contains the structured error from the agent.
    """

    test_artifacts_dir = tmp_path / "test_artifacts"
    cache_file = tmp_path / "codex_error_cache.sqlite"

    # fake_codex_agent.py is baked into the Modal image at /usr/local/bin/
    agent_cmd = "fake_codex_agent.py --model=fake-error-model"

    logger.info("=" * 60)
    logger.info("E2E Test: Agent error propagation")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "keystone",
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--log_db",
        str(cache_file),
        "--provider",
        "codex",
        "--agent_cmd",
        agent_cmd,
        "--agent_in_modal",
        "--docker_registry_mirror",
        "https://mirror.gcr.io",
        "--no_cache_replay",
    ]

    logger.info("Running: %s", " ".join(cmd))
    result = run_process(cmd, log_prefix="[agent-error-propagation]")

    output = parse_bootstrap_result(result.stdout)

    # The CLI should exit with non-zero code
    assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"

    # The result should indicate failure
    assert not output.success, "Expected success=False"

    # The agent should have exited non-zero
    assert output.agent.exit_code != 0, (
        f"Expected non-zero agent exit code, got {output.agent.exit_code}"
    )

    # Agent structured errors should be captured
    assert output.agent.error_messages, "Expected agent.error_messages to be populated"
    assert any("usage policy" in msg for msg in output.agent.error_messages), (
        f"Expected content filter error in agent.error_messages, got: {output.agent.error_messages}"
    )

    # The top-level error_message should include both verification failure AND root cause
    assert output.error_message is not None, "Expected an error_message"
    assert "Root cause" in output.error_message, (
        f"Expected 'Root cause' in error_message, got: {output.error_message}"
    )
    # The agent's structured error should be in the error message
    assert "usage policy" in output.error_message, (
        f"Expected agent error text in error_message, got: {output.error_message}"
    )

    logger.info("Agent error propagation test passed:")
    logger.info("  exit code: %d", result.returncode)
    logger.info("  agent exit code: %d", output.agent.exit_code)
    logger.info("  error_message: %s", output.error_message)
    logger.info("  agent.error_messages: %s", output.agent.error_messages)


@pytest.mark.local_docker
@pytest.mark.skipif(
    not shutil.which("timeout"), reason="GNU timeout not available (install coreutils)"
)
def test_agent_time_limit_causes_timeout(tmp_path: Path, project_root: Path) -> None:
    """Test that setting a very short --agent_time_limit_seconds causes timeout.

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
    logger.info("Testing agent_time_limit_seconds causes timeout")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "keystone",
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--agent_cmd",
        str(slow_agent),
        "--run_agent_locally_with_dangerously_skip_permissions",
        "--agent_time_limit_seconds",
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
