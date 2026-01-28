"""Integration test for snapshot behavior with local sync."""

import pytest
from playwright.sync_api import expect

from imbue_core.processes.local_process import run_blocking
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.snapshots import get_container_id_for_task
from sculptor.testing.elements.snapshots import verify_container_restart
from sculptor.testing.elements.snapshots import verify_no_container_restart
from sculptor.testing.elements.snapshots import verify_snapshot_count
from sculptor.testing.elements.snapshots import wait_for_possible_snapshot
from sculptor.testing.elements.task_starter import create_and_navigate_to_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.task_page import PlaywrightTaskPage
from sculptor.testing.user_stories import user_story


@pytest.fixture
def local_sync_debounce_seconds_() -> int:
    return 5


def _write_file(repo: MockRepoState, filename: str, size_mb: int) -> None:
    """Write a binary file to the repository."""
    file_path = repo.base_path / filename
    run_blocking(
        ["dd", "if=/dev/zero", f"of={file_path}", f"bs=1M", f"count={size_mb}"],
    )


def _wait_for_file_in_changes_tab(task_page: PlaywrightTaskPage, filename: str, timeout_ms: int = 30000) -> None:
    """Wait for a file to appear in the Changes tab."""
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()

    artifacts_panel.get_combined_diff_tab().click()
    diff_section = artifacts_panel.get_combined_diff_section()

    uncommitted_section = diff_section.get_uncommitted_section()
    expect(uncommitted_section).to_contain_text(filename, timeout=timeout_ms)


def _enable_local_sync_and_write_file(
    task_page: PlaywrightTaskPage,
    repo: MockRepoState,
    filename: str,
    size_mb: int,
) -> None:
    """Start local sync, write a file, wait for it to sync, then stop local sync."""
    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "ACTIVE")

    _write_file(repo, filename, size_mb=size_mb)
    _wait_for_file_in_changes_tab(task_page, filename)

    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")


@pytest.mark.flaky(reason="Flaky in CI while we stabilize local sync UX")
@user_story("to confirm a snapshot and restart happen after a local sync operation")
def test_snapshot_triggered_by_large_local_sync(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """
    1. syncs one file above restart size
    2. verifies a snapshot+restart takes place
    3. verifies _no other_ snapshot takes place immediately after (regression test)
    """
    # Create the task. The initial message will trigger the first snapshot.
    task_page = create_and_navigate_to_task(
        sculptor_page_.get_task_starter(), sculptor_page_.get_task_list(), task_text="Hello"
    )
    task_id = task_page.get_task_id()
    wait_for_completed_message_count(chat_panel=task_page.get_chat_panel(), expected_message_count=2)
    initial_container_id = get_container_id_for_task(task_id)
    wait_for_possible_snapshot(task_id, initial_count=0)
    verify_snapshot_count(task_id, expected_count=1, step_description="After first agent message")

    # Local sync a large file, which will trigger a snapshot + container restart
    _enable_local_sync_and_write_file(task_page, pure_local_repo_, filename="large_file.bin", size_mb=100)
    wait_for_possible_snapshot(task_id, initial_count=1)
    verify_snapshot_count(task_id, expected_count=2, step_description="After large file sync triggered a snapshot")
    container_id_after_restart = verify_container_restart(task_id, initial_container_id, timeout_seconds=120)

    # Make sure there is no additional snapshot after the restart
    wait_for_possible_snapshot(task_id, initial_count=2)
    verify_snapshot_count(task_id, expected_count=2, step_description="After container restart")

    # And no additional restarts either
    verify_no_container_restart(task_id, container_id_after_restart)
