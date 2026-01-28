"""Test the eval harness using repos from examples/repo_list.jsonl.

This test uses caching to avoid repeated agent runs.

Usage:
    cd evals
    uv run pytest test_local_worker.py -v
"""

from pathlib import Path

import pytest
from config import AgentConfig, EvalConfig
from flow import eval_flow
from git_utils import GitRepoError, resolve_git_ref


@pytest.fixture
def repo_list_path() -> Path:
    """Path to the repo list JSONL file."""
    path = Path(__file__).parent / "examples" / "repo_list.jsonl"
    if not path.exists():
        pytest.skip(f"Repo list not found at {path}")
    return path


@pytest.fixture
def git_ref() -> str:
    """Get the current git commit, ensuring repo is clean and pushed."""
    try:
        return resolve_git_ref(require_pushed=True)
    except GitRepoError as e:
        pytest.fail(str(e))


def test_eval_flow(repo_list_path: Path, tmp_path: Path, git_ref: str) -> None:
    """Test that eval_flow succeeds on repos from repo_list.jsonl."""
    # Configure with caching enabled, using current git commit
    agent_config = AgentConfig(
        max_budget_usd=1.0,
        timeout_minutes=30,
        bootstrap_git_ref=git_ref,
        # Uses default sqlite_cache_dir="~/.cache/bootstrap_devcontainer"
    )

    eval_config = EvalConfig(
        agent_config=agent_config,
        execution_mode="local",
        max_workers=1,
    )

    output_dir = tmp_path / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run the flow
    results = eval_flow(
        repo_list_path=str(repo_list_path),
        eval_config=eval_config,
        output_dir=str(output_dir),
    )

    # Should have processed repos from the list
    assert len(results) > 0, "No results returned"

    # At least one should succeed (may vary based on repo state)
    success_count = sum(1 for r in results if r.success)
    assert success_count > 0, f"No repos succeeded: {[r.error_message for r in results]}"

    # Verify output structure
    assert (output_dir / "summary.json").exists(), "Summary file not created"

    # Check successful results have expected fields
    for result in results:
        if result.success:
            assert result.bootstrap_result is not None, "bootstrap_result should be populated"
            br = result.bootstrap_result
            assert br.get("success") is True, f"bootstrap_result.success should be True: {br}"
            assert "agent_work_seconds" in br, "bootstrap_result should have agent_work_seconds"
            assert "cost_usd" in br, "bootstrap_result should have cost_usd"
            assert "token_spending" in br, "bootstrap_result should have token_spending"
            assert result.devcontainer_tarball_s3 is not None, "devcontainer tarball should exist"
