from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.elements.diff_artifact import PlaywrightDiffArtifactElement
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.task_page import PlaywrightTaskPage
from sculptor.testing.user_stories import user_story


def _click_into_changes_panel(task_page: PlaywrightTaskPage) -> PlaywrightDiffArtifactElement:
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()
    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    artifacts_panel.get_combined_diff_tab().click()
    return artifacts_panel.get_combined_diff_section()


@user_story("to verify untracked files don't linger from task to task after deleted")
def test_task_environment_has_no_untracked_files(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """
    This test:
    1. Creates a repo with both tracked and untracked files
    2. Creates a task through the Sculptor UI
    3. Verifies the untracked file shows up initially in the diff panel
    4. Deletes the untracked file
    5, Starts an new task
    6. Verifies that the untracked file does not show up in the new task
    """

    # Set up repo with tracked and untracked files
    pure_local_repo_.write_file("src/tracked_file1.py", "print('This is a tracked file')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add tracked file 1", commit_time="2025-01-01T00:00:01")

    pure_local_repo_.write_file("src/tracked_file2.py", "print('This is a tracked file')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add tracked file 2", commit_time="2025-01-01T00:00:01")

    # Delete the second tracked file without committing
    pure_local_repo_.write_file("src/tracked_file2.py", None)

    # Make a third tracked file, alphabetically earlier than .git
    pure_local_repo_.write_file(".a_tracked_file3.py", "print('This is a tracked file')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add tracked file 3", commit_time="2025-01-01T00:00:01")

    # Create an untracked file, alphabetically earlier than .git, that should appear in the diff panel initially
    pure_local_repo_.write_file(".a_untracked_file.txt", "This is an untracked file")

    # Verify the untracked file exists in the original repo
    git_status = pure_local_repo_.repo.run_git(["status", "--porcelain"])
    assert "?? .a_untracked_file.txt" in git_status, f"Expected untracked file in git status: {git_status}"

    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()

    # Create first task
    create_task(task_starter=task_starter, task_text="Hello")
    expect(tasks).to_have_count(1)

    # Wait for task to finish
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to the first task
    first_task = only(tasks.all())
    task_page = navigate_to_task_page(task=first_task)

    # Click into the changes panel and verify untracked file shows up initially
    diff_artifact = _click_into_changes_panel(task_page=task_page)
    uncommitted_section = diff_artifact.get_uncommitted_section()
    file_artifacts = uncommitted_section.get_file_artifacts()
    expect(file_artifacts).to_have_count(1)

    # Find the untracked file in the diff panel
    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    expect(file_artifact_element.get_file_name()).to_contain_text(".a_untracked_file.txt")

    # Delete the third tracked file
    pure_local_repo_.write_file(".a_tracked_file3.py", None)

    # Go back to home page
    sculptor_page_.go_back()

    # Create second task to verify the third tracked file no longer shows up, but the untracked file does show up
    create_task(task_starter=task_starter, task_text="Hello")
    expect(tasks).to_have_count(2)

    # Wait for second task to finish
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to the second task (most recent task is at index 0)
    second_task = tasks.nth(0)
    task_page = navigate_to_task_page(task=second_task)

    # Click into the changes panel and verify .a_tracked_file_3 shows up as a deletion
    diff_artifact = _click_into_changes_panel(task_page=task_page)
    uncommitted_section = diff_artifact.get_uncommitted_section()
    updated_file_artifacts = uncommitted_section.get_file_artifacts()
    expect(updated_file_artifacts).to_have_count(2)
    expect(uncommitted_section.get_nth_file_artifact_element(0).get_file_header()).to_contain_text(
        ".a_tracked_file3.py"
    )
    expect(uncommitted_section.get_nth_file_artifact_element(0).get_file_header()).to_contain_text("(deleted)")
