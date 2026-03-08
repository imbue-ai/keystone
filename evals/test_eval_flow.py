"""Integration test for eval flow using local sample repos.

Creates git repos from samples/python_project and samples/go_project,
then runs the eval flow on them.  Uses file:// URIs for S3 prefixes
so tests don't need real AWS credentials.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from eval_schema import EvalConfig
from flow import eval_flow

from keystone.constants import DEFAULT_TESTING_LOG_PATH
from keystone.schema import AgentConfig, KeystoneConfig

SAMPLES_DIR = Path(__file__).parent.parent / "samples"
# On Modal, fake_claude_agent.py is pre-installed at this path (see keystone/modal/image.py)
FAKE_CLAUDE_AGENT_MODAL = "/usr/local/bin/fake_claude_agent.py"


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

    # Write repo list JSONL (local paths as repos, with unique IDs and commit hashes)
    repo_list_path = tmp_path / "repos.jsonl"
    with repo_list_path.open("w") as f:
        for path in repo_paths:
            repo_id = Path(path).name
            commit_hash = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            f.write(json.dumps({"id": repo_id, "repo": path, "commit_hash": commit_hash}) + "\n")

    return repo_list_path, repo_paths


@pytest.mark.modal
def test_eval_flow_fake_agent(sample_repos: tuple[Path, list[str]], tmp_path: Path) -> None:
    """Test the eval flow with fake agent on Modal (no LLM).

    This test:
    1. Creates local git repos from samples
    2. Runs the eval flow with fake agent on Modal
    3. Verifies results structure and that repos are pinned

    Uses fake_agent.py which generates a working Python devcontainer.
    Only python_project will succeed; go_project will fail (expected).
    """
    repo_list_path, _repo_paths = sample_repos
    s3_output_dir = tmp_path / "s3_output"
    s3_cache_dir = tmp_path / "s3_cache"
    s3_output_dir.mkdir()
    s3_cache_dir.mkdir()

    keystone_config = KeystoneConfig(
        agent_config=AgentConfig(
            max_budget_usd=1.0,
            agent_time_limit_seconds=5 * 60,
            agent_in_modal=True,
            agent_cmd=f"python {FAKE_CLAUDE_AGENT_MODAL}",
            evaluator=True,
            guardrail=True,
            use_agents_md=True,
        ),
    )

    eval_config = EvalConfig(
        name="fake-agent",
        keystone_config=keystone_config,
        s3_output_prefix=s3_output_dir.as_uri() + "/",
    )

    outputs = eval_flow(
        repo_list_path=str(repo_list_path),
        eval_configs=[eval_config],
        s3_repo_cache_prefix=s3_cache_dir.as_uri() + "/",
    )

    assert len(outputs) == 1
    output = outputs[0]

    # Verify output structure
    assert output.keystone_version is not None
    assert output.keystone_version.git_hash is not None
    assert len(output.results) == 2

    # Verify repos are pinned with commit hashes (via results)
    for result in output.results:
        assert result.repo_entry.commit_hash is not None
        assert len(result.repo_entry.commit_hash) == 40  # Full SHA

    # Verify per-repo results were written to "S3" (local filesystem)
    for result in output.results:
        trial_index = result.trial_index if result.trial_index is not None else 0
        repo_output_dir = s3_output_dir / result.repo_entry.id / f"trial_{trial_index}"
        result_file = repo_output_dir / "eval_result.json"
        # Result files may exist even for failures
        if result.success:
            assert result_file.exists(), f"Missing result file for {result.repo_entry.id}"

    # Verify eval summary was written
    summary_file = s3_output_dir / "eval_summary.json"
    assert summary_file.exists()
    with summary_file.open() as f:
        saved_output = json.load(f)
    assert len(saved_output["results"]) == 2

    # Log results for debugging
    for result in output.results:
        print(f"\n{result.repo_entry.id}:")
        print(f"  success: {result.success}")
        print(
            f"  commit: {result.repo_entry.commit_hash[:12] if result.repo_entry.commit_hash else 'N/A'}"
        )
        if result.error_message:
            print(f"  error: {result.error_message[:200]}")

    success_count = sum(1 for r in output.results if r.success)
    print(f"\nTotal: {success_count}/{len(output.results)} succeeded")


@pytest.mark.modal
@pytest.mark.agentic
def test_eval_flow_claude_on_modal(sample_repos: tuple[Path, list[str]], tmp_path: Path) -> None:
    """End-to-end test with real Claude agent on Modal.

    This test:
    1. Creates local git repos from samples (python_project, go_project)
    2. Runs eval flow with real Claude agent on Modal in parallel
    3. Uploads results to local filesystem (file:// URIs)

    Expects both repos to succeed with the real agent.
    """
    repo_list_path, _repo_paths = sample_repos
    s3_output_dir = tmp_path / "s3_output"
    s3_cache_dir = tmp_path / "s3_cache"
    s3_output_dir.mkdir()
    s3_cache_dir.mkdir()

    keystone_config = KeystoneConfig(
        agent_config=AgentConfig(
            max_budget_usd=1.0,
            agent_time_limit_seconds=10 * 60,
            agent_in_modal=True,
            agent_cmd="claude",
            evaluator=True,
            guardrail=True,
            use_agents_md=True,
        ),
        log_db=str(DEFAULT_TESTING_LOG_PATH),
    )

    eval_config = EvalConfig(
        name="modal-test",
        keystone_config=keystone_config,
        s3_output_prefix=s3_output_dir.as_uri() + "/",
    )

    outputs = eval_flow(
        repo_list_path=str(repo_list_path),
        eval_configs=[eval_config],
        s3_repo_cache_prefix=s3_cache_dir.as_uri() + "/",
    )

    assert len(outputs) == 1
    output = outputs[0]

    # Verify output structure
    assert output.keystone_version is not None
    assert output.keystone_version.git_hash is not None
    assert len(output.results) == 2

    # Verify repos are pinned (via results)
    for result in output.results:
        assert result.repo_entry.commit_hash is not None
        assert len(result.repo_entry.commit_hash) == 40

    # Log full output report
    print("\n" + "=" * 60)
    print("EVAL OUTPUT REPORT")
    print("=" * 60)
    print(f"\nS3 output: {s3_output_dir}")
    print(f"\nkeystone version: {output.keystone_version}")

    print("\n" + "-" * 60)
    print("RESULTS:")
    print("-" * 60)
    for i, result in enumerate(output.results):
        status = "✓ SUCCESS" if result.success else "✗ FAILED"
        print(f"\n[{i + 1}] {result.repo_entry.id}: {status}")
        print(
            f"    commit: {result.repo_entry.commit_hash[:12] if result.repo_entry.commit_hash else 'N/A'}"
        )
        if result.error_message:
            print(f"    error: {result.error_message[:300]}")
        if result.bootstrap_result:
            print(f"    bootstrap_result: success={result.bootstrap_result.success}")
            if result.bootstrap_result.verification:
                print(f"    verification: {result.bootstrap_result.verification}")

    success_count = sum(1 for r in output.results if r.success)
    print("\n" + "=" * 60)
    print(f"TOTAL: {success_count}/{len(output.results)} succeeded")
    print("=" * 60)

    # Assert all repos succeeded
    failed = [r for r in output.results if not r.success]
    assert not failed, f"{len(failed)}/{len(output.results)} repos failed: " + ", ".join(
        f"{r.repo_entry.id}: {r.error_message or 'unknown error'}" for r in failed
    )
