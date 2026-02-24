"""Prefect flow for eval harness.

Clones repos to S3 tarballs, then runs keystone on each.
Per-repo results are uploaded to S3 as they complete.
"""

import contextlib
import json
import logging
import subprocess
import tempfile
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

import fsspec
from config import EvalConfig, EvalOutput, RepoEntry, RepoResult, resolve_path
from prefect import flow, get_run_logger, task
from prefect.futures import PrefectFuture, wait

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


def _s3_exists(path: str) -> bool:
    """Check if an S3 path exists using fsspec."""
    fs, _ = fsspec.core.url_to_fs(path)
    return fs.exists(path)  # type: ignore[no-any-return]


def _s3_write_bytes(path: str, data: bytes) -> None:
    """Write bytes to an S3 path using fsspec."""
    with fsspec.open(path, "wb") as f:
        f.write(data)  # type: ignore[union-attr]


def _s3_write_text(path: str, text: str) -> None:
    """Write text to an S3 path using fsspec."""
    with fsspec.open(path, "w") as f:
        f.write(text)  # type: ignore[union-attr]


def _s3_read_bytes(path: str) -> bytes:
    """Read bytes from an S3 path using fsspec."""
    with fsspec.open(path, "rb") as f:
        return f.read()  # type: ignore[union-attr]


def _tarball_cache_key(
    context: object,  # noqa: ARG001
    parameters: dict[str, Any],
) -> str | None:
    """Cache key for archive_repo_task: just the repo URL.

    The tarball is immutable once created (pinned at clone-time HEAD).
    To re-snapshot a repo, bump the cache_prefix or clear Prefect result cache.
    """
    repo_entry: RepoEntry = parameters["repo_entry"]  # type: ignore[assignment]
    cache_prefix: str = parameters["s3_cache_prefix"]  # type: ignore[assignment]
    return f"archive_repo:{cache_prefix}:{repo_entry.repo}"


@task(
    name="archive_repo",
    description="Clone a repo and upload a git archive tarball to S3",
    cache_key_fn=_tarball_cache_key,
    cache_expiration=None,  # Never expire - tarballs are immutable
)
def archive_repo_task(
    repo_entry: RepoEntry,
    s3_cache_prefix: str,
) -> tuple[str, str]:
    """Clone a repo, create a git archive tarball, upload to S3.

    Returns (s3_tarball_path, commit_hash).
    Prefect caches this based on (repo_url, s3_cache_prefix).
    """
    log = get_run_logger()
    repo_url = repo_entry.repo
    repo_id = repo_entry.id

    s3_tarball_path = f"{s3_cache_prefix.rstrip('/')}/{repo_id}.tar.gz"

    # Check if tarball already exists on S3
    if _s3_exists(s3_tarball_path):
        log.info(f"Tarball already exists at {s3_tarball_path}, skipping clone")
        # We need the commit hash — store it alongside the tarball
        meta_path = f"{s3_cache_prefix.rstrip('/')}/{repo_id}.commit"
        with fsspec.open(meta_path, "r") as f:
            commit_hash = f.read().strip()  # type: ignore[union-attr]
        return s3_tarball_path, commit_hash

    with tempfile.TemporaryDirectory() as tmp_dir:
        clone_path = Path(tmp_dir) / repo_id
        log.info(f"Cloning {repo_url}...")
        _run_git(["clone", "--depth=1", repo_url, str(clone_path)])

        # Get HEAD commit
        result = _run_git(["rev-parse", "HEAD"], cwd=clone_path)
        commit_hash = result.stdout.strip()
        log.info(f"{repo_id} at commit {commit_hash[:12]}")

        # Create git archive tarball
        archive_result = subprocess.run(
            ["git", "archive", "--format=tar.gz", "-o", str(clone_path / "archive.tar.gz"), "HEAD"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            check=True,
        )
        if archive_result.returncode != 0:
            raise RuntimeError(f"git archive failed: {archive_result.stderr}")

        tarball_bytes = (clone_path / "archive.tar.gz").read_bytes()

        # Upload tarball + commit hash to S3
        log.info(f"Uploading {len(tarball_bytes)} bytes to {s3_tarball_path}")
        _s3_write_bytes(s3_tarball_path, tarball_bytes)
        _s3_write_text(
            f"{s3_cache_prefix.rstrip('/')}/{repo_id}.commit",
            commit_hash,
        )

    return s3_tarball_path, commit_hash


def _process_repo_task_name(parameters: dict[str, object]) -> str:
    """Derive a human-friendly task-run name from the repo id."""
    repo_entry: RepoEntry = parameters["repo_entry"]  # type: ignore[assignment]
    return f"process_repo/{repo_entry.id}"


@task(
    name="process_repo",
    task_run_name=_process_repo_task_name,  # type: ignore[reportArgumentType]
    description="Run keystone on a repo",
    retries=0,
)
def process_repo_task(
    repo_entry: RepoEntry,
    s3_tarball_path: str,
    commit_hash: str,
    eval_config: EvalConfig,
    trial: int | None = None,
) -> RepoResult:
    """Process a single repo.

    1. Download tarball from S3
    2. Extract to temp dir, init git
    3. Run keystone CLI
    4. Upload result to S3
    5. Return result
    """
    log = get_run_logger()
    repo_id = repo_entry.id
    agent_config = eval_config.agent_config
    s3_output_prefix = eval_config.s3_output_prefix.rstrip("/")
    # {prefix}/{repo_id}/ for single-trial, {prefix}/{repo_id}/trial_{n}/ for multi-trial
    repo_output_prefix = f"{s3_output_prefix}/{repo_id}"
    if trial is not None:
        repo_output_prefix = f"{repo_output_prefix}/trial_{trial}"

    repo_entry.commit_hash = commit_hash

    # Skip if result already exists on S3 (enables resuming partial runs)
    existing_result_path = f"{repo_output_prefix}/eval_result.json"
    if _s3_exists(existing_result_path):
        log.info(f"[{repo_id}] Result already exists at {existing_result_path}, skipping")
        try:
            existing_bytes = _s3_read_bytes(existing_result_path)
            existing_data = json.loads(existing_bytes)
            return RepoResult(**existing_data)
        except Exception as e:
            log.warning(f"[{repo_id}] Failed to parse existing result, re-running: {e}")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_path = Path(tmp_dir) / repo_id

            # Step 1: Download and extract tarball
            log.info(f"[{repo_id}] Downloading tarball from {s3_tarball_path}")
            tarball_bytes = _s3_read_bytes(s3_tarball_path)
            tarball_file = Path(tmp_dir) / "archive.tar.gz"
            tarball_file.write_bytes(tarball_bytes)

            work_path.mkdir()
            subprocess.run(
                ["tar", "xzf", str(tarball_file), "-C", str(work_path)],
                check=True,
            )

            # Init as git repo (keystone requires it)
            _run_git(["init"], cwd=work_path)
            _run_git(["add", "-A"], cwd=work_path)
            _run_git(
                [
                    "-c",
                    "user.name=eval",
                    "-c",
                    "user.email=eval@eval",
                    "commit",
                    "-m",
                    "Initial commit",
                ],
                cwd=work_path,
            )

            # Step 2: Run keystone CLI
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
                "--agent_time_limit_seconds",
                str(agent_config.timeout_minutes * 60),
                "--agent_cmd",
                agent_config.agent_cmd,
                "--provider",
                agent_config.provider,
            ]

            if agent_config.run_agent_locally:
                cmd.append("--run_agent_locally_with_dangerously_skip_permissions")
            else:
                cmd.extend(
                    [
                        "--agent_in_modal",
                        "--docker_cache_secret",
                        agent_config.docker_cache_secret,
                    ]
                )

            if agent_config.model is not None:
                cmd.extend(["--model", agent_config.model.value])
            if agent_config.log_db:
                cmd.extend(["--log_db", str(resolve_path(agent_config.log_db))])
            if agent_config.require_cache_hit:
                cmd.append("--require_cache_hit")
            if agent_config.no_cache_replay:
                cmd.append("--no_cache_replay")

            log.info(f"[{repo_id}] Running keystone...")

            # Stream keystone output: status/summary markers at INFO (visible in
            # Prefect UI), everything else at DEBUG (available if needed).
            def _log_stdout(line: str) -> None:
                if (
                    "BOOTSTRAP_DEVCONTAINER_STATUS:" in line
                    or "BOOTSTRAP_DEVCONTAINER_SUMMARY:" in line
                ):
                    log.info(f"[{repo_id}] {line}")
                else:
                    log.debug(f"[{repo_id}] {line}")

            def _log_stderr(line: str) -> None:
                log.debug(f"[{repo_id}] {line}")

            proc = run_process(
                cmd,
                log_prefix=f"[{repo_id}]",
                stdout_callback=_log_stdout,
                stderr_callback=_log_stderr,
            )

            # Step 3: Parse result
            bootstrap_result = None
            if result_file.exists():
                try:
                    bootstrap_result = json.loads(result_file.read_text())
                except json.JSONDecodeError:
                    log.warning(f"[{repo_id}] Failed to parse keystone_result.json")

            success = proc.returncode == 0
            error_message = proc.stderr[:1000] if not success else None

            if not success:
                log.error(f"[{repo_id}] keystone failed (exit code {proc.returncode})")

            result = RepoResult(
                repo_entry=repo_entry,
                success=success,
                error_message=error_message,
                bootstrap_result=bootstrap_result,
            )

            # Step 4: Upload result to S3 (even on failure, for debugging)
            try:
                _s3_write_text(
                    f"{repo_output_prefix}/eval_result.json",
                    result.model_dump_json(indent=2),
                )
                # Upload stderr log separately for debugging
                if proc.stderr:
                    _s3_write_text(
                        f"{repo_output_prefix}/keystone_stderr.log",
                        proc.stderr,
                    )
                log.info(f"[{repo_id}] Uploaded results to {repo_output_prefix}/")
            except Exception as upload_err:
                log.warning(f"[{repo_id}] Failed to upload to S3: {upload_err}")

            # If keystone failed, raise so Prefect marks the task as Failed
            if not success:
                raise RuntimeError(
                    f"keystone failed for {repo_id} (exit code {proc.returncode}): "
                    f"{error_message or 'no error message'}"
                )

            return result

    except RuntimeError:
        # Re-raise RuntimeError (keystone failure) — Prefect marks task as Failed
        raise
    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        result = RepoResult(
            repo_entry=repo_entry,
            success=False,
            error_message=error_msg,
        )
        # Try to upload failure result
        with contextlib.suppress(Exception):
            _s3_write_text(
                f"{repo_output_prefix}/eval_result.json",
                result.model_dump_json(indent=2),
            )
        raise RuntimeError(f"process_repo failed for {repo_id}: {error_msg}") from e


@flow(name="eval_keystone")
def eval_flow(
    repo_list_path: str,
    eval_config: EvalConfig,
    limit: int | None = None,
) -> EvalOutput:
    """Main evaluation flow.

    Args:
        repo_list_path: Path to JSONL file with repo entries
        eval_config: Evaluation configuration
        limit: Optional limit on number of repos to process

    Returns:
        EvalOutput with version info, pinned repos, and results
    """
    log = get_run_logger()

    # Load repo list
    repos: list[RepoEntry] = []
    with Path(repo_list_path).open() as f:
        for line_str in f:
            line_str = line_str.strip()
            if line_str:
                repos.append(RepoEntry(**json.loads(line_str)))

    # Validate unique IDs
    ids = [r.id for r in repos]
    if len(ids) != len(set(ids)):
        dupes = [k for k, v in Counter(ids).items() if v > 1]
        raise ValueError(f"Duplicate repo IDs found: {dupes}")

    log.info(f"Loaded {len(repos)} repos from {repo_list_path}")

    if limit is not None:
        repos = repos[:limit]
        log.info(f"Limited to first {limit} repos")

    total = len(repos)
    s3_cache_prefix = eval_config.s3_repo_cache_prefix

    # Phase 1: Archive all repos to S3 (cached by Prefect)
    log.info(f"Phase 1: Archiving {total} repos to S3...")
    archive_futures = []
    for repo_entry in repos:
        future = archive_repo_task.submit(
            repo_entry=repo_entry,
            s3_cache_prefix=s3_cache_prefix,
        )
        archive_futures.append((repo_entry, future))

    wait([f for _, f in archive_futures])
    archives: list[tuple[RepoEntry, str, str]] = []  # (entry, s3_path, commit)
    for repo_entry, future in archive_futures:
        s3_path, commit_hash = future.result()
        archives.append((repo_entry, s3_path, commit_hash))

    log.info(f"Phase 1 complete: {len(archives)} repos archived")

    # Phase 2: Run keystone on each repo
    trials_per_repo = eval_config.trials_per_repo

    # When running multiple trials, force disable caching
    if trials_per_repo > 1 and not eval_config.agent_config.no_cache_replay:
        log.info(f"trials_per_repo={trials_per_repo} > 1: forcing --no_cache_replay")
        eval_config = eval_config.model_copy(
            update={
                "agent_config": eval_config.agent_config.model_copy(
                    update={"no_cache_replay": True}
                )
            }
        )

    total_tasks = total * trials_per_repo
    log.info(
        f"Phase 2: Running keystone on {total} repos"
        f" ({trials_per_repo} trial(s) each, {total_tasks} total tasks)..."
    )
    process_futures: list[tuple[RepoEntry, int, PrefectFuture[RepoResult]]] = []
    for repo_entry, s3_path, commit_hash in archives:
        for trial in range(trials_per_repo):
            future = process_repo_task.submit(
                repo_entry=repo_entry,
                s3_tarball_path=s3_path,
                commit_hash=commit_hash,
                eval_config=eval_config,
                trial=trial if trials_per_repo > 1 else None,
            )
            process_futures.append((repo_entry, trial, future))

    # Collect results as they complete, logging progress
    wait([f for _, _, f in process_futures])
    results: list[RepoResult] = []
    succeeded = 0
    failed = 0
    for repo_entry, trial, future in process_futures:
        trial_label = f" trial={trial}" if trials_per_repo > 1 else ""
        try:
            result = future.result()
            results.append(result)
            succeeded += 1
            remaining = total_tasks - succeeded - failed
            log.info(
                f"repo id={repo_entry.id}{trial_label} finished with SUCCESS, "
                f"{remaining} tasks remain in the eval."
            )
        except Exception as e:
            failed += 1
            remaining = total_tasks - succeeded - failed
            log.info(
                f"repo id={repo_entry.id}{trial_label} finished with FAILURE, "
                f"{remaining} tasks remain in the eval."
            )
            # Create a failure result for the summary
            results.append(
                RepoResult(
                    repo_entry=repo_entry,
                    success=False,
                    error_message=str(e),
                )
            )

    # Build output
    version_info = get_version_info()
    pinned_repos = [r.repo_entry for r in results]

    output = EvalOutput(
        keystone_version=version_info.model_dump(),
        repos=pinned_repos,
        results=results,
    )

    # Upload summary to S3
    s3_output_prefix = eval_config.s3_output_prefix.rstrip("/")
    try:
        _s3_write_text(
            f"{s3_output_prefix}/eval_summary.json",
            json.dumps(output.model_dump(), indent=2),
        )
        log.info(f"Wrote eval summary to {s3_output_prefix}/eval_summary.json")
    except Exception as e:
        log.warning(f"Failed to upload eval summary to S3: {e}")

    log.info(f"Complete: {succeeded}/{total_tasks} succeeded, {failed}/{total_tasks} failed")

    return output
