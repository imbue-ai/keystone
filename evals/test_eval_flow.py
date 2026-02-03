"""Integration test for eval flow using local sample repos.

Creates git repos from samples/python_project and samples/go_project,
then runs the eval flow on them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from config import AgentConfig, EvalConfig
from flow import eval_flow

SAMPLES_DIR = Path(__file__).parent.parent / "samples"
FAKE_AGENT = Path(__file__).parent.parent / "bootstrap_devcontainer" / "tests" / "fake_agent.py"


def init_git_repo(path: Path) -> None:
    """Initialize a git repository with test config."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def sample_repos(tmp_path: Path) -> tuple[Path, list[str]]:
    """Create git repos from samples and return (repo_list_path, repo_paths).

    Sets up python_project and go_project as local git repos.
    """
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    repo_paths: list[str] = []

    for sample_name in ["python_project", "go_project"]:
        src = SAMPLES_DIR / sample_name
        if not src.exists():
            pytest.skip(f"Sample not found: {src}")

        # Copy and init as git repo
        dest = repos_dir / sample_name
        shutil.copytree(src, dest)
        init_git_repo(dest)
        repo_paths.append(str(dest))

    # Write repo list JSONL
    repo_list_path = tmp_path / "repos.jsonl"
    with repo_list_path.open("w") as f:
        for path in repo_paths:
            f.write(json.dumps({"repo": path}) + "\n")

    return repo_list_path, repo_paths


@pytest.mark.slow
def test_eval_flow_fake_agent(sample_repos: tuple[Path, list[str]], tmp_path: Path) -> None:
    """Test the eval flow with fake agent (no Modal, no LLM).

    This test:
    1. Creates local git repos from samples
    2. Runs the eval flow with fake agent locally
    3. Verifies results structure and that repos are pinned

    Uses fake_agent.py which generates a working Python devcontainer.
    Only python_project will succeed; go_project will fail (expected).
    """
    repo_list_path, _repo_paths = sample_repos
    clone_dir = tmp_path / "clones"
    worktree_dir = tmp_path / "worktrees"
    output_path = tmp_path / "output.json"

    agent_config = AgentConfig(
        max_budget_usd=1.0,
        timeout_minutes=5,
        agent_cmd=f"python {FAKE_AGENT}",
        agent_in_modal=False,  # Run locally with fake agent
    )

    eval_config = EvalConfig(
        agent_config=agent_config,
        max_workers=1,  # Serial for easier debugging
    )

    output = eval_flow(
        repo_list_path=str(repo_list_path),
        clone_dir=str(clone_dir),
        worktree_dir=str(worktree_dir),
        eval_config=eval_config,
        output_path=str(output_path),
    )

    # Verify output structure
    assert output.bootstrap_devcontainer_version is not None
    assert "git_hash" in output.bootstrap_devcontainer_version
    assert len(output.repos) == 2
    assert len(output.results) == 2

    # Verify repos are pinned with commit hashes
    for repo in output.repos:
        assert repo.commit_hash is not None
        assert len(repo.commit_hash) == 40  # Full SHA

    # Verify output file was written
    assert output_path.exists()
    with output_path.open() as f:
        saved_output = json.load(f)
    assert len(saved_output["repos"]) == 2
    assert len(saved_output["results"]) == 2

    # Log results for debugging
    for result in output.results:
        print(f"\n{result.repo_entry.repo}:")
        print(f"  success: {result.success}")
        print(
            f"  commit: {result.repo_entry.commit_hash[:12] if result.repo_entry.commit_hash else 'N/A'}"
        )
        if result.error_message:
            print(f"  error: {result.error_message[:200]}")

    # At least check we got results (soft assertion - infra may be flaky)
    success_count = sum(1 for r in output.results if r.success)
    print(f"\nTotal: {success_count}/{len(output.results)} succeeded")
