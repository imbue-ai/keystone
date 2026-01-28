"""Benchmarks for Sculptor's functionality."""

from pathlib import Path

from playwright.sync_api import expect
from pytest_performancetotal.performance import Performance

from imbue_core.itertools import only
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import archive_task
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.resources import auto_select_project_  # noqa: F401
from sculptor.testing.resources import container_prefix_  # noqa: F401
from sculptor.testing.resources import credentials_  # noqa: F401
from sculptor.testing.resources import database_url_  # noqa: F401
from sculptor.testing.resources import frontend_  # noqa: F401
from sculptor.testing.resources import local_sync_debounce_seconds_  # noqa: F401
from sculptor.testing.resources import multi_tab_page_factory_  # noqa: F401
from sculptor.testing.resources import pure_local_repo_  # noqa: F401
from sculptor.testing.resources import pure_local_repo_with_checks_  # noqa: F401
from sculptor.testing.resources import sculptor_backend_port_  # noqa: F401
from sculptor.testing.resources import sculptor_config_path_  # noqa: F401
from sculptor.testing.resources import sculptor_factory_  # noqa: F401
from sculptor.testing.resources import sculptor_folder_  # noqa: F401
from sculptor.testing.resources import sculptor_launch_mode_  # noqa: F401
from sculptor.testing.resources import sculptor_page_  # noqa: F401
from sculptor.testing.resources import snapshot_path_  # noqa: F401
from sculptor.testing.resources import test_repo_factory_  # noqa: F401
from sculptor.testing.resources import testing_mode_  # noqa: F401


def benchmark_onboarding_to_first_task_archived(
    sculptor_page_: PlaywrightHomePage,  # noqa: F811
    pure_local_repo_: MockRepoState,  # noqa: F811
    performancetotal: Performance,
) -> None:
    """This benchmark runs through a simple scenario for a user who:

    1. Opens Sculptor
    2. Goes through the onboarding flow
    3. Begins their first task
    4. Performs one follow-up action to the task
    5. Validates that the task was completed successfully
    6. Archives the task
    """
    home_page = sculptor_page_

    # Get the repository path
    repo_path = str(Path(*pure_local_repo_.base_path.parts[-2:]))

    # Check if the repo indicator shows the directory
    repo_indicator = home_page.get_repository_indicator()

    # The repo indicator should contain the directory path
    expect(repo_indicator).to_be_visible()
    expect(repo_indicator).to_contain_text(repo_path)

    # Step 3: Begin their first task
    task_starter = home_page.get_task_starter()
    performancetotal.sample_start("task_creation")
    create_task(task_starter=task_starter, task_text="This is a test message: 'Knock knock!'")
    performancetotal.sample_end("task_creation")

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Wait for task to be ready
    performancetotal.sample_start("task_completion")
    wait_for_tasks_to_finish(task_list=task_list)
    performancetotal.sample_end("task_completion")

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task page
    performancetotal.sample_start("navigate_to_task")
    task_page = navigate_to_task_page(task=task)
    performancetotal.sample_end("navigate_to_task")

    # Step 5: Validate that the task was completed successfully
    chat_panel = task_page.get_chat_panel()

    # Wait for initial exchange to complete (user message + assistant response)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Step 4: Perform one follow-up action to the task
    performancetotal.sample_start("follow_up_message")
    send_chat_message(chat_panel=chat_panel, message="This is a follow-up test message. 'Amos.'")
    performancetotal.sample_end("follow_up_message")

    # Wait for assistant to respond to follow-up
    performancetotal.sample_start("follow_up_response")
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
    performancetotal.sample_end("follow_up_response")

    # Verify all messages appear
    messages = chat_panel.get_messages()
    expect(messages).to_have_count(4)

    # Navigate back to home page
    task_page.navigate_to_home()

    # Step 6: Archive the task
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    task = only(tasks.all())

    performancetotal.sample_start("archive_task")
    archive_task(task=task)
    performancetotal.sample_end("archive_task")

    # Verify task is removed from active list
    expect(tasks).to_have_count(0)

    # Verify task appears in archived tab
    sidebar = home_page.ensure_sidebar_is_open()
    sidebar.ensure_archived_view_is_open()
    expect(tasks).to_have_count(1)


def benchmark_multi_turn_conversation(
    sculptor_page_: PlaywrightHomePage,  # noqa: F811
    pure_local_repo_: MockRepoState,  # noqa: F811
    performancetotal: Performance,
) -> None:
    """This benchmark tests the performance of extended multi-turn conversations.
    a
        1. Opens Sculptor
        2. Creates a task
        3. Sends three follow-up messages, waiting for each response
        4. Verifies all messages appear correctly
    """
    home_page = sculptor_page_

    # Create initial task
    task_starter = home_page.get_task_starter()
    performancetotal.sample_start("initial_task_creation")
    create_task(task_starter=task_starter, task_text="This is a test message: 'Knock knock!'")
    performancetotal.sample_end("initial_task_creation")

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Wait for task to be ready
    performancetotal.sample_start("initial_task_completion")
    wait_for_tasks_to_finish(task_list=task_list)
    performancetotal.sample_end("initial_task_completion")

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and verify initial state
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    chat_input = chat_panel.get_chat_input()
    expect(chat_input).to_have_text("")

    # Wait for initial exchange to complete
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Send second message and verify conversation flow
    performancetotal.sample_start("follow_up_1_send")
    send_chat_message(chat_panel=chat_panel, message="This is follow-up test message 1. 'Who's there?'")
    performancetotal.sample_end("follow_up_1_send")

    # Ensure assistant has responded before continuing
    performancetotal.sample_start("follow_up_1_response")
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
    performancetotal.sample_end("follow_up_1_response")

    # Send third message
    performancetotal.sample_start("follow_up_2_send")
    send_chat_message(chat_panel=chat_panel, message="This is follow-up test message 2. 'Amos.'")
    performancetotal.sample_end("follow_up_2_send")

    # Ensure assistant has responded before continuing
    performancetotal.sample_start("follow_up_2_response")
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=6)
    performancetotal.sample_end("follow_up_2_response")

    # Send fourth message
    performancetotal.sample_start("follow_up_3_send")
    send_chat_message(chat_panel=chat_panel, message="This is follow-up test message 3. 'A mosquito'")
    performancetotal.sample_end("follow_up_3_send")

    # Verify complete conversation with all assistant responses
    performancetotal.sample_start("follow_up_3_response")
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=8)
    performancetotal.sample_end("follow_up_3_response")

    # Verify all messages appear
    messages = chat_panel.get_messages()
    expect(messages).to_have_count(8)


def benchmark_fork_task(
    sculptor_page_: PlaywrightHomePage,  # noqa: F811
    pure_local_repo_: MockRepoState,  # noqa: F811
    performancetotal: Performance,
) -> None:
    """This benchmark tests the performance of forking a task.

    1. Opens Sculptor
    2. Creates a task and waits for completion
    3. Forks the task at the last message
    4. Waits for forked task to complete
    5. Navigates between parent and child tasks
    """
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # Create parent task
    performancetotal.sample_start("parent_task_creation")
    create_task(task_starter=task_starter, task_text="This is a test message: 'Hello from parent task.'")
    performancetotal.sample_end("parent_task_creation")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    performancetotal.sample_start("parent_task_completion")
    wait_for_tasks_to_finish(task_list=task_list)
    performancetotal.sample_end("parent_task_completion")

    # Navigate to parent task
    parent_task = only(tasks.all())
    task_page = navigate_to_task_page(task=parent_task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Wait for snapshot to be created before forking
    expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

    # Fork at the last message
    performancetotal.sample_start("fork_task")
    chat_panel.fork_task(prompt="This is a fork test message: 'Hello from forked task.'", message_index=None)
    performancetotal.sample_end("fork_task")

    # Verify new task was created
    task_list = task_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)

    performancetotal.sample_start("forked_task_completion")
    wait_for_tasks_to_finish(task_list=task_list)
    performancetotal.sample_end("forked_task_completion")

    # Navigate to child task via the ForkedToBlock
    performancetotal.sample_start("navigate_to_forked_task")
    chat_panel.navigate_to_forked_task(block_index=0)
    performancetotal.sample_end("navigate_to_forked_task")

    # Verify child has the forked from block
    expect(chat_panel.get_forked_from_block(block_index=0)).to_be_visible()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=5)

    # Navigate back to parent via the ForkedFromBlock
    performancetotal.sample_start("navigate_to_parent_task")
    chat_panel.navigate_to_parent_task(block_index=0)
    performancetotal.sample_end("navigate_to_parent_task")

    # Verify we're back on parent
    expect(chat_panel.get_forked_to_block(block_index=0)).to_be_visible()
