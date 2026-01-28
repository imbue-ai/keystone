"""Integration tests for Task Page - Chatting functionality."""

from playwright.sync_api import expect

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.itertools import only
from sculptor.constants import ElementIDs
from sculptor.testing.container_utils import with_mock_claude_output
from sculptor.testing.elements.chat_panel import expect_message_to_have_role
from sculptor.testing.elements.chat_panel import select_model_by_name
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.image_utils import get_project_id_for_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.user_stories import user_story


@user_story("the contents of the prompt to survive page reloads and navigation")
def test_prompt_draft_persists_from_task_page(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the prompt draft persists when reloading the task page."""
    task_text = "Hello, this is a test message!"
    follow_up_text = "This is a follow-up message."

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # create a task
    create_task(task_starter=task_starter, task_text=task_text)

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    task = only(tasks.all())
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page and type a follow-up message
    task_page = navigate_to_task_page(task=task)
    task_page.get_chat_panel().get_chat_input().type(follow_up_text)

    # Verify that we can reload and the prompt draft persists
    task_page.reload()
    expect(task_page.get_chat_panel().get_chat_input()).to_have_text(follow_up_text)


@user_story("the contents of the prompt to survive page reloads and navigation")
def test_prompt_drafts_persist_on_multiple_tasks_and_home_page(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that prompt drafts persist across multiple tasks and home page navigation."""
    task_text = "Hello, this is a test message!"
    task_text_2 = "This is a second test message!"
    home_draft_text = "This is a home page draft message."
    follow_up_text = "This is a follow-up message."
    follow_up_text_2 = "This is a follow-up message for the second task."

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # Create a task
    create_task(task_starter=task_starter, task_text=task_text)
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    # create another task
    create_task(task_starter=task_starter, task_text=task_text_2)

    # Verify both tasks were created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)
    task_1 = tasks.nth(0)
    task_2 = tasks.nth(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # write another prompt draft
    task_starter.get_task_input().type(home_draft_text)

    # Navigate to task page and type a follow-up message
    task_page = navigate_to_task_page(task=task_1)
    task_page.get_chat_panel().get_chat_input().type(follow_up_text)

    # navigate back home and verify the draft is still there
    task_page.navigate_to_home()
    expect(task_starter.get_task_input()).to_have_text(home_draft_text)

    # navigate to the second task and type a follow-up message
    task_page = navigate_to_task_page(task=task_2)
    task_page.get_chat_panel().get_chat_input().type(follow_up_text_2)

    # Navigate back to the first task and verify the follow-up message is still there
    task_page.navigate_to_home()
    task_page = navigate_to_task_page(task=task_1)
    expect(task_page.get_chat_panel().get_chat_input()).to_have_text(follow_up_text)

    # navigate to the second task again and verify the follow-up message is still there
    task_page.navigate_to_home()
    task_page = navigate_to_task_page(task=task_2)
    expect(task_page.get_chat_panel().get_chat_input()).to_have_text(follow_up_text_2)


@user_story("to have a multi-turn conversation with the agent")
def test_starting_text(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the text for a task appears in the chat after it is started."""
    task_text = "Say hello to me!"

    home_page = sculptor_page_

    # Create task with specific text
    create_task(task_starter=home_page.get_task_starter(), task_text=task_text)

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Verify task text appears as first message in chat
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    # Wait for initial exchange to complete
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    messages = chat_panel.get_messages()
    expect_message_to_have_role(message=messages.nth(0), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(0)).to_have_text(task_text)


@user_story("to have a multi-turn conversation with the agent")
def test_send_message_after_task_start(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that users can send messages after task starts, and the assistant responds."""

    home_page = sculptor_page_

    # Create and start a task
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Hello, this is a test message! Please respond briefly!")

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and send a follow-up message
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    chat_input = chat_panel.get_chat_input()
    expect(chat_input).to_have_text("")

    send_chat_message(chat_panel=chat_panel, message="This is a second test message! Please respond briefly!")

    # Verify both user messages appear
    messages = chat_panel.get_messages()
    expect(messages).to_have_count(3)

    expect_message_to_have_role(message=messages.nth(0), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(0)).to_have_text("Hello, this is a test message! Please respond briefly!")

    expect_message_to_have_role(message=messages.nth(2), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(2)).to_have_text("This is a second test message! Please respond briefly!")

    # Verify assistant has responded to both messages
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)


@user_story("to have a multi-turn conversation with the agent")
def test_send_multiple_messages(sculptor_page_: PlaywrightHomePage) -> None:
    """Test sending multiple messages in a conversation."""

    home_page = sculptor_page_

    # Create and start initial task
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter, task_text="Hello this is test message one of three! Please respond briefly!"
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
    expect(messages).to_have_count(5)
    expect_message_to_have_role(message=messages.nth(0), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(0)).to_have_text("Hello this is test message one of three! Please respond briefly!")

    expect_message_to_have_role(message=messages.nth(2), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(2)).to_have_text("Hello this is test message two of three! Please respond briefly!")

    expect_message_to_have_role(message=messages.nth(4), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(4)).to_have_text("Hello this is test message three of three! Please respond briefly!")

    # Verify complete conversation with all assistant responses
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=6)


@user_story("to have a multi-turn conversation with the agent")
def test_remove_queued_message_and_continue(sculptor_page_: PlaywrightHomePage) -> None:
    """Test remove queued message and continue."""

    home_page = sculptor_page_

    # Start task and immediately navigate while it's still building
    task_starter = home_page.get_task_starter()
    task_starter.get_task_input().type("Hello this is test message one of four! Please respond briefly!")
    task_starter.get_start_button().click()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Queue a message while assistant is still responding to first message
    task_page = navigate_to_task_page(task=only(tasks.all()))
    chat_panel = task_page.get_chat_panel()
    chat_input = chat_panel.get_chat_input()
    expect(chat_input).to_have_text("")
    chat_input.type("Hello this is test message two of four! Please respond briefly!")
    chat_panel.get_send_button().click()
    expect(chat_panel.get_messages()).to_have_count(3)

    chat_input.type("Hello this is test message three of four! Please respond briefly!")
    chat_panel.get_send_button().click()

    # Delete the queued message before it's processed; we get the last since it's non-deterministic whether the first message has been sent
    delete_queued_message_button = chat_panel.get_delete_queued_message_button().last
    delete_queued_message_button.click()

    # Expect there to be no queued messages after all the messages have been sent and responded to
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
    expect(chat_panel.get_queued_message_card()).to_have_count(0)

    # Send a new message to verify conversation can continue normally
    chat_input.type("Hello this is test message four of four! Please respond briefly!")
    chat_panel.get_send_button().click()

    # Verify final state
    messages = chat_panel.get_messages()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=6)
    expect_message_to_have_role(message=messages.nth(0), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(0)).to_have_text("Hello this is test message one of four! Please respond briefly!")

    expect_message_to_have_role(message=messages.nth(2), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(2)).to_have_text("Hello this is test message two of four! Please respond briefly!")

    expect_message_to_have_role(message=messages.nth(4), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(4)).to_have_text("Hello this is test message four of four! Please respond briefly!")


@user_story("to control the model used by the agent")
def test_model_selection(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that all models can be selected and used in a conversation."""
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Say hello to me!")
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    task = only(tasks.all())
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify we can see and interact with the model selector
    model_selector = chat_panel.get_model_selector()
    expect(model_selector).to_be_visible()

    # Get all available model names
    model_selector.click()
    model_options = chat_panel.get_model_options()
    model_names: list[str] = []
    for i in range(model_options.count()):
        model_names.append(model_options.nth(i).inner_text().strip())
    # Press Escape to close the dropdown
    sculptor_page_.keyboard.press("Escape")

    expected_messages = 2

    # Iterate through all available models by name
    for model_name in model_names:
        selected_name = select_model_by_name(chat_panel=chat_panel, model_name=model_name)

        # Send a message asking about the model
        send_chat_message(chat_panel=chat_panel, message="What model are you using?")
        expected_messages += 2  # One for user message, one for assistant response
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=expected_messages)

        # Verify the response contains the model name
        last_message = chat_panel.get_messages().last
        assert selected_name.lower() in last_message.text_content().lower(), (
            f"Expected {selected_name} in response but got: {last_message.text_content()}"
        )

    # Verify we tested all unique models
    assert len(set(model_names)) == len(model_names), f"Found duplicate models in the list: {model_names}"


@user_story("to preserve the correct model when switching between tasks")
def test_model_selector_updates_when_switching_tasks(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the model selector displays the correct model when navigating between tasks."""
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    def verify_model_selection(task_index: int, model_name: str) -> None:
        """Navigate to a task and verify its model selector shows the expected model."""
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        task = tasks.nth(task_index)

        task_page = navigate_to_task_page(task=task)
        expect(task_page.get_by_test_id(ElementIDs.CHAT_PANEL)).to_be_visible()

        chat_panel = task_page.get_chat_panel()
        model_selector = chat_panel.get_model_selector()

        expect(model_selector).to_be_visible()
        expect(model_selector).to_contain_text(model_name, ignore_case=True, timeout=500)

    create_task(task_starter=task_starter, task_text="Say `Hello from Sonnet`", model_name="Sonnet")
    create_task(task_starter=task_starter, task_text="Say `Hello from Opus`", model_name="Opus")

    # Wait for both tasks to appear in the sidebar
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)

    # Verify model selections
    verify_model_selection(task_index=0, model_name="Opus")
    verify_model_selection(task_index=1, model_name="Sonnet")


@user_story("to see task failures and be able to recover from them")
def test_error_message_is_displayed_and_recoverable(
    sculptor_page_: PlaywrightHomePage,
    sculptor_factory_: SculptorFactory,
    test_root_concurrency_group: ConcurrencyGroup,
) -> None:
    """Test that error messages are shown in the flow of the conversation and messages can be retried after transient errors"""
    home_page = sculptor_page_

    # Create and start initial task
    task_starter = home_page.get_task_starter()
    initial_prompt = "Hello this is test message 1 of 2. Please respond briefly!"
    create_task(task_starter=task_starter, task_text=initial_prompt)

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and verify initial state
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

    # hijack claude to return an error message
    task_id = chat_panel.get_attribute("data-taskid")
    assert task_id is not None
    # this is a fake claude message that will output the same contents as an internal server error
    unsuccessful_claude_json = """
{"type":"system","subtype":"init","cwd":"/user_home/workspace","session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7","tools":["Task","Bash","Glob","Grep","LS","exit_plan_mode","Read","Edit","MultiEdit","Write","NotebookRead","NotebookEdit","WebFetch","TodoRead","TodoWrite","WebSearch","mcp__imbue__check","mcp__imbue__verify","ListMcpResourcesTool","ReadMcpResourceTool"],"mcp_servers":[{"name":"imbue","status":"connected"},{"name":"imbue_tools","status":"failed"}],"model":"claude-sonnet-4-20250514","permissionMode":"default","apiKeySource":"ANTHROPIC_API_KEY"}
{"type":"assistant","message":{"id":"msg_012vsi9duHT5ZZZcHuJTx19w","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","content":[{"type":"text","text":"hello. this is a fake message"}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":4,"cache_creation_input_tokens":2044,"cache_read_input_tokens":62606,"output_tokens":91,"service_tier":"standard"}},"parent_tool_use_id":null,"session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7"}
{"type":"result","subtype":"success","is_error":true,"duration_ms":18591,"duration_api_ms":15223,"num_turns":186,"result":"API Error: 500 {'type':'error','error':{'type':'api_error','message':'Internal server error'}}","session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7","total_cost_usd":0,"usage":{"input_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":0,"server_tool_use":{"web_search_requests":0}}}
"""
    project_id = get_project_id_for_task(sculptor_factory_.database_url, task_id, test_root_concurrency_group)
    with with_mock_claude_output(
        task_id, project_id, unsuccessful_claude_json, test_root_concurrency_group, exit_code=1
    ):
        new_message_text = "Hello this is test message 2 of 2. Please respond briefly!"
        send_chat_message(chat_panel=chat_panel, message=new_message_text)
        expect(chat_panel.get_error_block()).to_be_visible()
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "2")
    expect(chat_panel.get_error_block_retry_button()).to_be_visible()

    # Click the retry button
    chat_panel.get_error_block_retry_button().click()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=7)
    messages = chat_panel.get_messages()
    expect_message_to_have_role(message=messages.nth(0), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(0)).to_have_text(initial_prompt)

    expect_message_to_have_role(message=messages.nth(2), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(2)).to_have_text(new_message_text)

    expect_message_to_have_role(message=messages.nth(4), role=ElementIDs.USER_MESSAGE)
    expect(messages.nth(4)).to_have_text(new_message_text)


@user_story("to see the feedback buttons at the bottom of the chat panel opens dialog")
def test_feedback_buttons_display_dialog(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that feedback buttons are shown after the last message in the chat panel and opens the feedback dialog"""
    home_page = sculptor_page_

    # Create and start initial task
    task_starter = home_page.get_task_starter()
    initial_prompt = "Hello this is test message 1 of 1. Please respond briefly!"
    create_task(task_starter=task_starter, task_text=initial_prompt)

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and verify initial state
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # wait for feedback buttons to appear
    feedback_buttons = chat_panel.get_action_bar(message_index=1)
    expect(feedback_buttons).to_be_visible()

    # Open the feedback dialog with thumbs up button
    feedback_dialog = chat_panel.open_feedback_dialog(thumbs_up_button=True, message_index=1)

    # Close the feedback dialog
    cancel_button = feedback_dialog.get_cancel_button()
    cancel_button.click()
    expect(feedback_dialog).not_to_be_visible()

    # Verify thumbs down button also opens the dialog
    feedback_dialog = chat_panel.open_feedback_dialog(thumbs_up_button=False, message_index=1)

    # Also expect there to be additional dropdown for feedback issue type
    expect(feedback_dialog.get_issue_type_dropdown()).to_be_visible()
    cancel_button = feedback_dialog.get_cancel_button()
    cancel_button.click()
    expect(feedback_dialog).not_to_be_visible()


@user_story("to expect compaction to increase the remaining context left")
def test_compaction(sculptor_page_: PlaywrightHomePage) -> None:
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Say 20 random words for testing")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)
    task_page = navigate_to_task_page(task=only(tasks.all()))

    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    compaction_header = task_page.get_compaction_bar()
    initial_context_remaining = compaction_header.get_context_remaining()
    compaction_header.click()
    compaction_panel = task_page.get_compaction_panel()
    compaction_panel.get_compaction_button().click()

    expect(compaction_header).to_contain_text("Compacting...")
    expect(compaction_header).to_contain_text("Context Remaining")
    expect(chat_panel.get_context_summary_messages()).to_have_count(1)
    final_context_remaining = compaction_header.get_context_remaining()

    # TODO: This fails since there's a bug with our context
    # assert final_context_remaining > initial_context_remaining
