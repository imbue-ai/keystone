"""Integration tests for Task Page - Chatting functionality."""

from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.constants import ElementIDs
from sculptor.testing.elements.chat_panel import expect_message_to_have_role
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


@user_story("to have a multi-turn conversation with the agent")
def test_send_multiple_messages(sculptor_page_: PlaywrightHomePage) -> None:
    """Test sending multiple messages in a conversation."""

    home_page = sculptor_page_

    # Create and start initial task
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Hello this is test message one of three! Please respond briefly!",
        model_name="Codex (Beta)",
    )

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and verify initial state
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    chat_input = chat_panel.get_chat_input()
    expect(chat_input).to_have_text("")

    # Send second message and verify conversation flow
    send_chat_message(
        chat_panel=chat_panel, message="Hello this is test message two of three! Please respond briefly!"
    )

    # Ensure assistant has responded before continuing
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    # Send third message to test extended conversation handling
    send_chat_message(
        chat_panel=chat_panel, message="Hello this is test message three of three! Please respond briefly!"
    )

    # Verify all messages appear in correct order
    messages = chat_panel.get_messages()
    expect(messages).to_have_count(6)
    expect_message_to_have_role(message=messages.nth(0), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(0)).to_have_text("Hello this is test message one of three! Please respond briefly!")

    expect_message_to_have_role(message=messages.nth(2), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(2)).to_have_text("Hello this is test message two of three! Please respond briefly!")

    expect_message_to_have_role(message=messages.nth(4), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(4)).to_have_text("Hello this is test message three of three! Please respond briefly!")

    # Verify complete conversation with all assistant responses
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=6)
