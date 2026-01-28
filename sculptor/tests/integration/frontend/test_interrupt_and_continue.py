"""Tests for the interrupt and continue functionality. All tests are run in acceptance mode."""

import pytest
from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.decorators import mark_acceptance_test
from sculptor.testing.elements.chat_panel import PlaywrightChatPanelElement
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


def _interrupt_agent_before_any_output(chat_panel: PlaywrightChatPanelElement) -> None:
    """Interrupt the agent before any output is generated."""
    stop_button = chat_panel.get_stop_button()
    expect(stop_button).to_be_visible()

    # click the stop button until the agent is no longer running
    # [PROD-1549] if the process takes too long to start, the agent will continue running so we might need to hit stop again
    num_tries = 0
    while chat_panel.get_attribute("data-is-streaming") == "true" and num_tries < 3:
        expect(stop_button).to_be_visible()
        stop_button.click()
        expect(stop_button).to_be_disabled()
        # wait for the stop button to finish
        expect(chat_panel.get_stop_button_spinner()).not_to_be_visible()
        num_tries += 1

    expect(chat_panel).to_have_attribute("data-is-streaming", "false")


def _interrupt_agent_after_some_output(chat_panel: PlaywrightChatPanelElement) -> None:
    """Interrupt the agent after some output has been generated."""
    stop_button = chat_panel.get_stop_button()
    expect(stop_button).to_be_visible()

    stop_button.click()
    expect(chat_panel.get_stop_button_spinner()).to_be_visible()
    expect(stop_button).to_be_disabled()

    expect(chat_panel).to_have_attribute("data-is-streaming", "false")


@pytest.mark.skip(reason="[PROD-1744] This test is broken")
@user_story("to interrupt the agent while it's working")
@mark_acceptance_test
def test_interrupt_initial_message_immediately(sculptor_page_: PlaywrightHomePage, testing_mode_: str) -> None:
    """Test interrupting the initial message immediately."""

    home_page = sculptor_page_

    # Create a task that will generate a file
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Write a hello_world function in a file called hello.py at the repo root. Then run sleep 10.",
    )

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Get the task from the task list
    task = only(tasks.all())

    wait_for_tasks_to_build(task_list=task_list)

    # Navigate to task and wait for assistant to complete the file creation
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    expect(chat_panel.get_queued_message_card()).to_have_count(0)
    expect(chat_panel).to_have_attribute("data-is-streaming", "true")

    _interrupt_agent_before_any_output(chat_panel=chat_panel)

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    send_chat_message(
        chat_panel=chat_panel,
        message="What was the last thing I asked you to do? Do not write code or run commands. Just repeat the last thing I asked you to do.",
    )
    # wait for the agent to stop running
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)


@pytest.mark.skip(reason="[PROD-1744] This test is broken")
@user_story("to interrupt the agent while it's working")
@mark_acceptance_test
def test_interrupt_second_message_immediately(sculptor_page_: PlaywrightHomePage, testing_mode_: str) -> None:
    """Test interrupting the second message immediately."""

    home_page = sculptor_page_

    # Create a task that will generate a file
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="say hello to me. keep your message short and concise.")

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Get the task from the task list
    task = only(tasks.all())

    # Wait for task to be running
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task and wait for assistant to complete the file creation
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    send_chat_message(
        chat_panel=chat_panel,
        message="Write a hello_world function in a file called hello.py at the repo root. Then run sleep 10.",
    )
    expect(chat_panel.get_queued_message_card()).to_have_count(0)
    expect(chat_panel).to_have_attribute("data-is-streaming", "true")

    _interrupt_agent_after_some_output(chat_panel=chat_panel)

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    send_chat_message(
        chat_panel=chat_panel,
        message="What was the last thing I asked you to do? Do not write code or run commands. Just repeat the last thing I asked you to do.",
    )
    # wait for the agent to stop running
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=6)


@user_story("to interrupt the agent while it's working")
@mark_acceptance_test
@pytest.mark.skip("PROD-1582: Investigate the instability of `test_interrupt_tool_call`")
def test_interrupt_tool_call(sculptor_page_: PlaywrightHomePage, testing_mode_: str) -> None:
    """Test interrupting a tool call."""

    home_page = sculptor_page_

    # Create a task that will generate a file
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Write a hello_world function in a file called hello.py at the repo root. Then run sleep 10.",
    )

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Get the task from the task list
    task = only(tasks.all())

    wait_for_tasks_to_build(task_list=task_list)

    # Navigate to task and wait for agent to start running
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    expect(chat_panel.get_queued_message_card()).to_have_count(0)
    expect(chat_panel).to_have_attribute("data-is-streaming", "true")

    # wait for the tool call to be visible
    expect(chat_panel.get_tool_call()).to_be_visible()

    _interrupt_agent_after_some_output(chat_panel=chat_panel)

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    send_chat_message(
        chat_panel=chat_panel,
        message="What was the last thing I asked you to do? Do not write code or run commands. Just repeat the last thing I asked you to do.",
    )
    # wait for the agent to stop running
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)


@pytest.mark.skip(reason="[PROD-1744] This test is broken")
@user_story("to interrupt the agent while it's working")
@mark_acceptance_test
def test_interrupt_after_first_text_output(sculptor_page_: PlaywrightHomePage, testing_mode_: str) -> None:
    """Test that agent can be interrupted after it has output some text."""

    home_page = sculptor_page_

    # Create a task that will generate some output
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Write a hello_world function in a file called hello.py at the repo root. Then run sleep 10.",
    )

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Get the task from the task list
    task = only(tasks.all())

    wait_for_tasks_to_build(task_list=task_list)

    # Navigate to task page and wait for agent to start running
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    expect(chat_panel.get_queued_message_card()).to_have_count(0)
    expect(chat_panel).to_have_attribute("data-is-streaming", "true")

    # Wait for at least two messages to appear (user + assistant)
    expect(chat_panel.get_messages()).to_have_count(2)

    _interrupt_agent_after_some_output(chat_panel=chat_panel)

    initial_message_count = len(chat_panel.get_messages().all())

    # Ask the agent to repeat exactly what it said
    send_chat_message(
        chat_panel=chat_panel,
        message="What was the last thing I asked you to do? Do not write code or run commands. Just repeat the last thing I asked you to do.",
    )

    # Wait for the agent's response
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=initial_message_count + 2)


@user_story("to interrupt the agent while it's working")
@pytest.mark.skip(
    reason="[PROD-1548] This test addresses a real issue. We need to fix the issue before enabling this test."
)
@mark_acceptance_test
def test_agent_awareness_of_partial_messages(sculptor_page_: PlaywrightHomePage, testing_mode_: str) -> None:
    """Test that agent is aware of partial messages it emitted before being interrupted."""

    home_page = sculptor_page_

    # Create a task that will generate some output
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Write four random words and then the word 'done'. DO NOT add any other text. After you have written the words, run sleep 10.",
    )

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Get the task from the task list
    task = only(tasks.all())

    wait_for_tasks_to_build(task_list=task_list)

    # Navigate to task page
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    expect(chat_panel.get_queued_message_card()).to_have_count(0)
    expect(chat_panel).to_have_attribute("data-is-streaming", "true")

    # Wait for at least two messages to appear (user + assistant)
    expect(chat_panel.get_messages()).to_have_count(2)
    assistant_message = chat_panel.get_messages().nth(1)
    expect(assistant_message).to_contain_text("done")

    # Capture the partial content before interrupting
    partial_content = assistant_message.text_content()
    assert partial_content is not None
    partial_content_without_tool_call = partial_content.split("done")[0]

    # Interrupt the agent
    _interrupt_agent_after_some_output(chat_panel=chat_panel)

    initial_message_count = len(chat_panel.get_messages().all())

    # Ask the agent to repeat exactly what it said
    send_chat_message(
        chat_panel=chat_panel,
        message="What were the four words you chose? Repeat them in order with the same format. DO NOT run a sleep command.",
    )

    # Wait for the agent's response
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=initial_message_count + 2)

    # Get the agent's response
    last_message = chat_panel.get_messages().last
    repeated_content = last_message.text_content()
    assert repeated_content is not None

    # Verify the agent repeated the partial content
    # The repeated content should contain the partial content (allowing for some formatting differences)
    assert partial_content_without_tool_call.strip() in repeated_content, (
        f"Agent should repeat the partial content.\nOriginal: {partial_content}...\nRepeated: {repeated_content}..."
    )
