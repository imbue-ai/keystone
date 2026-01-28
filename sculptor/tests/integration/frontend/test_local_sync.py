"""Integration tests for Local Sync functionality."""

import time
from pathlib import PosixPath

import pytest
from playwright.sync_api import expect
from pytest import mark

from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.diff_artifact import PlaywrightDiffArtifactElement
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.task_page import PlaywrightTaskPage
from sculptor.testing.playwright_utils import start_task_and_wait_for_ready
from sculptor.testing.resources import SculptorFactory
from sculptor.testing.user_stories import user_story


# TODO: Full E2E test that includes snapshotting
@pytest.fixture
def turn_off_snapshotting(sculptor_factory_: SculptorFactory) -> None:
    sculptor_factory_.environment["IS_SNAPSHOTTING_ENABLED"] = "false"


def _click_into_changes_panel(task_page: PlaywrightTaskPage) -> PlaywrightDiffArtifactElement:
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()
    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    artifacts_panel.get_combined_diff_tab().click()
    return artifacts_panel.get_combined_diff_section()


# FIXME: This doesn't work when the window is narrow because of _click_into_changes_panel.
# @mark.flaky(reason="Flaky in CI while we stabilize local sync UX")
@user_story("to make and receive changes using local sync")
def test_local_edit_shows_up_in_diff(
    turn_off_snapshotting: None,
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """Test that local edits show up in the diff artifact of a task."""
    # Navigate to task and enable local sync
    task_page = start_task_and_wait_for_ready(sculptor_page_, prompt="hello :)", wait_for_agent_to_finish=False)
    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("sync-status", "ACTIVE")

    # Make a local file change while sync is active
    pure_local_repo_.write_file(path="test.txt", content="test content")

    # Verify the local change appears in the diff panel
    diff_artifact = _click_into_changes_panel(task_page=task_page)
    uncommitted_section = diff_artifact.get_uncommitted_section()
    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    expect(file_artifact_element.get_file_name()).to_contain_text("test.txt")
    file_artifact_element.toggle_body()
    expect(file_artifact_element.get_file_body()).to_contain_text("test content")


# FIXME: This doesn't work when the window is narrow because of _click_into_changes_panel.
# @mark.flaky(reason="Flaky in CI while we stabilize local sync UX")
@user_story("to make and receive changes using local sync")
def test_local_edit_and_commit_shows_up_in_diff(
    turn_off_snapshotting: None,
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """Test that a local edit and then commit shows up in the committed diff artifact of a task."""
    pure_local_repo_.write_file(path=".gitignore", content=".claude/")
    pure_local_repo_.commit(message="ignore claude")
    # Navigate to task and enable sync
    task_page = start_task_and_wait_for_ready(sculptor_page_, prompt="hello :)", wait_for_agent_to_finish=False)
    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("sync-status", "ACTIVE")

    # Make a local edit and verify it appears in uncommitted section
    pure_local_repo_.write_file(path="test.txt", content="test content")
    edit_detected_text = "1+1-0"  # test.txt+1-0"

    diff_artifact = _click_into_changes_panel(task_page=task_page)
    expect(diff_artifact.get_uncommitted_section()).to_contain_text(f"Uncommitted Changes{edit_detected_text}")

    # Commit the changes locally
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit(message="test commit")

    committed = diff_artifact.get_committed_section()
    # Verify the change moves from uncommitted to committed section
    expect(committed).to_contain_text(f"Committed Changes{edit_detected_text}")


# FIXME: This doesn't work when the window is narrow because of _click_into_changes_panel.
@user_story("to make and receive changes using local sync")
def test_local_edit_and_reset_shows_up_in_diff(
    turn_off_snapshotting: None,
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """Test that a local edit and then reset clears the uncommitted diff."""
    # Navigate to task and enable sync
    task_page = start_task_and_wait_for_ready(sculptor_page_, prompt="hello :)", wait_for_agent_to_finish=False)
    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "ACTIVE")

    # Make a local edit and verify it appears
    pure_local_repo_.write_file(path="test.txt", content="test content")

    diff_artifact = _click_into_changes_panel(task_page=task_page)
    uncommitted_section = diff_artifact.get_uncommitted_section()
    file_artifacts = uncommitted_section.get_file_artifacts()
    expect(file_artifacts).to_have_count(1)
    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    expect(file_artifact_element.get_file_name()).to_contain_text("test.txt")
    file_artifact_element.toggle_body()
    expect(file_artifact_element.get_file_body()).to_contain_text("test content")

    # Reset local changes and verify diff is cleared
    pure_local_repo_.clean()
    updated_file_artifacts = uncommitted_section.get_file_artifacts()
    expect(updated_file_artifacts).to_have_count(0)


# FIXME: This doesn't work when the window is narrow because of _click_into_changes_panel.
@mark.flaky(reason="Flaky in CI while we stabilize local sync UX")
@user_story("to make and receive changes using local sync")
def test_local_history_edit_causes_pause(
    turn_off_snapshotting: None,
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """Test that a local history edit results in PAUSED state."""
    pure_local_repo_.write_file(path=".gitignore", content=".claude/")
    pure_local_repo_.commit(message="ignore claude")
    # Navigate to task and enable sync
    task_page = start_task_and_wait_for_ready(sculptor_page_, prompt="hello :)", wait_for_agent_to_finish=False)
    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "ACTIVE")

    pure_local_repo_.write_file(path="test.txt", content="test content")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit(message="test commit")
    # need to let sync finish so next action causes conflict
    edit_detected_text = "1+1-0"  # test.txt+1-0"
    diff_artifact = _click_into_changes_panel(task_page=task_page)
    committed = diff_artifact.get_committed_section()
    expect(committed).to_contain_text(f"Committed Changes{edit_detected_text}")

    pure_local_repo_.repo.run_git(("commit", "--amend", "-m", "amended commit"))
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "PAUSED")

    pure_local_repo_.repo.run_git(("reset", "--soft", "HEAD~1"))

    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "ACTIVE")


@user_story("to make and receive changes using local sync")
def test_local_sync_unstartable_when_user_has_dirty_state(
    turn_off_snapshotting: None,
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    """User worktree changes get overwritten at start if present, so we want to avoid that."""
    # Navigate to task and enable sync
    task_page = start_task_and_wait_for_ready(sculptor_page_, prompt="hello :)", wait_for_agent_to_finish=False)
    pure_local_repo_.write_file(path="test.txt", content="test content to prevent starting local sync")
    pure_local_repo_.stage_all_changes()

    task_header = task_page.get_task_header()

    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")
    # TODO: maybe cover with tests around the service layer

    # We *could* write a test that conditionally clicks on the button
    # if it didn't manage to change its state and skip the test otherwise.

    # It'll start enabled but is expected to be disabled
    expect(task_header.get_sync_button(), "button to be disabled because repo is dirty").to_be_disabled()

    # Forcing because the button is disabled
    task_header.get_sync_button().hover(force=True)

    tooltip = task_header.get_sync_button_tooltip()
    expect(tooltip).to_be_visible()
    expect(tooltip).to_contain_text("local changes need to be committed")

    pure_local_repo_.commit(message="commit to allow starting of local sync again")
    expect(task_header.get_sync_button(), "button to re-enable now that repo is clean").to_be_enabled()

    # Allow the tasks to stabilize before we finish the test so that we can safely
    # extract their snapshots or additional data
    wait_for_tasks_to_finish(task_page.get_task_list())


@mark.skip(reason="TODO")
def test_dangling_session_cleanup_at_startup() -> None:
    # Probably belongs in service-level test
    assert False


@mark.skip(reason="TODO")
def test_dangling_session_cleanup_at_teardown() -> None:
    # Probably belongs in service-level test
    assert False


@mark.skip(reason="TODO")
def test_switching_between_task_syncs() -> None:
    assert False


ALLOWED_TIME_FOR_FILE_TO_SHOW_UP_SECONDS = 10


@user_story("to make changes involving files with unusual characters like parentheses while using local sync")
def test_unusual_characters_in_filenames(
    turn_off_snapshotting: None,
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
) -> None:
    # Navigate to task and enable sync
    # TODO: maybe add proper escaping for crazier filenames like "test_file(x) ?*&!@#$@!-+-{$}\t  "
    test_file_name = "test_file(x)"
    task_page = start_task_and_wait_for_ready(sculptor_page_, prompt="hello!", wait_for_agent_to_finish=True)
    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "ACTIVE")

    send_chat_message(task_page.get_chat_panel(), f'run this command: touch "{test_file_name}"')
    wait_for_completed_message_count(chat_panel=task_page.get_chat_panel(), expected_message_count=4)
    time.sleep(ALLOWED_TIME_FOR_FILE_TO_SHOW_UP_SECONDS)
    assert pure_local_repo_.repo.is_path_in_repo(PosixPath(pure_local_repo_.base_path / test_file_name)), (
        f"File with unusual name did not show up within {ALLOWED_TIME_FOR_FILE_TO_SHOW_UP_SECONDS} seconds"
    )

    # Allow the tasks to stabilize before we finish the test so that we can safely
    # extract their snapshots or additional data
    wait_for_tasks_to_finish(task_page.get_task_list())


@user_story("to send messages after entering local sync and editing files")
def test_message_sending_after_local_sync_and_file_edit(
    turn_off_snapshotting: None,
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_with_checks_: MockRepoState,
) -> None:
    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        prompt="Please add a comment to the top of src/main.py that says '# Modified file'",
        wait_for_agent_to_finish=False,
    )

    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("data-sync-status", "ACTIVE")

    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    send_chat_message(chat_panel, "Please confirm you can still respond")

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    wait_for_tasks_to_finish(task_page.get_task_list())


# TODO: Test backup and restore behaviors once finalized
