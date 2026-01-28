"""Integration tests for Local Sync container restart recovery functionality."""

import random
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pytest
from loguru import logger
from playwright.sync_api import expect

from imbue_core.pydantic_utils import model_update
from sculptor.services.config_service.conftest import populate_config_file_for_test
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.local_implementation import CREDENTIALS_FILENAME
from sculptor.services.config_service.user_config import load_config
from sculptor.services.config_service.user_config import save_config
from sculptor.services.config_service.utils import populate_credentials_file
from sculptor.testing.constants import RUNNING_TIMEOUT_SECS
from sculptor.testing.elements.chat_panel import PlaywrightChatPanelElement
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.diff_artifact import PlaywrightDiffArtifactElement
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.task_page import PlaywrightTaskPage
from sculptor.testing.playwright_utils import start_task_and_wait_for_ready
from sculptor.testing.resources import custom_sculptor_folder_populator
from sculptor.testing.resources import sculptor_folder_  # noqa: F401
from sculptor.testing.user_stories import user_story

_50_MB_IN_BYTES = 50 * 1024 * 1024


def _write_binary_file_of_n_bytes(repo: MockRepoState, file_name: str, n: int) -> None:
    """Write a binary file of approximately n bytes."""
    path = Path(repo.repo.base_path / file_name)
    path.unlink(missing_ok=True)
    with open(path, "wb") as f:
        f.write(random.randbytes(n))
    logger.info("Wrote binary file {} of size {} bytes", file_name, n)


@pytest.fixture
def local_sync_debounce_seconds_() -> float | None:
    return 5.0


def _click_into_changes_panel(task_page: PlaywrightTaskPage) -> PlaywrightDiffArtifactElement:
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()
    artifacts_panel.get_combined_diff_tab().click()
    return artifacts_panel.get_combined_diff_section()


def _get_container_id_for_task(task_id: str) -> str:
    """
    Get the container ID for a specific task by searching docker containers.
    """
    list_running_containers_cmd = ("docker", "ps", "--filter", "status=running", "--format", "{{.ID}} {{.Names}}")
    result = subprocess.run(list_running_containers_cmd, check=True, capture_output=True, text=True)
    container_lines = (cl.strip() for cl in result.stdout.strip().splitlines())
    container_lines = (cl for cl in container_lines if cl)
    matching_containers = []
    for line in container_lines:
        parts = line.split(maxsplit=1)
        if not len(parts) == 2:
            continue
        container_id, container_name = parts
        if task_id in container_name:
            matching_containers.append((container_id, container_name))
    assert len(matching_containers) > 0, f"No running container found for task {task_id}"
    assert len(matching_containers) == 1, (
        f"Multiple running containers found for task {task_id}: {matching_containers}"
    )
    final_running_container_id, _final_container_name = matching_containers[0]
    return final_running_container_id


def _is_container_running(container_id: str) -> bool:
    cmd = ("docker", "ps", "--filter", "status=running", "--filter", f"id={container_id}", "--format", "{{.ID}}")
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return bool(result.stdout.strip())
    except:
        return False


def _wait_for_container_to_stop(container_id: str, timeout_seconds: int = 30) -> None:
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        if not _is_container_running(container_id):
            return
        time.sleep(0.5)
    raise TimeoutError(f"Container {container_id} still running after {timeout_seconds} seconds")


def _wait_task_container_id(task_id: str, timeout_seconds: int = 30) -> str:
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            container_id = _get_container_id_for_task(task_id)
            if _is_container_running(container_id):
                return container_id
        except AssertionError:
            continue
        time.sleep(0.5)
    raise TimeoutError(f"Didn't find {task_id} container after {timeout_seconds} seconds")


# TODO: consider just grabbing load_config(sculptor_folder_).max_snapshot_size_bytes directly and telling agent to write a bigger file
def _populate_sculptor_folder_with_pinned_snapshot_limit(path: Path, credentials: Credentials) -> None:
    conf_path = path / "config.toml"
    populate_config_file_for_test(conf_path)
    config = model_update(
        load_config(conf_path), {"max_snapshot_size_bytes": _50_MB_IN_BYTES}
    )  # want to force a restart immediately
    conf_path.unlink()
    save_config(config, conf_path)
    populate_credentials_file(path / CREDENTIALS_FILENAME, credentials)


def _expect_file_synced(changes_panel: PlaywrightDiffArtifactElement, filename: str, timeout_sec: int = 30) -> None:
    expect(changes_panel.get_uncommitted_section()).to_contain_text(filename, timeout=timeout_sec * 1000)


@contextmanager
def container_restart_scenario_setup_and_validation(
    sculptor_page: PlaywrightHomePage,
) -> Generator[tuple[str, PlaywrightChatPanelElement, PlaywrightDiffArtifactElement], None, None]:
    """
    Context manager for container restart tests.

    Handles:
    - Starting task and enabling local sync
    - Getting task_id, diff_artifact, chat_panel
    - Post-test verification of UI state (no ConcurrencyGroup errors, etc.)
    - Stopping local sync after the test completes
    """
    home_page = PlaywrightHomePage(page=sculptor_page)

    # Start task and enable local sync
    task_page = start_task_and_wait_for_ready(home_page, prompt="hello :)", wait_for_agent_to_finish=False)
    task_header = task_page.get_task_header()
    task_id = task_page.get_task_id()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "ACTIVE")

    container_id = _get_container_id_for_task(task_id)
    assert container_id is not None, "Container ID should not be None"
    chat_panel = task_page.get_chat_panel()
    changes_panel = _click_into_changes_panel(task_page=task_page)

    # Run the test
    yield container_id, chat_panel, changes_panel

    # Verify the Pairing Mode button is not in an error/paused state
    sync_button_status = task_header.get_sync_button().get_attribute("data-sync-status")
    assert sync_button_status not in ["ERROR", "PAUSED"]

    task_header.get_sync_button().hover()
    sync_button_tooltip = task_header.get_sync_button_tooltip()
    tooltip_messages = []
    if sync_button_tooltip.count() > 0:
        tooltip_text = sync_button_tooltip.text_content() or ""
        tooltip_messages.append(tooltip_text)
    assert not any("ConcurrencyGroup" in msg and ("not active" in msg or "exited" in msg) for msg in tooltip_messages)

    # Verify container ID changed (confirms restart occurred)
    container_id_after = _wait_task_container_id(task_id)
    assert container_id != container_id_after, f"Container should have restarted, but only saw {container_id_after=}"

    # Stop local sync
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")


@user_story("to have local sync resume working after container restarts")
@custom_sculptor_folder_populator.with_args(_populate_sculptor_folder_with_pinned_snapshot_limit)
def test_local_sync_continues_after_restart(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """Test that local sync continues to work after the container restarts."""
    with container_restart_scenario_setup_and_validation(sculptor_page_) as (_container_id, chat_panel, changes_panel):
        # force restart and verify initial sync working
        pure_local_repo_.write_file("test_before_restart.txt", "content_before_restart")
        _write_binary_file_of_n_bytes(pure_local_repo_, "force_restart.bin", 2 * _50_MB_IN_BYTES)
        _expect_file_synced(changes_panel, "test_before_restart.txt")

        # Trigger container restart
        send_chat_message(chat_panel=chat_panel, message="List the files in the current directory")
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

        # Verify file sync after restart
        pure_local_repo_.write_file(path="test_after_restart.txt", content="content_after_restart")
        _expect_file_synced(changes_panel, "test_after_restart.txt")


@user_story("to preserve uncommitted changes during container restart")
@custom_sculptor_folder_populator.with_args(_populate_sculptor_folder_with_pinned_snapshot_limit)
def test_local_sync_edits_during_restart_synced(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """Test that local edits during a restart are still synced after the restart completes"""
    with container_restart_scenario_setup_and_validation(sculptor_page_) as (container_id, chat_panel, changes_panel):
        # force restart and verify initial sync working
        _write_binary_file_of_n_bytes(pure_local_repo_, "force_restart.bin", 2 * _50_MB_IN_BYTES)
        pure_local_repo_.write_file("test_before_restart.txt", "content_before_restart")
        _expect_file_synced(changes_panel, "test_before_restart.txt")

        # Trigger container restart
        send_chat_message(chat_panel=chat_panel, message="List the files in the current directory")

        # Wait for the old container to stop (indicating restart is in progress)
        _wait_for_container_to_stop(container_id, timeout_seconds=RUNNING_TIMEOUT_SECS // 2)

        # Now immediately write file while restart is in progress
        pure_local_repo_.write_file(path="test_during_restart.txt", content="written_during_restart")

        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

        # Do more IO and verify all files got synced
        pure_local_repo_.write_file(path="test_after_restart.txt", content="written_after_restart")
        _expect_file_synced(changes_panel, "test_during_restart.txt")
        _expect_file_synced(changes_panel, "test_after_restart.txt")
