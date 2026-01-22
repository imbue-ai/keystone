"""Test the eval harness on samples/python_project.

This test uses caching to avoid repeated agent runs.

Usage:
    cd evals
    uv run pytest test_local_worker.py -v
"""
import subprocess
from pathlib import Path

import pytest

from config import AgentConfig
from flow import create_tarball_from_dir, eval_local_tarball_flow


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
def samples_dir() -> Path:
    """Path to the sample python project."""
    path = Path(__file__).parent.parent / "samples" / "python_project"
    if not path.exists():
        pytest.skip(f"Sample project not found at {path}")
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


def test_eval_local_tarball_flow(samples_dir: Path, tmp_path: Path, git_ref: str) -> None:
    """Test that eval_local_tarball_flow succeeds on the sample project."""
    # Create tarball
    tarball_path = tmp_path / "python_project.tar.gz"
    create_tarball_from_dir(samples_dir, tarball_path)
    
    # Configure with caching enabled, using current git commit
    agent_config = AgentConfig(
        max_budget_usd=1.0,
        use_cache=True,
        timeout_minutes=30,
        bootstrap_git_ref=git_ref,
    )
    
    # Run the flow
    result = eval_local_tarball_flow(
        tarball_path=str(tarball_path),
        agent_config=agent_config,
        output_dir=str(tmp_path / "result"),
    )
    
    # Assert success
    assert result.success, f"Eval failed: {result.error_message}"
    
    # Verify output files exist
    result_dir = tmp_path / "result"
    assert result_dir.exists(), "Result directory not created"
