"""Integration test for snapshot failure robustness."""

import pytest
from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.user_stories import user_story


@pytest.fixture
def turn_off_snapshotting(sculptor_factory_: SculptorFactory) -> None:
    sculptor_factory_.environment["IS_SNAPSHOTTING_ENABLED"] = "false"


@user_story("to have tasks continue working even when snapshots fail")
def test_task_continues_with_snapshot_failures(
    turn_off_snapshotting: None, sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """
    This test verifies that tasks can continue operating even when the snapshot mechanism fails.

    Steps:
    1. Enable TESTING__FAIL_SNAPSHOTS environment variable to make snapshots always fail
    2. Create a task through the Sculptor UI
    3. Send multiple messages to the task
    4. Verify the task continues to function despite snapshot failures
    """

    # Set up a simple repo with a tracked file
    pure_local_repo_.write_file("src/example.py", "print('Hello, world!')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add example file", commit_time="2025-01-01T00:00:01")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()

    # Create a task
    initial_message = "Hello! Please respond briefly."
    create_task(task_starter=task_starter, task_text=initial_message)
    expect(tasks).to_have_count(1)

    # Wait for task to finish
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to the task
    first_task = only(tasks.all())
    task_page = navigate_to_task_page(task=first_task)
    chat_panel = task_page.get_chat_panel()

    # Wait for initial conversation to complete
    # wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify initial messages: initial user message, initial agent message, and snapshot failed warning
    messages = chat_panel.get_messages()
    expect(messages).to_have_count(2)

    # Send a second message
    second_message = "Can you help me understand this code? Please respond briefly."
    send_chat_message(chat_panel=chat_panel, message=second_message)

    # Wait for response to second message
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    # Send a third message
    third_message = "Thanks! One more question - please respond briefly."
    send_chat_message(chat_panel=chat_panel, message=third_message)

    # Wait for response to third message
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=6)

    # Verify we have all expected messages (2 per turn)
    final_messages = chat_panel.get_messages()
    expect(final_messages).to_have_count(6, timeout=5)
