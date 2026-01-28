"""Integration tests for Task Page - Changes Tab functionality."""

import pytest
from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.elements.task_starter import select_branch
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story

# Basic artifact panel functionality tests


@user_story("to see changes in the agent's code")
def test_artifact_panel_diff_tab_basic(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the artifact panel diff tab shows file changes correctly."""

    home_page = sculptor_page_

    # Create a task that will generate a file
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter, task_text="Write a hello_world function in a file called hello.py at the repo root"
    )

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and wait for assistant to complete the file creation
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify artifact panel shows the created file
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()

    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    artifacts_panel.get_combined_diff_tab().click()
    diff_artifact = artifacts_panel.get_combined_diff_section()

    # Check that the assistant created exactly one file
    uncommitted_section = diff_artifact.get_uncommitted_section()
    file_artifacts = uncommitted_section.get_file_artifacts()
    expect(file_artifacts).to_have_count(1)

    # Verify it's the expected hello.py file and contains expected content
    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    expect(file_artifact_element.get_file_name()).to_contain_text("hello.py")
    file_artifact_element.toggle_body()
    expect(file_artifact_element.get_file_body()).to_contain_text("def hello_world")


# File expand/collapse tests


@pytest.mark.skip(reason="[PROD-2353] We need to add back the ability to launch tasks with untracked files")
@user_story("to see changes in the agent's code")
def test_expand_collapse_file_contents_in_diff(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that file contents can be expanded and collapsed in the diff view."""

    home_page = sculptor_page_

    # Create a task with multiple staged files
    file_contents = "testing string"
    pure_local_repo_.write_file("testing.py", file_contents)
    pure_local_repo_.stage_all_changes()

    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Say hello to me",
    )

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task and wait for completion
    task_page = navigate_to_task_page(task=only(tasks.all()))
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Check the artifact panel
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()

    artifacts_panel.get_combined_diff_tab().click()
    diff_artifact = artifacts_panel.get_combined_diff_section()

    uncommitted_section = diff_artifact.get_uncommitted_section()
    file_artifacts = uncommitted_section.get_file_artifacts()
    expect(file_artifacts).to_have_count(1)

    # Test expanding and collapsing file contents
    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    file_dropdown = file_artifact_element.get_file_dropdown()
    file_body = file_artifact_element.get_file_body()

    # Initially, file contents should be collapsed (not showing function definitions)
    expect(file_body).not_to_be_visible()

    # Click to expand file
    file_artifact_element.toggle_body()
    # Now it should show the function definition
    expect(file_body).to_contain_text(file_contents)

    # Click to collapse file
    file_artifact_element.toggle_body()
    expect(file_body).not_to_be_visible()

    # Verify the file dropdown still exists and file is visible
    expect(file_dropdown).to_be_visible()
    expect(file_artifacts).to_have_count(1)


@user_story("to see changes in the agent's code")
def test_changes_stream_as_agent_makes_edits(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that changes stream in real-time as the agent makes edits."""

    home_page = sculptor_page_

    # Create a task that will make multiple edits
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Sleep for 10 seconds, then create a file called hello.py with a function named hello that prints hello",
    )

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_build(task_list=task_list)

    # Navigate to task
    task_page = navigate_to_task_page(task=only(tasks.all()))
    chat_panel = task_page.get_chat_panel()

    # Open artifact panel immediately to watch changes
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()

    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    artifacts_panel.get_combined_diff_tab().click()
    diff_artifact = artifacts_panel.get_combined_diff_section()
    uncommitted_section = diff_artifact.get_uncommitted_section()

    # Initially should have no files
    file_artifacts = uncommitted_section.get_file_artifacts()
    expect(file_artifacts).to_have_count(0)

    # Wait for the agent to complete the task
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Now the file should appear
    updated_file_artifacts = uncommitted_section.get_file_artifacts()
    expect(updated_file_artifacts).to_have_count(1)
    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    file_body = file_artifact_element.get_file_body()
    expect(file_artifact_element.get_file_name()).to_contain_text("hello.py")

    # Ask the agent to modify the file
    send_chat_message(
        chat_panel=chat_panel,
        message="Sleep for 10 seconds, then add a function named goodbye to hello.py that prints goodbye",
    )

    # The file should remain visible while being edited
    current_file_artifacts = uncommitted_section.get_file_artifacts()
    expect(current_file_artifacts).to_have_count(1)

    # Wait for the modification to complete
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    # The file should still be there, potentially with updated content
    current_file_artifacts = uncommitted_section.get_file_artifacts()
    expect(current_file_artifacts).to_have_count(1)
    file_artifact_element.toggle_body()

    # Should now contain both the original and new function
    expect(file_body).to_contain_text("def hello")
    expect(file_body).to_contain_text("def goodbye")


# Branch-specific changes tests


@user_story("to see changes in the agent's code")
@pytest.mark.skip("Local sync is currently broken")
def test_changes_reflected_from_branch_with_commits(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that changes are reflected when starting a task from a branch with committed changes."""

    # Create a new branch and add some committed changes
    test_branch = "feature-branch-with-changes"
    pure_local_repo_.create_reset_and_checkout_branch(test_branch)

    # Create and commit a file
    committed_content = "def feature_function():\n    return 'This was committed on the branch'"
    pure_local_repo_.write_file("feature.py", committed_content)
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add feature function")

    home_page = sculptor_page_

    # Start a task on this branch
    task_starter = home_page.get_task_starter()
    select_branch(task_starter=task_starter, branch_name=test_branch)

    create_task(task_starter=task_starter, task_text="Show me the contents of feature.py")

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task
    task_page = navigate_to_task_page(task=only(tasks.all()))
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Check the artifact panel shows the committed changes
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()

    artifacts_panel.get_combined_diff_tab().click()
    diff_artifact = artifacts_panel.get_combined_diff_section()

    # The committed section should show our file
    committed_section = diff_artifact.get_committed_section()
    expect(committed_section).to_be_visible()

    # Expand the committed section
    committed_section.get_expand_button().click()

    committed_file_artifacts = committed_section.get_file_artifacts()
    expect(committed_file_artifacts).to_have_count(1)

    # Verify the file is shown
    file_artifact_element = committed_section.get_nth_file_artifact_element(0)
    file_dropdown = file_artifact_element.get_file_dropdown()
    expect(file_dropdown).to_contain_text("feature.py")
    file_dropdown.click()
    expect(file_artifact_element.get_file_body()).to_contain_text("def feature_function")


# Commit transition tests


@user_story("to see changes in the agent's code")
@pytest.mark.skip("[PROD-950] This is failing on CI")
def test_artifact_panel_uncommitted_to_committed_transition(sculptor_page_: PlaywrightHomePage) -> None:
    """Test the transition of changes from uncommitted to committed state."""

    home_page = sculptor_page_

    # First, create a task that generates a file without committing
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Write a simple hello_world function in a file called hello_world.py at the repo root. Do NOT commit the changes.",
    )

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    task = only(tasks.all())

    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify the file is in uncommitted changes
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()
    artifacts_panel.get_combined_diff_tab().click()
    diff_artifact = artifacts_panel.get_combined_diff_section()

    # Check uncommitted section has our file
    uncommitted_section = diff_artifact.get_uncommitted_section()
    uncommitted_file_artifacts = uncommitted_section.get_file_artifacts()
    expect(uncommitted_file_artifacts).to_have_count(1)
    uncommitted_file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    expect(uncommitted_file_artifact_element.get_file_dropdown()).to_contain_text("hello_world.py")

    # Verify no committed changes yet
    committed_section = diff_artifact.get_committed_section()
    committed_file_artifacts = committed_section.get_file_artifacts()
    expect(committed_file_artifacts).to_have_count(0)

    # Now ask the assistant to commit the changes
    send_chat_message(
        chat_panel=chat_panel,
        message="Please commit the changes you made to hello_world.py with a descriptive commit message. Don't say any commit hashes.",
    )
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    # Verify the file has moved from uncommitted to committed
    # The uncommitted section should now be empty
    updated_uncommitted_file_artifacts = uncommitted_section.get_file_artifacts()
    expect(updated_uncommitted_file_artifacts).to_have_count(0)

    # The committed section should now have our file
    # Click to expand the committed section (it's collapsed by default)
    committed_section.get_expand_button().click()

    updated_committed_file_artifacts = committed_section.get_file_artifacts()
    expect(updated_committed_file_artifacts).to_have_count(1)
    committed_file_artifact_element = committed_section.get_nth_file_artifact_element(0)
    committed_file_dropdown = committed_file_artifact_element.get_file_dropdown()
    expect(committed_file_dropdown).to_contain_text("hello_world.py")

    # Click to expand and verify content is still there
    committed_file_dropdown.click()
    expect(committed_file_artifact_element.get_file_body()).to_contain_text("def hello_world")


@user_story("to see changes in the agent's code")
def test_artifact_panel_shows_committed_changes(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that committed changes show up correctly in the artifact panel."""

    home_page = sculptor_page_

    # Create a task that will generate a file and then commit it
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Write a hello_world function in a file called hello.py at the repo root, then commit the changes with a descriptive commit message. Don't say any commit hashes. Do this as quickly as possible -- do not bother exploring the repo first!",
    )

    # Wait for task container to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    task = only(tasks.all())

    wait_for_tasks_to_build(task_list=task_list)

    # Navigate to task and wait for assistant to complete the file creation and commit
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify artifact panel shows the changes
    artifacts_panel = task_page.get_artifacts_panel()
    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    artifacts_panel.get_combined_diff_tab().click()
    diff_artifact = artifacts_panel.get_combined_diff_section()

    # Check that the uncommitted section is empty or doesn't show our file
    # (since it should be committed)
    uncommitted_section = diff_artifact.get_uncommitted_section()
    uncommitted_file_artifacts = uncommitted_section.get_file_artifacts()
    # The file should not be in uncommitted changes after commit
    expect(uncommitted_file_artifacts).to_have_count(0)

    # Check that the committed section shows our file
    committed_section = diff_artifact.get_committed_section()
    committed_section.get_expand_button().click()

    committed_file_artifacts = committed_section.get_file_artifacts()
    expect(committed_file_artifacts).to_have_count(1)

    # Verify it's the expected hello.py file and contains expected content
    file_artifact_element = committed_section.get_nth_file_artifact_element(0)
    expect(file_artifact_element.get_file_name()).to_contain_text("hello.py")
    file_artifact_element.toggle_body()
    expect(file_artifact_element.get_file_body()).to_contain_text("def hello_world")
