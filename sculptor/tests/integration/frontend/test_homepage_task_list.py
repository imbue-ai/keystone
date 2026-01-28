"""Integration tests for Homepage - Task List functionality."""

from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.constants import ElementIDs
from sculptor.testing.elements.task import archive_task
from sculptor.testing.elements.task import delete_task
from sculptor.testing.elements.task import get_task_branch_name
from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story
from sculptor.web.derived import TaskStatus


@user_story("to see my current and past tasks at a glance")
def test_initial_load(sculptor_page_: PlaywrightHomePage) -> None:
    """When the home page is first loaded, ensure it starts off in a good state.
    Related to viewing tasks at a glance
    """
    home_page = sculptor_page_

    # Verify initial state has no tasks
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(0)
    expect(task_list).to_contain_text("No agents yet...")


@user_story("to meaningfully interact with the tasks on the list")
def test_task_deletes_from_list(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that a task can be deleted directly from the task list.
    Expectation: task can be deleted (forever) directly from the list
    """

    home_page = sculptor_page_

    # Create a task
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Task to be deleted. Respond with hello.")

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Wait for task to be ready
    wait_for_tasks_to_finish(task_list=task_list)

    task = only(tasks.all())

    # Delete the task
    delete_task(task=task)

    # Verify task is removed
    expect(tasks).to_have_count(0)


@user_story("to meaningfully interact with the tasks on the list")
def test_task_archives_and_shows_in_archived_tab(sculptor_page_: PlaywrightHomePage) -> None:
    """Test archiving a task moves it to the archived tab.
    Expectation: task can be archived and unarchived directly from the list
    Expectation: the list includes old tasks that have been archived
    """

    home_page = sculptor_page_

    # Create a task
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Task to be archived. Respond with hello.")

    # Get task list reference for verification
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    task = only(tasks.all())

    # Archive task and verify it's removed from active list
    archive_task(task=task)
    expect(tasks).to_have_count(0)

    # Verify task appears in archived tab with correct status
    sidebar = home_page.ensure_sidebar_is_open()
    sidebar.ensure_archived_view_is_open()
    expect(tasks).to_have_count(1)
    expect(task).to_have_attribute("data-archived", "true")


def test_task_summary_display_in_list(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that task summaries are displayed correctly in the task list.
    Expectation: there is a summary of the initial prompt for each task
    """
    home_page = sculptor_page_

    # Create a task
    task_starter = home_page.get_task_starter()
    initial_prompt = "hello. do nothing."
    create_task(task_starter=task_starter, task_text=initial_prompt)

    # Verify task appears
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    task = only(tasks.all())
    task_title_element = task.get_by_test_id(ElementIDs.TASK_TITLE)

    # Verify task title/summary element exists and is visible
    expect(task_title_element).to_be_visible()

    # The title element should have content - either the initial prompt or a generated title
    expect(task_title_element).not_to_be_empty()

    wait_for_tasks_to_build(task_list=task_list)

    # The data-has-title attribute indicates whether a generated title is shown
    # "false" means showing initial prompt, "true" means showing generated title
    expect(task_title_element).to_have_attribute("data-has-title", "true")

    # Wait for task to be ready
    wait_for_tasks_to_finish(task_list=task_list)

    # Verify the summary is still visible after task is ready
    expect(task_title_element).to_be_visible()
    expect(task_title_element).not_to_be_empty()
    expect(task_title_element).to_have_attribute("data-has-title", "true")


def test_task_displays_branch_name_in_list(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that branch names are displayed correctly in the task list.
    Expectation: there is a name of the branch that each task operates on
    """
    home_page = sculptor_page_

    # Create a task
    create_task(home_page.get_task_starter(), "hello. do nothing.")

    # Verify task appears
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    task = only(tasks.all())

    # Find the branch display element
    branch_element = task.get_by_test_id(ElementIDs.TASK_BRANCH)

    # Initially might show skeleton while branch name is determined
    expect(branch_element).to_be_visible()

    wait_for_tasks_to_build(task_list=task_list)

    # Verify the data attribute indicates branch is present
    branch_name = get_task_branch_name(task=task)

    # Wait for task to be ready (branch name should be determined by then)
    wait_for_tasks_to_finish(task_list=task_list)

    # The branch display also includes relative time
    # Just verify it contains some time-related text
    assert "branch_" in branch_name  # NOTE: we use branch_ prefix for testing purposes


def test_task_can_be_archived_and_unarchived(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that archived tasks can be unarchived.
    Expectation: task can be archived and unarchived directly from the list
    """
    home_page = sculptor_page_

    # Create a task
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Task to be archived. Respond with hello.")

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    task = only(tasks.all())

    # Archive the task
    archive_task(task=task)
    expect(tasks).to_have_count(0)

    # Navigate to archived tab
    sidebar = home_page.ensure_sidebar_is_open()
    sidebar.ensure_archived_view_is_open()
    expect(tasks).to_have_count(1)

    # Verify task shows archived status
    archived_task = only(tasks.all())
    expect(archived_task).to_have_attribute("data-archived", "true")

    # Unarchive the task
    archive_task(task=archived_task)

    # Verify task is removed from archived tab
    expect(tasks).to_have_count(0)

    # Navigate back to active tab
    sidebar = home_page.ensure_sidebar_is_open()
    sidebar.ensure_active_view_is_open()

    # Verify task is back in active list
    expect(tasks).to_have_count(1)

    # Verify task has its original status (not "Archived")
    unarchived_task = only(tasks.all())
    expect(unarchived_task).to_have_attribute("data-archived", "false")
    expect(unarchived_task).to_have_attribute("data-status", TaskStatus.READY)
