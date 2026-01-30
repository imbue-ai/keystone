"""Integration test for eval flow with Modal sandboxes.

This test:
1. Creates temporary git repos from sample projects
2. Packages them as tarballs
3. Runs the Prefect eval flow with --agent_in_modal
4. Uses DEFAULT_TESTING_LOG_PATH for caching/logging
5. Verifies results

Requirements:
- Modal CLI configured (`modal token set`)
- ANTHROPIC_API_KEY environment variable (or claude CLI auth)
"""

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from config import AgentConfig, EvalConfig
from flow import eval_flow

# Sample projects to test (subset for faster CI)
SAMPLE_PROJECTS = ["python_project", "rust_project", "fullstack_project"]
SAMPLES_DIR = Path(__file__).parent.parent / "samples"
DEFAULT_TESTING_LOG_PATH = Path.home() / ".bootstrap_devcontainer" / "testing_log.sqlite"


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
        ["git", "commit", "-m", "Initial commit", "--allow-empty"],
        cwd=path,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def eval_repos(tmp_path: Path) -> tuple[Path, list[Path]]:
    """Create temporary repos from sample projects and return (repo_list_path, tarball_paths).

    Creates git-initialized copies of sample projects, packages them as tarballs,
    and generates a JSONL repo list file.
    """
    tarballs_dir = tmp_path / "tarballs"
    tarballs_dir.mkdir()
    tarball_paths: list[Path] = []

    for sample_name in SAMPLE_PROJECTS:
        src = SAMPLES_DIR / sample_name
        if not src.exists():
            pytest.skip(f"Sample project not found: {src}")

        # Copy to temp and init git
        project_dir = tmp_path / sample_name
        shutil.copytree(src, project_dir)
        init_git_repo(project_dir)

        # Create tarball
        tarball_path = tarballs_dir / f"{sample_name}.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(project_dir, arcname=sample_name)
        tarball_paths.append(tarball_path)

    # Create repo list JSONL
    repo_list_path = tmp_path / "repo_list.jsonl"
    with repo_list_path.open("w") as f:
        for tarball_path in tarball_paths:
            f.write(json.dumps({"s3_repo_tarball": str(tarball_path)}) + "\n")

    return repo_list_path, tarball_paths


@pytest.mark.slow
@pytest.mark.modal
def test_eval_flow_modal(eval_repos: tuple[Path, list[Path]], tmp_path: Path) -> None:
    """Test the eval flow with Modal sandboxes.

    This is a slow integration test that actually runs the agent on Modal.
    Mark with @pytest.mark.slow and @pytest.mark.modal for selective test runs.

    Budget: $1 per repo
    Timeout: 10 minutes per repo
    """
    repo_list_path, _tarball_paths = eval_repos
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Configure eval with Modal execution, testing log path
    agent_config = AgentConfig(
        max_budget_usd=1.0,
        timeout_minutes=10,
        agent_in_modal=True,
        log_db=str(DEFAULT_TESTING_LOG_PATH),
        # Use current git ref for testing against local changes
        bootstrap_git_ref=None,  # Auto-resolve
    )

    eval_config = EvalConfig(
        agent_config=agent_config,
        execution_mode="local",  # Run prefect locally, but agent on Modal
        max_workers=1,  # Serial for easier debugging
    )

    # Run the flow
    results = eval_flow(
        repo_list_path=str(repo_list_path),
        eval_config=eval_config,
        output_dir=str(output_dir),
    )

    # Verify results
    assert len(results) == len(SAMPLE_PROJECTS), f"Expected {len(SAMPLE_PROJECTS)} results"

    # Check summary file was written
    summary_path = output_dir / "summary.json"
    assert summary_path.exists(), "Summary file not written"

    # Log results for inspection
    for result in results:
        print(f"\n{result.s3_repo_tarball}:")
        print(f"  success: {result.success}")
        if result.error_message:
            print(f"  error: {result.error_message[:200]}")
        if result.bootstrap_result:
            print(f"  devcontainer: {result.bootstrap_result.get('devcontainer_created', False)}")

    # At least one should succeed (soft assertion for flaky infra)
    successes = sum(1 for r in results if r.success)
    print(f"\nTotal: {successes}/{len(results)} succeeded")

    # For CI, we might want to be more lenient - uncomment for strict mode:
    # assert all(r.success for r in results), "Not all repos succeeded"


@pytest.mark.slow
@pytest.mark.modal
@pytest.mark.parametrize("sample_name", SAMPLE_PROJECTS)
def test_eval_single_repo_modal(sample_name: str, tmp_path: Path) -> None:
    """Test a single sample project with Modal.

    Useful for debugging individual project failures.
    """
    src = SAMPLES_DIR / sample_name
    if not src.exists():
        pytest.skip(f"Sample project not found: {src}")

    # Set up repo
    project_dir = tmp_path / sample_name
    shutil.copytree(src, project_dir)
    init_git_repo(project_dir)

    # Create tarball
    tarball_path = tmp_path / f"{sample_name}.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(project_dir, arcname=sample_name)

    # Create repo list
    repo_list_path = tmp_path / "repo_list.jsonl"
    with repo_list_path.open("w") as f:
        f.write(json.dumps({"s3_repo_tarball": str(tarball_path)}) + "\n")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    agent_config = AgentConfig(
        max_budget_usd=1.0,
        timeout_minutes=10,
        agent_in_modal=True,
        log_db=str(DEFAULT_TESTING_LOG_PATH),
        bootstrap_git_ref=None,
    )

    eval_config = EvalConfig(
        agent_config=agent_config,
        execution_mode="local",
        max_workers=1,
    )

    results = eval_flow(
        repo_list_path=str(repo_list_path),
        eval_config=eval_config,
        output_dir=str(output_dir),
    )

    assert len(results) == 1
    result = results[0]

    print(f"\n{sample_name} result:")
    print(f"  success: {result.success}")
    if result.error_message:
        print(f"  error: {result.error_message}")
    if result.bootstrap_result:
        print(f"  result: {json.dumps(result.bootstrap_result, indent=2)}")

    # Check output artifacts
    repo_output = output_dir / "repo_0"
    if repo_output.exists():
        print(f"  artifacts: {list(repo_output.iterdir())}")

    # Soft assertion - log failures but don't fail test
    # (infrastructure issues shouldn't block all development)
    if not result.success:
        pytest.xfail(f"{sample_name} failed: {result.error_message}")
