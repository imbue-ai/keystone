"""Test the eval harness using repos from examples/repo_list.jsonl.

This test uses caching to avoid repeated agent runs.

Usage:
    cd evals
    uv run pytest test_local_worker.py -v
"""
import subprocess
from pathlib import Path

import pytest

from config import AgentConfig, EvalConfig
from flow import eval_flow


def get_git_info() -> tuple[str, bool]:
    """Get current git commit hash and check if repo is clean.
    
    Returns:
        (commit_hash, is_clean) tuple
    """
    repo_root = Path(__file__).parent.parent
    
    # Get current commit hash
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    commit_hash = result.stdout.strip()
    
    # Check if repo is clean
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    is_clean = result.stdout.strip() == ""
    
    return commit_hash, is_clean


def check_commit_pushed(commit_hash: str) -> bool:
    """Check if commit exists on origin/main."""
    repo_root = Path(__file__).parent.parent
    
    # Fetch latest from origin
    subprocess.run(
        ["git", "fetch", "origin", "main"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    
    # Check if commit is ancestor of origin/main
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit_hash, "origin/main"],
        cwd=repo_root,
        capture_output=True,
    )
    return result.returncode == 0


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
    commit_hash, is_clean = get_git_info()
    
    if not is_clean:
        pytest.fail("Git repo has uncommitted changes. Commit and push before running eval tests.")
    
    if not check_commit_pushed(commit_hash):
        pytest.fail(f"Commit {commit_hash[:8]} not pushed to origin/main. Push before running eval tests.")
    
    return commit_hash


def test_eval_flow(repo_list_path: Path, tmp_path: Path, git_ref: str) -> None:
    """Test that eval_flow succeeds on repos from repo_list.jsonl."""
    # Configure with caching enabled, using current git commit
    agent_config = AgentConfig(
        max_budget_usd=1.0,
        use_cache=True,
        timeout_minutes=30,
        bootstrap_git_ref=git_ref,
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
            assert "total_time" in br, "bootstrap_result should have total_time"
            assert "cost_usd" in br, "bootstrap_result should have cost_usd"
            assert "token_spending" in br, "bootstrap_result should have token_spending"
            assert result.devcontainer_tarball_s3 is not None, "devcontainer tarball should exist"
