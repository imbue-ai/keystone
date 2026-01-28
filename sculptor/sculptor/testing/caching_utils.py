import re
import subprocess
import time
from pathlib import Path

from loguru import logger

from sculptor.constants import PROXY_CACHE_PATH

_TEST_NAME_SUFFIX_PATTERN = re.compile(r"\[.*\]$")


def get_cache_dir_from_snapshot(snapshot) -> Path:
    """We want to create a cache file per test, not per test-file."""
    test_file = Path(snapshot.test_location.filepath)
    snapshot_dir = test_file.parent / "__snapshots__" / test_file.stem
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = snapshot_dir / _TEST_NAME_SUFFIX_PATTERN.sub("", snapshot.test_location.testname)
    return cache_dir.absolute()


def save_caches_to_snapshot_directory(local_path: Path, containers_with_tasks: tuple[tuple[str, str], ...]) -> None:
    for i, (container_id, task_id) in enumerate(containers_with_tasks):
        snapshot_filename = f"task_{i}.llm_cache_db"
        cache_path = local_path / snapshot_filename
        logger.info("Copying cache from container {}, task {} to {}", container_id, task_id, cache_path)
        copy_cache_db_from_container(container_id=container_id, local_path=cache_path)


def copy_cache_db_from_container(container_id: str, local_path: Path) -> None:
    proxy_cache_dir = PROXY_CACHE_PATH
    local_path = local_path.expanduser().resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)

    # This is currently necessary since it's possible the test is done before container setup is finished for restarts
    # Remove when a frontend indicator is created
    proxy_cache_exists = False
    start = time.time()
    while time.time() - start < 60.0:
        if (
            subprocess.run(["docker", "exec", "-u", "root", container_id, "test", "-f", proxy_cache_dir]).returncode
            == 0
        ):
            proxy_cache_exists = True
            break
        time.sleep(1.0)

    if not proxy_cache_exists:
        raise FileNotFoundError("Could not find proxy cache in container")

    subprocess.run(
        ["docker", "cp", f"{container_id}:{proxy_cache_dir}", str(local_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
