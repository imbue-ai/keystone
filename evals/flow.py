"""Prefect flow for eval harness.

Clones repos, creates worktrees, and runs keystone CLI on each.
"""

import json
import logging
import shutil
import subprocess
import traceback
from pathlib import Path

from config import AgentConfig, EvalConfig, EvalOutput, RepoEntry, RepoResult, resolve_path
from prefect import flow, get_run_logger, task
from prefect.futures import wait
from prefect.tasks import task_input_hash

from keystone.process_runner import run_process
from keystone.version import get_version_info

logger = logging.getLogger(__name__)


def _run_git(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a git command."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


@task(
    name="clone_repo",
    description="Clone a repository to local cache",
    cache_key_fn=task_input_hash,
    cache_expiration=None,  # Never expire - repos are immutable at a given URL
)
def clone_repo_task(
    repo_url: str,
    clone_dir: Path,
) -> tuple[Path, str]:
    """Clone a repo to the cache directory.

    Returns (repo_path, commit_hash).
    Prefect caches this based on (repo_url, clone_dir).
    """
    log = get_run_logger()

    # Derive a directory name from the repo URL
    # e.g., https://github.com/psf/requests -> psf_requests
    repo_name = repo_url.rstrip("/").split("/")[-2] + "_" + repo_url.rstrip("/").split("/")[-1]
    repo_name = repo_name.replace(".git", "")
    repo_path = clone_dir / repo_name

    if repo_path.exists():
        # Already cloned, just fetch latest and get HEAD
        log.info(f"Repo already exists at {repo_path}, fetching...")
        _run_git(["fetch", "--all"], cwd=repo_path)
    else:
        # Clone fresh
        log.info(f"Cloning {repo_url} to {repo_path}")
        clone_dir.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", repo_url, str(repo_path)])

    # Get current HEAD commit
    result = _run_git(["rev-parse", "HEAD"], cwd=repo_path)
    commit_hash = result.stdout.strip()
    log.info(f"Repo {repo_url} at commit {commit_hash[:12]}")

    return repo_path, commit_hash


def _process_repo_task_name(parameters: dict[str, object]) -> str:
    """Derive a human-friendly task-run name from the repo URL."""
    repo_entry: RepoEntry = parameters["repo_entry"]  # type: ignore[assignment]
    short_name = repo_entry.repo.rstrip("/").split("/")[-1].replace(".git", "")
    return f"process_repo/{short_name}"


@task(
    name="process_repo",
    task_run_name=_process_repo_task_name,  # type: ignore[reportArgumentType]  # prefect resolves callback params at runtime
    description="Run keystone on a repo worktree",
    retries=1,
    retry_delay_seconds=60,
)
def process_repo_task(
    repo_entry: RepoEntry,
    clone_dir: Path,
    worktree_dir: Path,
    agent_config: AgentConfig,
) -> RepoResult:
    """Process a single repo.

    1. Clone/fetch repo to clone_dir (cached)
    2. Create worktree in worktree_dir
    3. Run keystone CLI
    4. Return result
    """
    log = get_run_logger()
    repo_url = repo_entry.repo

    try:
        # Step 1: Clone (cached by Prefect)
        repo_path, commit_hash = clone_repo_task.fn(repo_url, clone_dir)
        repo_entry.commit_hash = commit_hash

        # Step 2: Create worktree
        # Derive worktree name from repo
        repo_name = repo_path.name
        work_path = worktree_dir / f"{repo_name}_{commit_hash[:8]}"

        if work_path.exists():
            # Clean up existing worktree
            shutil.rmtree(work_path, ignore_errors=True)

        # Prune stale worktree records (e.g., from deleted directories)
        _run_git(["worktree", "prune"], cwd=repo_path)

        work_path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", str(work_path), "HEAD"], cwd=repo_path)
        log.info(f"Created worktree at {work_path}")

        # Step 3: Run CLI
        test_artifacts_dir = work_path / ".keystone_artifacts"
        test_artifacts_dir.mkdir(exist_ok=True)

        result_file = work_path / "keystone_result.json"

        cmd = [
            "uv",
            "run",
            "keystone",
            "--project_root",
            str(work_path),
            "--test_artifacts_dir",
            str(test_artifacts_dir),
            "--max_budget_usd",
            str(agent_config.max_budget_usd),
            "--output_file",
            str(result_file),
            "--agent_time_limit_secs",
            str(agent_config.timeout_minutes * 60),
            "--agent_cmd",
            agent_config.agent_cmd,
        ]

        if agent_config.agent_in_modal:
            cmd.append("--agent_in_modal")
        else:
            cmd.append("--agent_local")

        if agent_config.log_db:
            cmd.extend(["--log_db", str(resolve_path(agent_config.log_db))])

        if agent_config.require_cache_hit:
            cmd.append("--require_cache_hit")

        if agent_config.no_cache_replay:
            cmd.append("--no_cache_replay")

        if agent_config.docker_cache_secret:
            cmd.extend(["--docker_cache_secret", agent_config.docker_cache_secret])

        log.info(f"Running: {' '.join(cmd[:8])}...")

        # Use streaming process runner to forward CLI output in real-time
        # IMPORTANT: Run from our repo root, NOT the target repo's worktree.
        # If cwd is the target repo, `uv run` sees that repo's pyproject.toml and tries to
        # create a venv and install its dependencies locally (e.g. compiling pytorch).
        # The --project_root CLI arg already tells keystone where the target is.
        repo_name = repo_path.name

        # Forward all CLI stdout/stderr to the Prefect logger so it appears
        # on the dashboard (print() alone doesn't show up there).
        def _log_stdout(line: str) -> None:
            log.info(f"[{repo_name}] {line}")

        def _log_stderr(line: str) -> None:
            log.warning(f"[{repo_name}] {line}")

        proc = run_process(
            cmd,
            log_prefix=f"[{repo_name}]",
            stdout_callback=_log_stdout,
            stderr_callback=_log_stderr,
        )

        # Step 4: Parse result
        bootstrap_result = None
        if result_file.exists():
            try:
                bootstrap_result = json.loads(result_file.read_text())
            except json.JSONDecodeError:
                log.warning(f"Failed to parse {result_file}")

        success = proc.returncode == 0

        if not success:
            log.error(f"CLI failed: {proc.stderr[:500]}")

        return RepoResult(
            repo_entry=repo_entry,
            success=success,
            error_message=proc.stderr[:1000] if not success else None,
            bootstrap_result=bootstrap_result,
        )

    except subprocess.TimeoutExpired:
        return RepoResult(
            repo_entry=repo_entry,
            success=False,
            error_message=f"Timeout after {agent_config.timeout_minutes} minutes",
        )
    except Exception as e:
        return RepoResult(
            repo_entry=repo_entry,
            success=False,
            error_message=f"{e}\n{traceback.format_exc()}",
        )
    finally:
        # Clean up worktree (but keep the clone)
        # TODO: Re-enable worktree cleanup after debugging
        # if "work_path" in locals() and work_path.exists():
        #     try:
        #         _run_git(
        #             ["worktree", "remove", "--force", str(work_path)], cwd=repo_path, check=False
        #         )
        #     except Exception:
        #         shutil.rmtree(work_path, ignore_errors=True)
        pass


@flow(name="eval_keystone")
def eval_flow(
    repo_list_path: str,
    clone_dir: str,
    worktree_dir: str,
    eval_config: EvalConfig,
    output_path: str | None = None,
    limit: int | None = None,
) -> EvalOutput:
    """Main evaluation flow.

    Args:
        repo_list_path: Path to JSONL file with repo entries
        clone_dir: Directory for pristine repo clones (cached)
        worktree_dir: Directory for worktrees
        eval_config: Evaluation configuration
        output_path: Optional path to write JSON output
        limit: Optional limit on number of repos to process

    Returns:
        EvalOutput with version info, pinned repos, and results
    """
    log = get_run_logger()

    # Resolve paths
    clone_path = resolve_path(clone_dir)
    worktree_path = resolve_path(worktree_dir)
    clone_path.mkdir(parents=True, exist_ok=True)
    worktree_path.mkdir(parents=True, exist_ok=True)

    # Load repo list
    repos: list[RepoEntry] = []
    with Path(repo_list_path).open() as f:
        for line in f:
            line = line.strip()
            if line:
                repos.append(RepoEntry(**json.loads(line)))

    log.info(f"Loaded {len(repos)} repos from {repo_list_path}")

    # Apply limit if specified
    if limit is not None:
        repos = repos[:limit]
        log.info(f"Limited to first {limit} repos")

    # Submit all tasks
    futures = []
    for repo_entry in repos:
        future = process_repo_task.submit(
            repo_entry=repo_entry,
            clone_dir=clone_path,
            worktree_dir=worktree_path,
            agent_config=eval_config.agent_config,
        )
        futures.append(future)

    # Wait and collect
    wait(futures)
    results = [f.result() for f in futures]

    # Build output
    version_info = get_version_info()
    pinned_repos = [r.repo_entry for r in results]

    output = EvalOutput(
        keystone_version=version_info.model_dump(),
        repos=pinned_repos,
        results=results,
    )

    # Write output
    if output_path:
        out_path = resolve_path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump(output.model_dump(), f, indent=2)
        log.info(f"Wrote output to {out_path}")

    success_count = sum(1 for r in results if r.success)
    log.info(f"Complete: {success_count}/{len(results)} succeeded")

    return output
