"""Prefect flow for eval harness.

Clones repos to S3 tarballs, then runs keystone on each.
Per-repo results are uploaded to S3 as they complete.
"""

import contextlib
import datetime
import json
import logging
import subprocess
import tempfile
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

import fsspec
from eval_schema import EvalConfig, EvalResult, KeystoneRepoResult, RepoEntry, resolve_path
from prefect import flow, get_run_logger, task
from prefect.futures import PrefectFuture, wait

from keystone.agent_log import create_devcontainer_tarball
from keystone.process_runner import run_process
from keystone.timeouts import sandbox_timeout_seconds
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


def _get_git_info() -> tuple[str, bool]:
    """Return (commit_hash, is_dirty) for the current repo."""
    try:
        repo_root = Path(__file__).parent.parent
        commit = _run_git(["rev-parse", "HEAD"], cwd=repo_root, check=False)
        commit_hash = commit.stdout.strip() if commit.returncode == 0 else "unknown"
        status = _run_git(["status", "--porcelain"], cwd=repo_root, check=False)
        is_dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
        return commit_hash, is_dirty
    except Exception:
        return "unknown", False


def _save_rerun_manifest(
    eval_config: "EvalConfig",
    repo_list_path: str,
    limit: int | None,
    git_commit: str,
    git_is_dirty: bool,
    log: Any,
) -> None:
    """Save a rerun.json manifest to S3 so this eval can be exactly reproduced."""
    s3_prefix = eval_config.s3_output_prefix.rstrip("/")
    name = eval_config.name or ""

    # Reconstruct parent prefix (strip trailing /{name})
    if name and s3_prefix.endswith(f"/{name}"):
        parent_prefix = s3_prefix[: -len(f"/{name}")]
    else:
        parent_prefix = s3_prefix

    # Build a valid EvalRunConfig JSON (extra fields are ignored by EvalRunConfig parser)
    config_dict = {
        k: v
        for k, v in eval_config.model_dump().items()
        if k not in ("s3_output_prefix", "s3_repo_cache_prefix")
    }
    manifest = {
        "description": (
            f"Rerun of '{name}' "
            f"(originally {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')})"
        ),
        "repo_list_path": repo_list_path,
        "limit_to_first_n_repos": limit,
        "s3_output_prefix": parent_prefix + "/",
        "s3_repo_cache_prefix": eval_config.s3_repo_cache_prefix,
        "configs": [config_dict],
        # Extra metadata read by the viewer (ignored by EvalRunConfig)
        "git_commit": git_commit,
        "git_is_dirty": git_is_dirty,
    }

    rerun_path = f"{s3_prefix}/rerun.json"
    try:
        _s3_write_text(rerun_path, json.dumps(manifest, indent=2))
        log.info(f"Saved rerun manifest to {rerun_path}")
    except Exception as e:
        log.warning(f"Failed to save rerun manifest: {e}")


def _tarball_cache_key(
    context: object,  # noqa: ARG001
    parameters: dict[str, Any],
) -> str | None:
    """Cache key for archive_repo_task: repo URL + pinned commit hash.

    The tarball is immutable once created (pinned to a specific commit).
    To re-snapshot a repo, bump the cache_prefix or clear Prefect result cache.
    """
    repo_entry: RepoEntry = parameters["repo_entry"]  # type: ignore[assignment]
    cache_prefix: str = parameters["s3_cache_prefix"]  # type: ignore[assignment]
    return f"archive_repo:{cache_prefix}:{repo_entry.repo}:{repo_entry.commit_hash}"


@task(
    name="archive_repo",
    description="Clone a repo and upload a git archive tarball to S3",
    cache_key_fn=_tarball_cache_key,
    cache_expiration=None,  # Never expire - tarballs are immutable
    retries=4,
    retry_delay_seconds=10,
)
def archive_repo_task(
    repo_entry: RepoEntry,
    s3_cache_prefix: str,
) -> str:
    """Clone a repo at its pinned commit, create a git archive tarball, upload to S3.

    Returns the s3_tarball_path.
    Prefect caches this based on (repo_url, commit_hash, s3_cache_prefix).
    """
    log = get_run_logger()
    repo_url = repo_entry.repo
    repo_id = repo_entry.id
    commit_hash = repo_entry.commit_hash

    s3_tarball_path = f"{s3_cache_prefix.rstrip('/')}/{repo_id}.tar.gz"

    # Check if tarball already exists on S3
    if _s3_exists(s3_tarball_path):
        log.info(f"Tarball already exists at {s3_tarball_path}, skipping clone")
        return s3_tarball_path

    with tempfile.TemporaryDirectory() as tmp_dir:
        clone_path = Path(tmp_dir) / repo_id
        log.info(f"Cloning {repo_url} (pinned to {commit_hash[:12]})...")
        _run_git(["clone", repo_url, str(clone_path)])
        _run_git(["checkout", commit_hash], cwd=clone_path)

        # Verify checkout
        result = _run_git(["rev-parse", "HEAD"], cwd=clone_path)
        actual_hash = result.stdout.strip()
        if actual_hash != commit_hash:
            raise RuntimeError(
                f"Checkout mismatch for {repo_id}: expected {commit_hash}, got {actual_hash}"
            )
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

        # Upload tarball to S3
        log.info(f"Uploading {len(tarball_bytes)} bytes to {s3_tarball_path}")
        _s3_write_bytes(s3_tarball_path, tarball_bytes)

    return s3_tarball_path


def _process_repo_task_name(parameters: dict[str, object]) -> str:
    """Derive a human-friendly task-run name from the config, repo, and trial."""
    repo_entry: RepoEntry = parameters["repo_entry"]  # type: ignore[assignment]
    eval_config: EvalConfig = parameters["eval_config"]  # type: ignore[assignment]
    trial: int | None = parameters.get("trial")  # type: ignore[assignment]
    assert eval_config.name, "Must have a name!"
    return f"{eval_config.name}/{repo_entry.id}/t{trial}"


@task(
    name="process_repo",
    task_run_name=_process_repo_task_name,  # type: ignore[reportArgumentType]
    description="Run keystone on a repo",
    retries=0,
)
def process_repo_task(
    repo_entry: RepoEntry,
    s3_tarball_path: str,
    eval_config: EvalConfig,
    trial: int,
    docker_registry_mirror: str,
) -> KeystoneRepoResult:
    """Process a single repo.

    1. Download tarball from S3
    2. Extract to temp dir, init git
    3. Run keystone CLI
    4. Upload result to S3
    5. Return result
    """
    log = get_run_logger()
    repo_id = repo_entry.id
    keystone_config = eval_config.keystone_config
    s3_output_prefix = eval_config.s3_output_prefix.rstrip("/")
    repo_output_prefix = f"{s3_output_prefix}/{repo_id}/trial_{trial}"

    # Skip if result already exists on S3 (enables resuming partial runs)
    existing_result_path = f"{repo_output_prefix}/eval_result.json"
    if _s3_exists(existing_result_path):
        log.info(f"[{repo_id}] Result already exists at {existing_result_path}, skipping")
        try:
            existing_bytes = _s3_read_bytes(existing_result_path)
            existing_data = json.loads(existing_bytes)
            return KeystoneRepoResult(**existing_data)
        except Exception as e:
            log.warning(f"[{repo_id}] Failed to parse existing result, re-running: {e}")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_path = Path(tmp_dir) / repo_id

            # Step 1: Download and extract tarball
            log.debug(f"[{repo_id}] Downloading tarball from {s3_tarball_path}")
            tarball_bytes = _s3_read_bytes(s3_tarball_path)
            tarball_file = Path(tmp_dir) / "archive.tar.gz"
            tarball_file.write_bytes(tarball_bytes)

            work_path.mkdir()
            subprocess.run(
                ["tar", "xzf", str(tarball_file), "-C", str(work_path)],
                check=True,
            )

            # TODO: Are the tarballs in S3 not actually git repos?  I thought they were.
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

            agent = keystone_config.agent_config

            cmd = [
                "uv",
                "run",
                "keystone",
                "--project_root",
                str(work_path),
                "--test_artifacts_dir",
                str(test_artifacts_dir),
                "--max_budget_usd",
                str(agent.max_budget_usd),
                "--output_file",
                str(result_file),
                "--agent_time_limit_seconds",
                str(agent.agent_time_limit_seconds),
                "--provider",
                agent.provider,
            ]

            if not agent.agent_in_modal:
                cmd.append("--run_agent_locally_with_dangerously_skip_permissions")
            else:
                if not docker_registry_mirror:
                    raise RuntimeError(
                        "DOCKER_REGISTRY_MIRROR environment variable is not set. "
                        "Export it (e.g. export DOCKER_REGISTRY_MIRROR=https://mirror.gcr.io) "
                        "or pass docker_registry_mirror in the run config."
                    )
                cmd.extend(
                    [
                        "--agent_in_modal",
                        "--docker_registry_mirror",
                        docker_registry_mirror,
                    ]
                )

            if not agent.evaluator:
                cmd.append("--no_evaluator")
            if agent.agent_cmd is not None:
                cmd.extend(["--agent_cmd", agent.agent_cmd])
            if agent.model is not None:
                cmd.extend(["--model", agent.model.value])
            if keystone_config.log_db:
                cmd.extend(["--log_db", str(resolve_path(keystone_config.log_db))])
            if keystone_config.require_cache_hit:
                cmd.append("--require_cache_hit")
            if keystone_config.no_cache_replay:
                cmd.append("--no_cache_replay")
            if not agent.guardrail:
                cmd.append("--no_guardrail")
            if agent.use_agents_md:
                cmd.append("--use_agents_md")

            log.info(f"[{repo_id}] Running keystone...")

            # Stream keystone output: only forward status/summary markers to
            # Prefect (get_run_logger ships ALL levels to Prefect Cloud, so
            # sending every agent line would flood the connection).
            def _log_stdout(line: str) -> None:
                if (
                    "BOOTSTRAP_DEVCONTAINER_STATUS:" in line
                    or "BOOTSTRAP_DEVCONTAINER_SUMMARY:" in line
                ):
                    log.info(f"[{repo_id}] {line}")

            def _log_stderr(line: str) -> None:
                pass  # drop — avoid flooding Prefect Cloud with agent stderr

            # Harness process timeout: must outlive the Modal sandbox so the
            # keystone CLI can collect results and upload artifacts.  Derived
            # from sandbox_timeout_seconds (which is 2x agent timeout) plus a
            # 30 s buffer for cleanup/upload.
            # See keystone/src/keystone/timeouts.py for the full timeout hierarchy.
            hard_timeout = sandbox_timeout_seconds(agent.agent_time_limit_seconds) + 30

            proc = run_process(
                cmd,
                log_prefix=f"[{repo_id}]",
                stdout_callback=_log_stdout,
                stderr_callback=_log_stderr,
                timeout_seconds=hard_timeout,
            )

            # Step 3: Parse result
            bootstrap_result = None
            if result_file.exists():
                try:
                    bootstrap_result = json.loads(result_file.read_text())
                except json.JSONDecodeError:
                    log.warning(f"[{repo_id}] Failed to parse keystone_result.json")

            success = proc.returncode == 0
            if not success:
                last_lines = proc.stderr.splitlines()[-100:]
                error_message = "\n".join(last_lines)
            else:
                error_message = None

            if not success:
                log.error(f"[{repo_id}] keystone failed (exit code {proc.returncode})")

            result = KeystoneRepoResult(
                repo_entry=repo_entry,
                eval_config=eval_config,
                success=success,
                error_message=error_message,
                bootstrap_result=bootstrap_result,
                keystone_config=keystone_config,
                trial_index=trial,
            )

            # Step 4: Upload result and artifacts to S3 (even on failure, for debugging)
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
                # Upload devcontainer artifacts as tarball (same format used for Modal verification)
                devcontainer_dir = work_path / ".devcontainer"
                if devcontainer_dir.exists():
                    tarball = create_devcontainer_tarball(work_path)
                    if tarball:
                        _s3_write_bytes(
                            f"{repo_output_prefix}/devcontainer.tar.gz",
                            tarball,
                        )
                        log.debug(
                            f"[{repo_id}] Uploaded devcontainer tarball to "
                            f"{repo_output_prefix}/devcontainer.tar.gz"
                        )
                else:
                    log.warning(f"[{repo_id}] No .devcontainer directory found to upload")
                # Upload agent state directory tarball (e.g. ~/.claude, ~/.codex)
                agent_dir_file = work_path / "agent_dir.tar.gz"
                if agent_dir_file.exists():
                    _s3_write_bytes(
                        f"{repo_output_prefix}/agent_dir.tar.gz",
                        agent_dir_file.read_bytes(),
                    )
                    log.debug(
                        f"[{repo_id}] Uploaded agent dir tarball to "
                        f"{repo_output_prefix}/agent_dir.tar.gz"
                    )
                log.debug(f"[{repo_id}] Uploaded results to {repo_output_prefix}/")
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
        result = KeystoneRepoResult(
            repo_entry=repo_entry,
            eval_config=eval_config,
            success=False,
            error_message=error_msg,
            keystone_config=eval_config.keystone_config,
            trial_index=trial,
        )
        # Try to upload failure result
        with contextlib.suppress(Exception):
            _s3_write_text(
                f"{repo_output_prefix}/eval_result.json",
                result.model_dump_json(indent=2),
            )
        raise RuntimeError(f"process_repo failed for {repo_id}: {error_msg}") from e


def _load_repos(
    repo_list_path: str,
    limit: int | None = None,
) -> list[RepoEntry]:
    """Load and validate repo entries from a JSONL file."""
    repos: list[RepoEntry] = []
    with Path(repo_list_path).open() as f:
        for line_str in f:
            line_str = line_str.strip()
            if line_str:
                repos.append(RepoEntry(**json.loads(line_str)))

    ids = [r.id for r in repos]
    if len(ids) != len(set(ids)):
        dupes = [k for k, v in Counter(ids).items() if v > 1]
        raise ValueError(f"Duplicate repo IDs found: {dupes}")

    if limit is not None:
        repos = repos[:limit]

    return repos


def _archive_repos(
    repos: list[RepoEntry],
    s3_cache_prefix: str,
    log: Any,
) -> list[tuple[RepoEntry, str]]:
    """Archive repos to S3 and return (entry, s3_path) tuples."""
    log.info(f"Archiving {len(repos)} repos to S3...")
    archive_futures = []
    for repo_entry in repos:
        future = archive_repo_task.submit(
            repo_entry=repo_entry,
            s3_cache_prefix=s3_cache_prefix,
        )
        archive_futures.append((repo_entry, future))

    wait([f for _, f in archive_futures])
    archives: list[tuple[RepoEntry, str]] = []
    for repo_entry, future in archive_futures:
        s3_path = future.result()
        archives.append((repo_entry, s3_path))

    log.info(f"Archiving complete: {len(archives)} repos archived")
    return archives


def _collect_eval_results(
    eval_config: EvalConfig,
    process_futures: list[tuple[RepoEntry, int, PrefectFuture[KeystoneRepoResult]]],
    log: Any,
    repo_list_path: str | None = None,
    limit: int | None = None,
    git_commit: str = "unknown",
    git_is_dirty: bool = False,
) -> EvalResult:
    """Collect results from already-completed futures and build EvalResult."""
    total_tasks = len(process_futures)
    results: list[KeystoneRepoResult] = []
    succeeded = 0
    failed = 0
    for repo_entry, trial, future in process_futures:
        trial_label = f" trial={trial}"
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
            results.append(
                KeystoneRepoResult(
                    repo_entry=repo_entry,
                    eval_config=eval_config,
                    success=False,
                    error_message=str(e),
                    keystone_config=eval_config.keystone_config,
                    trial_index=trial,
                )
            )

    # Build output
    version_info = get_version_info()

    output = EvalResult(
        keystone_version=version_info,
        eval_config=eval_config,
        results=results,
    )

    # Upload summary to S3
    s3_output_prefix = eval_config.s3_output_prefix.rstrip("/")
    try:
        _s3_write_text(
            f"{s3_output_prefix}/eval_summary.json",
            output.model_dump_json(indent=2),
        )
        log.info(f"Wrote eval summary to {s3_output_prefix}/eval_summary.json")
    except Exception as e:
        log.warning(f"Failed to upload eval summary to S3: {e}")

    # Save rerun manifest so this eval can be exactly reproduced
    if repo_list_path:
        _save_rerun_manifest(eval_config, repo_list_path, limit, git_commit, git_is_dirty, log)

    log.info(f"Complete: {succeeded}/{total_tasks} succeeded, {failed}/{total_tasks} failed")
    return output


@flow(name="eval_keystone")
def eval_flow(
    # FIXME: Pass the whole config object in here, not all these parameters.
    repo_list_path: str,
    eval_configs: list[EvalConfig],
    s3_repo_cache_prefix: str,
    limit_to_first_n_repos: int | None,
    max_concurrent: int,
    docker_registry_mirror: str,
    task_start_stagger_seconds: float = 0,
) -> list[EvalResult]:
    """Main evaluation flow.

    Archives repos once, then runs each eval config against the same archives.
    """
    log = get_run_logger()
    git_commit, git_is_dirty = _get_git_info()
    log.info(f"Git state: {git_commit[:12]} {'(dirty)' if git_is_dirty else '(clean)'}")
    repos = _load_repos(repo_list_path, limit_to_first_n_repos)
    log.info(f"Loaded {len(repos)} repos from {repo_list_path}")

    # Phase 1: Archive repos once (shared across all configs)
    archives = _archive_repos(repos, s3_repo_cache_prefix, log)

    # Phase 2: Submit all configs' tasks with a shared concurrency limit.
    # Build work items ordered repo-first (all models on repo0, then repo1, ...)
    # so that under concurrency limits we get cross-model results per repo
    # rather than all repos for one model before moving to the next.
    resolved_eval_configs: list[EvalConfig] = []
    for i, eval_config in enumerate(eval_configs):
        label = eval_config.name or f"config-{i}"
        log.info(f"--- Preparing eval [{label}] ---")

        trials_per_repo = eval_config.trials_per_repo
        if trials_per_repo > 1 and not eval_config.keystone_config.no_cache_replay:
            log.info(f"trials_per_repo={trials_per_repo} > 1: forcing --no_cache_replay")
            eval_config = eval_config.model_copy(
                update={
                    "keystone_config": eval_config.keystone_config.model_copy(
                        update={"no_cache_replay": True}
                    )
                }
            )
        resolved_eval_configs.append(eval_config)

    work_items: list[tuple[int, RepoEntry, str, int]] = []
    for repo_entry, s3_path in archives:
        for i, eval_config in enumerate(resolved_eval_configs):
            for trial in range(eval_config.trials_per_repo):
                work_items.append((i, repo_entry, s3_path, trial))

    # Submit all work items; concurrency is bounded by the flow's
    # ThreadPoolTaskRunner(max_workers=max_concurrent).
    all_tagged: list[tuple[int, RepoEntry, int, PrefectFuture[KeystoneRepoResult]]] = []
    for i, (cfg_idx, repo_entry, s3_path, trial) in enumerate(work_items):
        if i > 0 and task_start_stagger_seconds > 0:
            time.sleep(task_start_stagger_seconds)
        future = process_repo_task.submit(
            repo_entry=repo_entry,
            s3_tarball_path=s3_path,
            eval_config=resolved_eval_configs[cfg_idx],
            trial=trial,
            docker_registry_mirror=docker_registry_mirror,
        )
        all_tagged.append((cfg_idx, repo_entry, trial, future))

    log.info(
        f"Submitted {len(all_tagged)} tasks "
        f"(max_concurrent={max_concurrent}, total={len(work_items)})"
    )

    # Group futures by config index
    config_futures: list[
        tuple[EvalConfig, list[tuple[RepoEntry, int, PrefectFuture[KeystoneRepoResult]]]]
    ] = [(cfg, []) for cfg in resolved_eval_configs]
    for cfg_idx, repo_entry, trial, future in all_tagged:
        config_futures[cfg_idx][1].append((repo_entry, trial, future))

    # Collect results per config
    outputs: list[EvalResult] = []
    for eval_config, futures in config_futures:
        output = _collect_eval_results(
            eval_config,
            futures,
            log,
            repo_list_path=repo_list_path,
            limit=limit_to_first_n_repos,
            git_commit=git_commit,
            git_is_dirty=git_is_dirty,
        )
        outputs.append(output)

    return outputs
