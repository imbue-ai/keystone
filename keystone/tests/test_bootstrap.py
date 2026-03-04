import json
import logging
import shlex
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from conftest import SAMPLES_DIR, init_git_repo
from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

from keystone.constants import DEFAULT_TESTING_LOG_PATH
from keystone.keystone_cli import app
from keystone.process_runner import run_process
from keystone.schema import BootstrapResult
from keystone.version import _UNKNOWN_VERSION, get_version_info

logger = logging.getLogger(__name__)


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "[OPTIONS]" in result.stdout
    assert "--project_root" in result.stdout


@pytest.mark.manual
def test_cli_help_runs_from_uvx() -> None:
    """Test that the CLI can be installed and invoked via uvx from the public repo.

    The uvx command tested here is the one documented in the README:
        uvx --from 'git+https://github.com/imbue-ai/keystone@prod' keystone --help

    Marked manual because it requires network access and installs from git.
    """
    result = run_process(
        [
            "uvx",
            "--from",
            "git+https://github.com/imbue-ai/keystone@prod",
            "keystone",
            "--help",
        ],
        log_prefix="[uvx run]",
    )
    assert result.returncode == 0, f"uvx invocation failed:\n{result.stderr}"
    assert "--project_root" in result.stdout


def test_get_version_info_without_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When CWD is not a git repo and no stamp file exists, get_version_info returns a fallback.

    This simulates the uvx-from-github scenario where the process runs outside
    any git repository and there is no baked-in version_stamp.json.
    """
    # Clear the @cache so we get a fresh call
    get_version_info.cache_clear()

    # Run from a directory that is not a git repo
    monkeypatch.chdir(tmp_path)

    result = get_version_info()
    assert result == _UNKNOWN_VERSION
    assert result.git_hash is None
    assert result.branch is None
    assert result.commit_timestamp is None

    # Clean up cache so other tests aren't affected
    get_version_info.cache_clear()


def test_cli_does_not_crash_in_empty_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI must not crash when --project_root is an empty git repo (no commits).

    This reproduces the bug seen when running via uvx:
        uvx --from 'git+https://github.com/imbue-ai/keystone@prod' keystone \\
            --project_root <empty-repo> --test_artifacts_dir <dir>

    In that scenario the CWD may be inside a git repo with no commits, causing
    ``git rev-parse HEAD`` to fail with exit code 128.  The version fallback
    chain must catch this and continue.
    """
    # Create an empty git repo (init but no commits)
    empty_repo = tmp_path / "empty_git_repo"
    empty_repo.mkdir()
    init_git_repo(empty_repo, add_all=False, commit=False)

    test_artifacts_dir = tmp_path / "test_artifacts"

    # Simulate uvx environment: CWD is the empty repo, no stamp file
    monkeypatch.chdir(empty_repo)
    get_version_info.cache_clear()

    try:
        cmd = [
            "--project_root",
            str(empty_repo),
            "--test_artifacts_dir",
            str(test_artifacts_dir),
            "--max_budget_usd",
            "0",
            "--run_agent_locally_with_dangerously_skip_permissions",
        ]
        result = CliRunner().invoke(app, cmd)

        # The CLI will fail because the empty repo has no devcontainer config etc.,
        # but it must NOT crash with CalledProcessError from git rev-parse HEAD.
        # Exit code 1 (controlled failure) is fine; a traceback with
        # CalledProcessError from version.py is not.
        assert "CalledProcessError" not in (result.stdout + (result.stderr or "")), (
            f"CLI crashed with CalledProcessError (version resolution bug):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    finally:
        get_version_info.cache_clear()


def test_get_version_info_from_direct_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate uvx installing from a git URL: no local git, but PEP 610 metadata exists.

    uv/pip write a direct_url.json into .dist-info with the resolved commit_id
    when installing from ``git+https://…@branch``.  We verify that
    get_version_info picks up the hash and branch from that metadata.
    """
    get_version_info.cache_clear()
    monkeypatch.chdir(tmp_path)

    fake_direct_url = json.dumps(
        {
            "url": "https://github.com/imbue-ai/keystone.git",
            "vcs_info": {
                "vcs": "git",
                "requested_revision": "prod",
                "commit_id": "4dba85a1880214f687d1779e75d06440cc4bb9ef",
            },
        }
    )

    mock_dist = MagicMock()
    mock_dist.read_text.return_value = fake_direct_url

    with patch("keystone.version.importlib.metadata.distribution", return_value=mock_dist):
        result = get_version_info()

    assert result.git_hash == "4dba85a1880214f687d1779e75d06440cc4bb9ef"
    assert result.branch == "prod"
    assert result.is_dirty is False
    assert result.commit_timestamp is None

    get_version_info.cache_clear()


@pytest.mark.parametrize(
    "execution_mode",
    [
        pytest.param("local", id="local"),
        pytest.param(
            "modal",
            id="modal",
            marks=pytest.mark.manual,
        ),
    ],
)
def test_e2e_fake_agent(
    tmp_path: Path, project_root: Path, execution_mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Test the full Docker mechanics using a deterministic fake agent.
    This tests the devcontainer build and test execution without LLM dependencies.

    Parameterized to run both locally (--run_agent_locally_with_dangerously_skip_permissions)
    and on Modal (--agent_in_modal with --docker_registry_mirror).
    """
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


@pytest.mark.parametrize("project_root", ["rust_project"], indirect=True)
def test_e2e_fake_agent_fails_on_rust_project(tmp_path: Path, project_root: Path) -> None:
    """
    Test that the fake agent (which generates Python devcontainer) fails
    when used against a Rust project, demonstrating proper failure detection.
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
        "--run_agent_locally_with_dangerously_skip_permissions",  # Use local runner for fake agent tests
    ]

    logger.info("Running: keystone %s", " ".join(cmd))

    result = CliRunner().invoke(app, cmd)

    logger.info("Exit code: %s", result.exit_code)

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
        assert result.exit_code != 0 or not output.success, (
            "Expected failure: Python devcontainer cannot run Rust tests"
        )
    else:
        # If we can't parse JSON, the process must have failed
        assert result.exit_code != 0, "Expected failure: Python devcontainer cannot run Rust tests"

    # Verify the devcontainer was created (agent ran successfully)
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()


@pytest.mark.manual
@pytest.mark.parametrize("project_root", ["python_project"], indirect=True)
def test_e2e_codex_on_modal(tmp_path: Path, project_root: Path) -> None:
    """E2E test: run the real Codex provider on Modal against python_project.

    Verifies that OPENAI_API_KEY is forwarded into the Modal sandbox and
    that codex can authenticate and produce a working devcontainer.

    Requires OPENAI_API_KEY in the environment and Modal credentials configured.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"
    cache_file = tmp_path / "codex_modal_cache.sqlite"

    logger.info("=" * 60)
    logger.info("E2E Test: Codex on Modal")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--log_db",
        str(cache_file),
        "--provider",
        "codex",
        "--agent_in_modal",
        "--docker_registry_mirror",
        "https://mirror.gcr.io",
        "--no_cache_replay",
    ]

    logger.info("Running: keystone %s", " ".join(cmd))
    result = CliRunner().invoke(app, cmd)

    output = _parse_bootstrap_result(result.stdout)

    assert result.exit_code == 0, (
        f"Codex on Modal failed (exit {result.exit_code}):\nerror: {output.error_message}"
    )
    assert output.success, f"Bootstrap failed: {output.error_message}"

    # Verify devcontainer files were created
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()

    # Verify test artifacts were extracted
    assert (test_artifacts_dir / "junit").exists(), "Expected junit test artifacts"

    # Verify verification passed with actual test results
    assert output.verification is not None
    assert output.verification.success, f"Verification failed: {output.verification.error_message}"

    # Verify ccusage cost reporting (only on fresh runs — cached replays don't have cost data)
    cost = output.agent.cost
    if cost.ccusage_raw is not None:
        # Fresh run: ccusage should have reported real cost data
        assert cost.cost_usd > 0, f"ccusage should report non-zero cost: {cost}"
        ts = cost.token_spending
        assert ts.input > 0 or ts.cached > 0, f"ccusage should report input tokens: {ts}"
        assert ts.output > 0, f"ccusage should report output tokens: {ts}"
    else:
        logger.warning("Skipping ccusage cost assertions (likely a cached replay)")


@pytest.mark.manual
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

    output = _parse_bootstrap_result(result.stdout)

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


@pytest.mark.manual
@pytest.mark.parametrize(
    "project_root",
    [
        "python_project",
        #        "node_project",
        #        "go_project",
        #        "rust_project",
        # "fullstack_project",
        "python_with_failing_test",
        # "cmake_vcpkg_project",
    ],
    indirect=True,
)
def test_e2e_sample_projects(
    tmp_path: Path,
    project_root: Path,
    snapshot: SnapshotAssertion,
    request: pytest.FixtureRequest,
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
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--log_db",
        str(cache_file),
    ]

    logger.info("Running: keystone %s", " ".join(cmd))

    result = CliRunner().invoke(app, cmd)

    sample_name = request.node.callspec.params["project_root"]
    if "failing" in sample_name:
        assert result.exit_code != 0, "Expected failure for failing project"
    else:
        assert result.exit_code == 0, f"{sample_name} failed with exit code {result.exit_code}"

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

    # Validate status messages
    _validate_status_messages(output)

    # Verify ccusage cost reporting (only on fresh runs — cached replays don't have cost data)
    cost = output.agent.cost
    if cost.ccusage_raw is not None:
        # Fresh run: ccusage should have reported real cost data
        assert cost.cost_usd > 0, f"ccusage should report non-zero cost: {cost}"
        ts = cost.token_spending
        assert ts.input > 0 or ts.cached > 0, f"ccusage should report input tokens: {ts}"
        assert ts.output > 0, f"ccusage should report output tokens: {ts}"
    else:
        logger.warning("Skipping ccusage cost assertions (likely a cached replay)")

    # Snapshot test - strip non-deterministic fields
    snapshot_data = _strip_nondeterministic_fields(output)
    assert snapshot_data == snapshot


def _parse_bootstrap_result(stdout: str) -> BootstrapResult:
    """Extract and parse the JSON BootstrapResult from CLI stdout."""
    stdout_lines = stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break
    assert json_start is not None, "Could not find JSON output in stdout"
    json_str = "\n".join(stdout_lines[json_start:])
    return BootstrapResult.model_validate_json(json_str)


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

    # Cost data is now computed post-hoc via ccusage (only available on Modal).
    # For local/test runs, cost will be zero — no assertion on cost values here.


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
    # Remove CLI args (they include pytest flags that vary between runs)
    result.pop("cli_args", None)
    # Remove evaluator result (LLM output is non-deterministic)
    result.pop("evaluator", None)
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
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--max_budget_usd",
        "0",
        "--run_agent_locally_with_dangerously_skip_permissions",  # Use local runner (budget test uses real claude locally)
    ]

    logger.info("Running: keystone %s", " ".join(cmd))

    result = CliRunner().invoke(app, cmd)

    logger.info("Exit code: %s", result.exit_code)

    # CLI should return non-zero exit code on failure
    assert result.exit_code != 0, "Expected non-zero exit code with zero budget"

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


@pytest.mark.skipif(
    not shutil.which("timeout"), reason="GNU timeout not available (install coreutils)"
)
def test_agent_time_limit_causes_timeout(tmp_path: Path, project_root: Path) -> None:
    """
    Test that setting a very short --agent_time_limit_seconds causes timeout.
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
