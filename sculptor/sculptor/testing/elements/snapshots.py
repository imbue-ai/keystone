"""Helper functions for testing snapshot behavior."""

import time

from loguru import logger

from imbue_core.processes.local_process import run_blocking


def get_container_id_for_task(task_id: str) -> str:
    """Get the Docker container ID for a given task."""
    result = run_blocking(
        ["docker", "ps", "--format", "{{.ID}} {{.Names}}"],
    )

    for line in result.stdout.strip().splitlines():
        if task_id in line:
            container_id = line.split()[0]
            logger.info(f"Found container {container_id} for task {task_id}")
            return container_id

    raise ValueError(f"No container found for task {task_id}")


def get_snapshot_images_for_task(task_id: str) -> list[str]:
    """Get all snapshot images for a given task."""
    result = run_blocking(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
    )

    snapshot_images = []
    for line in result.stdout.strip().splitlines():
        if line.strip():
            repo_tag = line.strip()
            if repo_tag.startswith("sculptortesting") and "-snapshot:" in repo_tag and task_id in repo_tag:
                snapshot_images.append(repo_tag)

    return snapshot_images


def verify_snapshot_count(task_id: str, expected_count: int, step_description: str = None) -> list[str]:
    """Verify the snapshot count for a task matches expectations."""
    snapshots = get_snapshot_images_for_task(task_id)
    actual_count = len(snapshots)

    assert actual_count == expected_count, (
        f"{step_description + ': ' if step_description else ''}Snapshot count mismatch! "
        + f"Expected: {expected_count}, Actual: {actual_count}. "
        + f"Snapshots: {snapshots}"
    )

    return snapshots


def verify_container_restart(task_id: str, original_container_id: str, timeout_seconds: int = 120) -> str:
    """Wait for a container to restart and return the new container ID."""
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        try:
            current_container_id = get_container_id_for_task(task_id)
            if current_container_id != original_container_id:
                logger.info(f"Container restarted: {original_container_id} -> {current_container_id}")
                return current_container_id
        except ValueError:
            # Container might be in the process of restarting
            pass

        time.sleep(2)

    raise TimeoutError(
        f"Container did not restart within {timeout_seconds} seconds. "
        + f"Original container: {original_container_id}"
    )


def verify_no_container_restart(task_id: str, expected_container_id: str) -> None:
    """Verify that the container has not restarted."""
    current_container_id = get_container_id_for_task(task_id)
    assert current_container_id == expected_container_id, (
        f"Container unexpectedly restarted. " + f"Expected: {expected_container_id}, Got: {current_container_id}"
    )


def wait_for_possible_snapshot(task_id: str, initial_count: int, timeout_seconds: int = 10) -> None:
    """Wait up to timeout_seconds for a new snapshot to be created."""
    start_time = time.time()
    poll_interval = 2

    while time.time() - start_time < timeout_seconds:
        time.sleep(poll_interval)
        snapshots = get_snapshot_images_for_task(task_id)
        current_count = len(snapshots)

        if current_count > initial_count:
            return
