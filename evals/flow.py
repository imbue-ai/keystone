"""Prefect flow for MapReduce-style evaluation.

This module provides a unified task that works with any Prefect task runner:
- ThreadPoolTaskRunner (default, local)
- ProcessPoolTaskRunner (local, parallel processes)
- DaskTaskRunner (distributed)
- Modal (via Prefect-Modal integration)

The same `process_repo_task` runs everywhere - the task runner determines
where execution happens.
"""
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

import boto3
from prefect import flow, task, get_run_logger
from prefect.futures import wait
from prefect.task_runners import ProcessPoolTaskRunner
from prefect.task_runners import ThreadPoolTaskRunner

from config import AgentConfig, EvalConfig, RepoEntry, WorkerResult
from worker import process_repo


@task(
    name="process_repo",
    description="Process a single repository tarball with bootstrap_devcontainer",
    retries=1,
    retry_delay_seconds=60,
)
def process_repo_task(
    repo_source: str,
    agent_config: AgentConfig,
    output_dir: str,
) -> WorkerResult:
    """Process a single repo - works in any execution environment.
    
    This task is designed to work identically whether run locally or on
    a remote worker (Modal, Dask, etc.). The task runner handles distribution.
    
    Args:
        repo_source: Path to tarball (local path or S3 URI)
        agent_config: Configuration for the agent
        output_dir: Directory for output artifacts
        
    Returns:
        WorkerResult with success/failure and artifact paths
    """
    logger = get_run_logger()
    logger.info(f"Starting process_repo_task for {repo_source}")
    
    # API key is optional - if not set, relies on claude CLI's own auth
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    
    # Handle S3 sources by downloading first
    tarball_path = Path(repo_source)
    temp_download_dir = None
    
    if repo_source.startswith("s3://"):
        temp_download_dir = Path(tempfile.mkdtemp(prefix="s3_download_"))
        tarball_path = temp_download_dir / "input.tar.gz"
        
        # Parse S3 URI: s3://bucket/key -> bucket, key
        bucket, key = repo_source.replace("s3://", "").split("/", 1)
        s3 = boto3.client("s3")
        s3.download_file(bucket, key, str(tarball_path))
    
    try:
        logger.info(f"Calling process_repo with tarball={tarball_path}, output_dir={output_dir}")
        result = process_repo(
            tarball_path=tarball_path,
            agent_config=agent_config,
            output_dir=Path(output_dir),
            anthropic_api_key=anthropic_api_key,
            log_callback=lambda line: logger.info(line),
        )
        # Preserve the original source URI
        result.s3_repo_tarball = repo_source
        logger.info(f"Completed {repo_source}: success={result.success}")
        return result
    finally:
        # Clean up temp download
        if temp_download_dir and temp_download_dir.exists():
            shutil.rmtree(temp_download_dir, ignore_errors=True)


def get_task_runner(execution_mode: str, max_workers: int):
    """Get the appropriate task runner based on execution mode.
    
    Args:
        execution_mode: "local", "process", "dask", or "modal"
        max_workers: Maximum parallel workers
        
    Returns:
        A Prefect task runner instance
    """
    if execution_mode == "local":
        return ThreadPoolTaskRunner(max_workers=max_workers)
    elif execution_mode == "process":
        return ProcessPoolTaskRunner(max_workers=max_workers)
    elif execution_mode == "dask":
        from prefect_dask import DaskTaskRunner  # Optional dependency
        return DaskTaskRunner()
    elif execution_mode == "modal":
        # Modal integration via Prefect - requires prefect to be configured
        # to use Modal as a work pool
        # For now, fall back to local with a note
        # TODO: Configure Modal work pool integration
        return ThreadPoolTaskRunner(max_workers=max_workers)
    else:
        return ThreadPoolTaskRunner(max_workers=max_workers)


@flow(name="eval_bootstrap_devcontainer")
def eval_flow(
    repo_list_path: str,
    eval_config: EvalConfig,
    output_dir: str,
) -> list[WorkerResult]:
    """Main evaluation flow.
    
    The task_runner is configured based on eval_config.execution_mode.
    The same process_repo_task runs in all modes - only the runner changes.
    
    Args:
        repo_list_path: Path to JSONL file with repo entries
        eval_config: Evaluation configuration
        output_dir: Local directory for outputs
        
    Returns:
        List of WorkerResult for each repo
    """
    logger = get_run_logger()
    
    # Load repo list
    repos: list[RepoEntry] = []
    with open(repo_list_path) as f:
        for line in f:
            line = line.strip()
            if line:
                repos.append(RepoEntry(**json.loads(line)))
    
    logger.info(f"Loaded {len(repos)} repos from {repo_list_path}")
    
    # Submit all tasks
    futures = []
    for i, repo in enumerate(repos):
        repo_output_dir = Path(output_dir) / f"repo_{i}"
        repo_output_dir.mkdir(parents=True, exist_ok=True)
        
        future = process_repo_task.submit(
            repo_source=repo.s3_repo_tarball,
            agent_config=eval_config.agent_config,
            output_dir=str(repo_output_dir),
        )
        futures.append(future)
        logger.info(f"Submitted task {i+1}/{len(repos)}: {repo.s3_repo_tarball}")
    
    # Wait for all to complete
    logger.info(f"Waiting for {len(futures)} tasks to complete...")
    wait(futures)
    results = [f.result() for f in futures]
    
    success_count = sum(1 for r in results if r.success)
    logger.info(f"All tasks complete: {success_count}/{len(results)} succeeded")
    
    # Write summary
    summary_path = Path(output_dir) / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump([r.model_dump() for r in results], f, indent=2)
    
    return results


def create_tarball_from_dir(source_dir: Path, output_path: Path) -> Path:
    """Create a tarball from a directory."""
    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(source_dir, arcname=source_dir.name)
    return output_path
