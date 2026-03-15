"""End-to-end tests using deterministic fake agents (no real LLM calls).

These tests exercise the full Docker/Modal mechanics — devcontainer build,
test execution, caching, error propagation — without non-deterministic LLM
dependencies.

Markers:
  - local_docker: tests that require a local Docker daemon
  - modal: tests that run on Modal (deterministic)
"""

import io
import json
import logging
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from conftest import SAMPLES_DIR, init_git_repo, parse_bootstrap_result
from typer.testing import CliRunner

from keystone.junit_report_parser import parse_junit_xml
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
        str(cache_file),
    ]
    if use_modal:
        cmd += [
            "--agent_in_modal",
            "--docker_registry_mirror",
            os.environ["DOCKER_REGISTRY_MIRROR"],
        ]
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
        cmd2 += [
            "--agent_in_modal",
            "--docker_registry_mirror",
            os.environ["DOCKER_REGISTRY_MIRROR"],
        ]
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
        os.environ["DOCKER_REGISTRY_MIRROR"],
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


@pytest.mark.modal
@pytest.mark.agentic
@pytest.mark.parametrize("project_root", ["python_project"], indirect=True)
def test_agent_time_limit_causes_timeout(tmp_path: Path, project_root: Path) -> None:
    """Test that setting a very short --agent_time_limit_seconds causes timeout.

    Uses a real Claude agent on Modal with a 1-second time limit. The agent
    will always exceed this, triggering the timeout path. Verifies the CLI
    returns non-zero exit code and sets agent.timed_out=True.

    Requires ANTHROPIC_API_KEY in the environment and Modal credentials configured.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"
    cache_file = tmp_path / "timeout_test_cache.sqlite"

    logger.info("=" * 60)
    logger.info("Testing agent_time_limit_seconds causes timeout")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--log_db",
        str(cache_file),
        "--model",
        "claude-opus-4-6",
        "--claude_reasoning_level",
        "low",
        "--agent_in_modal",
        "--docker_registry_mirror",
        os.environ["DOCKER_REGISTRY_MIRROR"],
        "--no_cache_replay",
        "--agent_time_limit_seconds",
        "1",  # 1 second timeout - agent startup alone exceeds this
    ]

    logger.info("Running: keystone %s", " ".join(cmd))

    result = CliRunner().invoke(app, cmd)

    # Surface CLI crashes before attempting to parse JSON output
    if result.exception and not isinstance(result.exception, SystemExit):
        logger.error("CLI raised an exception:\n%s", result.exception)
        raise result.exception

    logger.info("Exit code: %s", result.exit_code)

    output = parse_bootstrap_result(result.stdout)

    # CLI should return non-zero exit code on timeout
    assert result.exit_code != 0, "Expected non-zero exit code with time limit"
    assert not output.success, "Expected success=false with time limit"
    assert output.agent.timed_out, "Expected agent.timed_out=True"


@pytest.mark.local_docker
def test_docker_cp_extracts_artifacts_when_dest_exists(tmp_path: Path) -> None:
    """Verify docker cp with '/.' copies contents even when destination pre-exists.

    This tests the fix for a bug where the modal runner's `docker cp` without
    trailing '/.' would nest artifacts when the destination directory already
    existed (e.g. because the agent ran `docker cp` during its own run).

    Without the '/.' suffix:
      docker cp container:/test_artifacts /tmp/test_artifacts
      → /tmp/test_artifacts/test_artifacts/junit/results.xml  (NESTED, BAD)

    With the '/.' suffix:
      docker cp container:/test_artifacts/. /tmp/test_artifacts
      → /tmp/test_artifacts/junit/results.xml  (FLAT, GOOD)

    The glob 'test_artifacts_dir/junit/*.xml' must find files in both cases.
    """
    container_name = "keystone-test-docker-cp-artifacts"
    junit_xml = '<?xml version="1.0" ?><testsuites><testsuite name="s" tests="1"><testcase name="t1" classname="c"/></testsuite></testsuites>'

    # Create a container with /test_artifacts/junit/results.xml
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "--name",
            container_name,
            "alpine:3.18",
            "mkdir",
            "-p",
            "/test_artifacts/junit",
        ],
        capture_output=True,
        check=True,
    )

    try:
        # Inject the test artifacts via tar
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            info = tarfile.TarInfo(name="junit/results.xml")
            data = junit_xml.encode()
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

            info2 = tarfile.TarInfo(name="final_result.json")
            data2 = b'{"success": true}'
            info2.size = len(data2)
            tar.addfile(info2, io.BytesIO(data2))

        tar_buffer.seek(0)
        proc = subprocess.run(
            ["docker", "cp", "-", f"{container_name}:/test_artifacts"],
            input=tar_buffer.read(),
            capture_output=True,
        )
        assert proc.returncode == 0, f"Failed to inject artifacts: {proc.stderr}"

        # Case 1: destination does NOT exist — both styles work
        dest_fresh = tmp_path / "fresh_dest"
        subprocess.run(
            ["docker", "cp", f"{container_name}:/test_artifacts/.", str(dest_fresh)],
            capture_output=True,
            check=True,
        )
        found = list(dest_fresh.glob("junit/*.xml"))
        assert len(found) == 1, (
            f"Expected 1 xml in fresh dest, found: {list(dest_fresh.rglob('*'))}"
        )

        # Case 2: destination ALREADY EXISTS (simulates agent's docker cp)
        # This is the scenario that triggered the bug.
        dest_preexisting = tmp_path / "preexisting_dest"
        dest_preexisting.mkdir()
        (dest_preexisting / "stale_file.txt").write_text("stale")

        subprocess.run(
            ["docker", "cp", f"{container_name}:/test_artifacts/.", str(dest_preexisting)],
            capture_output=True,
            check=True,
        )
        found = list(dest_preexisting.glob("junit/*.xml"))
        assert len(found) == 1, (
            f"Expected 1 xml in pre-existing dest with '/.' suffix, "
            f"found: {list(dest_preexisting.rglob('*'))}"
        )

        # Verify parsing works on the extracted file
        results = parse_junit_xml(found[0])
        assert len(results) == 1
        assert results[0].passed
        assert "t1" in results[0].name

        # Case 3: WITHOUT '/.' suffix and dest exists — shows the bug
        # docker cp nests the directory when dest exists and no '/.' used
        dest_buggy = tmp_path / "buggy_dest"
        dest_buggy.mkdir()
        subprocess.run(
            ["docker", "cp", f"{container_name}:/test_artifacts", str(dest_buggy)],
            capture_output=True,
            check=True,
        )
        # Without '/.' the files end up nested under test_artifacts/
        found_buggy = list(dest_buggy.glob("junit/*.xml"))
        assert len(found_buggy) == 0, (
            "BUG demonstration: without '/.' suffix and pre-existing dest, "
            f"junit/*.xml should NOT be found at top level, but was: {found_buggy}"
        )
        # The files are nested one level deeper
        found_nested = list(dest_buggy.glob("test_artifacts/junit/*.xml"))
        assert len(found_nested) == 1, (
            f"Without '/.' suffix, files should be nested: {list(dest_buggy.rglob('*'))}"
        )

    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
