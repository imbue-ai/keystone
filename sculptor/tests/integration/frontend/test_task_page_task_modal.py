"""Integration tests for Task page - Task Starting functionality from the task modal"""

from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.computing_environment import get_branch_commit
from sculptor.testing.elements.task import get_task_branch_name
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.elements.task_starter import select_branch
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage


def test_prompt_draft_persists_from_task_modal(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the prompt draft persists when reloading the home page."""
    task_text = "Hello, this is a test message!"
    follow_up_text = "This is a follow-up message."

    # Create a task
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text=task_text)

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    task = only(tasks.all())
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page open the task modal
    task_page = navigate_to_task_page(task=task)
    task_page.ensure_sidebar_is_open().click_new_agent_button()
    task_modal = task_page.get_task_modal()

    # Type a follow-up message
    prompt_input_element = task_modal.get_input_element()
    prompt_input_element.click()
    prompt_input_element.type(follow_up_text)

    # Open and close the modal
    task_modal.close()
    task_page.ensure_sidebar_is_open().click_new_agent_button()
    task_modal = task_page.get_task_modal()

    # Verify the follow-up message is still present
    expect(task_modal).to_contain_text(follow_up_text)


def test_task_starts_from_task_modal(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that a task can be started from the task modal."""
    task_text = "Hello, this is a test message!"
    follow_up_text = "This is a follow-up message."

    # Create a task
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text=task_text)

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    task = only(tasks.all())
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page open the task modal
    task_page = navigate_to_task_page(task=task)
    task_page.ensure_sidebar_is_open().click_new_agent_button()
    task_modal = task_page.get_task_modal()

    # Type a follow-up message and start the task
    prompt_input_element = task_modal.get_input_element()
    prompt_input_element.click()
    prompt_input_element.type(follow_up_text)
    task_modal.start_task()
    expect(task_modal).not_to_be_visible()

    # Go back to the task list
    task_page.navigate_to_home()

    # Verify the task was started and is in the task list
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)
    wait_for_tasks_to_finish(task_list=task_list)


def test_task_starts_from_task_modal_with_create_more(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that a task can be started from the task modal with the create more toggle on"""
    task_text = "Hello, this is a test message!"
    follow_up_text = "This is a follow-up message."
    follow_up_text_2 = "This is a follow-up message 2."

    # Create a task
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text=task_text)

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    task = only(tasks.all())
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page and open the task modal
    task_page = navigate_to_task_page(task=task)
    task_page.ensure_sidebar_is_open().click_new_agent_button()
    task_modal = task_page.get_task_modal()

    # Enable the create more toggle and start a task
    task_modal.toggle_create_more()
    prompt_input_element = task_modal.get_input_element()
    prompt_input_element.click()
    prompt_input_element.type(follow_up_text)
    task_modal.start_task()

    # Verify the modal is still open and the prompt input is cleared
    expect(task_modal).to_be_visible()
    prompt_input_element = task_modal.get_input_element()
    expect(prompt_input_element).to_have_text("")

    # Type a second follow-up message and start another task
    prompt_input_element.click()
    prompt_input_element.type(follow_up_text_2)
    task_modal.start_task()
    task_modal.close()

    # Go back to the task list and verify that both tasks were created
    task_page.navigate_to_home()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(3)
    wait_for_tasks_to_finish(task_list=task_list)


def test_tasks_from_multiple_branches_via_task_modal(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Create tasks from multiple different source branches using the task modal."""

    # Set up test branches
    initial_branch_name = pure_local_repo_.get_current_branch_name()
    assert initial_branch_name == "testing"
    second_branch_name = "second_branch"
    pure_local_repo_.create_reset_and_checkout_branch(second_branch_name)
    pure_local_repo_.write_file("src/second_branch.py", "print('Hello from second branch!')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add second branch file", commit_time="2025-01-01T00:00:02")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()

    # Create initial task from first branch to set up the task page
    select_branch(task_starter=task_starter, branch_name=initial_branch_name)
    create_task(task_starter=task_starter, task_text="Initial task from testing branch")
    expect(tasks).to_have_count(1)
    initial_task = only(tasks.all())
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page and open the task modal
    task_page = navigate_to_task_page(task=initial_task)
    task_page.ensure_sidebar_is_open().click_new_agent_button()
    task_modal = task_page.get_task_modal()

    # Switch to the testing branch in the task modal
    task_modal.switch_source_branch(initial_branch_name)
    task_modal.toggle_create_more()
    prompt_input_element = task_modal.get_input_element()
    prompt_input_element.click()
    prompt_input_element.type("Say hello from testing branch!")
    task_modal.start_task()
    expect(task_modal).to_be_visible()
    expect(task_modal.get_input_element()).to_have_text("")

    # Switch to the second branch in the task modal
    task_modal.switch_source_branch(second_branch_name)
    prompt_input_element.click()
    prompt_input_element.type("Say goodbye from second branch!")
    task_modal.start_task()
    task_modal.close()

    # Go back to the task list to verify both the tasks was created
    task_page.navigate_to_home()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(3)

    # verify the first task is created from "testing" branch and the second task from "second_branch"
    first_task = tasks.nth(1)
    first_task_branch_name = get_task_branch_name(first_task)
    second_task = tasks.nth(0)
    second_task_branch_name = get_task_branch_name(second_task)

    # Check if the created branch for the first task exists in the repo
    all_branches = pure_local_repo_.get_branches()
    assert first_task_branch_name in all_branches, (
        f"Branch {first_task_branch_name} not found in repo. Available branches: {all_branches}"
    )

    # Verify the first task branch points to the same commit as the testing branch
    first_task_commit = get_branch_commit(pure_local_repo_.repo, first_task_branch_name)
    testing_commit = get_branch_commit(pure_local_repo_.repo, initial_branch_name)
    assert first_task_commit == testing_commit, (
        f"First task branch {first_task_branch_name} points to {first_task_commit}, but testing branch points to {testing_commit}"
    )

    # Check if the created branch for the second task exists in the repo
    assert second_task_branch_name in all_branches, (
        f"Branch {second_task_branch_name} not found in repo. Available branches: {all_branches}"
    )

    # Verify the second task branch points to the same commit as the second_branch
    second_task_commit = get_branch_commit(pure_local_repo_.repo, second_task_branch_name)
    second_branch_commit = get_branch_commit(pure_local_repo_.repo, second_branch_name)
    assert second_task_commit == second_branch_commit, (
        f"Second task branch {second_task_branch_name} points to {second_task_commit}, but second_branch points to {second_branch_commit}"
    )

    wait_for_tasks_to_finish(task_list=task_list)


def test_updated_system_prompt_from_task_modal_persists(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the updated system prompt persists when reloading the home page."""
    task_text = "Hello, this is a test message!"
    initial_system_prompt = "Initial system prompt."
    updated_system_prompt = "Updated system prompt."

    # Set the initial system prompt
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    task_starter.get_system_prompt_open_button().click()
    task_starter.get_system_prompt_input_box().type(initial_system_prompt)
    task_starter.get_system_prompt_save_button().click()

    task_starter = home_page.get_task_starter()
    task_starter.get_task_input().click()
    # Create a task
    create_task(task_starter=task_starter, task_text=task_text)

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    task = only(tasks.all())
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page open the task modal
    task_page = navigate_to_task_page(task=task)
    task_page.ensure_sidebar_is_open().click_new_agent_button()
    task_modal = task_page.get_task_modal()

    # Open the task modal and update the system prompt
    assert task_modal.get_system_prompt_text() == initial_system_prompt, (
        f"Expected system prompt to be '{initial_system_prompt}', but got '{task_modal.get_system_prompt_text()}'"
    )
    task_modal.update_system_prompt(updated_system_prompt)
    task_modal.close()

    # Go back to the home page and verify the system prompt is updated
    task_page.navigate_to_home()
    task_starter = home_page.get_task_starter()
    task_starter.get_system_prompt_open_button().click()
    expect(task_starter.get_system_prompt_input_box()).to_have_text(updated_system_prompt)
