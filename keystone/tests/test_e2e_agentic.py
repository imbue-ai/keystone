"""End-to-end tests using real LLM agents (non-deterministic).

These tests run actual coding agents (Claude, Codex) on Modal and verify the
full bootstrap pipeline. They are marked @agentic because the agent output
varies between runs; snapshot tests use soft-field matching so only structural
outcomes (success, exit_code, verification results) are enforced.

Markers:
  - modal: all tests here run on Modal
  - agentic: all tests here invoke a real LLM agent
"""

import logging
import os
from pathlib import Path

import pytest
from conftest import parse_bootstrap_result
from snapshot_ext import SoftAmberExtension
from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

from keystone.constants import DEFAULT_TESTING_LOG_PATH
from keystone.keystone_cli import app
from keystone.schema import BootstrapResult

logger = logging.getLogger(__name__)


class BootstrapSnapshotExtension(SoftAmberExtension):
    """Snapshot extension for agentic e2e tests.

    Soft fields are stored in the snapshot for eyeballing diffs but do not
    cause assertion failures if they change between runs.
    """

    soft_fields = frozenset(
        {
            # Timing
            "agent.duration_seconds",
            "agent.start_time",
            "agent.end_time",
            # Cost
            "agent.cost",
            # LLM-generated content (non-deterministic across runs)
            "agent.status_messages",
            "agent.summary",
            "agent.error_messages",
            "agent.model",
            "generated_files",
            # Environment-specific
            "cli_args",
            # Verification timing
            "verification.image_build_seconds",
            "verification.test_execution_seconds",
        }
    )


def _validate_status_messages(output: BootstrapResult) -> None:
    """Validate that status messages have increasing timestamps."""
    if not output.agent.status_messages:
        return

    prev_ts = None
    for msg in output.agent.status_messages:
        if prev_ts is not None:
            assert msg.timestamp >= prev_ts, (
                f"Timestamps should be non-decreasing: {prev_ts} -> {msg.timestamp}"
            )
        prev_ts = msg.timestamp


@pytest.mark.modal
@pytest.mark.agentic
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
        "--model",
        "gpt-5.3-codex",
        "--agent_in_modal",
        "--docker_registry_mirror",
        os.environ["DOCKER_REGISTRY_MIRROR"],
        "--no_cache_replay",
    ]

    logger.info("Running: keystone %s", " ".join(cmd))
    result = CliRunner().invoke(app, cmd)

    output = parse_bootstrap_result(result.stdout)

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
        assert cost.cost_usd > 0, f"ccusage should report non-zero cost: {cost}"
        ts = cost.token_spending
        assert ts.input > 0 or ts.cached > 0, f"ccusage should report input tokens: {ts}"
        assert ts.output > 0, f"ccusage should report output tokens: {ts}"
    else:
        logger.warning("Skipping ccusage cost assertions (likely a cached replay)")


@pytest.mark.modal
@pytest.mark.agentic
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
def test_e2e_claude_on_modal_sample_projects(
    tmp_path: Path,
    project_root: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """E2E test: run real Claude agent on sample projects, snapshot the outcome.

    Soft-field snapshot matching means timing, cost, LLM-generated file content,
    and status messages are stored for eyeballing but don't cause failures.
    Only structural outcomes are enforced: success, exit_code, timed_out,
    verification.success, and verification.test_results.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"
    cache_file = DEFAULT_TESTING_LOG_PATH

    logger.info("=" * 60)
    logger.info("E2E Test Starting")
    logger.info("Project root: %s", project_root)
    logger.info("Test artifacts dir: %s", test_artifacts_dir)
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
    ]

    logger.info("Running: keystone %s", " ".join(cmd))

    result = CliRunner().invoke(app, cmd)

    # Don't assert on exit_code for agentic tests — a smart agent may fix or
    # disable failing tests, turning an expected failure into a success.
    # The snapshot captures the actual outcome for human review.

    # Surface CLI crashes before attempting to parse JSON output
    if result.exception and not isinstance(result.exception, SystemExit):
        logger.error("CLI raised an exception:\n%s", result.exception)
        raise result.exception

    output = parse_bootstrap_result(result.stdout)

    # Check if .devcontainer was created
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()

    # Validate status messages
    _validate_status_messages(output)

    # Verify ccusage cost reporting (only on fresh runs — cached replays don't have cost data)
    cost = output.agent.cost
    if cost.ccusage_raw is not None:
        assert cost.cost_usd > 0, f"ccusage should report non-zero cost: {cost}"
        ts = cost.token_spending
        assert ts.input > 0 or ts.cached > 0, f"ccusage should report input tokens: {ts}"
        assert ts.output > 0, f"ccusage should report output tokens: {ts}"
    else:
        logger.warning("Skipping ccusage cost assertions (likely a cached replay)")

    # Snapshot test — full data stored, soft fields ignored during comparison
    assert output.model_dump() == snapshot(extension_class=BootstrapSnapshotExtension)
