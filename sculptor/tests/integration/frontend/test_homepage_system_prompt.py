"""Integration tests for Homepage - System Prompt and Task Page - Chatting functionalities."""

from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.constants import ElementIDs
from sculptor.testing.elements.chat_panel import expect_message_to_have_role
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.elements.task_starter import set_home_page_system_prompt
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


# @pytest.mark.skip("[PROD-1119] Blocked on a race due to not waiting for system prompt to register")
@user_story("to inspect and edit the default system prompt")
def test_system_prompt_from_home_page(sculptor_page_: PlaywrightHomePage) -> None:
    home_page = sculptor_page_
    set_home_page_system_prompt(
        task_starter=home_page.get_task_starter(), system_prompt='Start all messages with "TESTING"'
    )

    create_task(task_starter=home_page.get_task_starter(), task_text="Say hello to me")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)
    task = only(tasks.all())
    task_page = navigate_to_task_page(task)

    chat_panel = task_page.get_chat_panel()
    messages = chat_panel.get_messages()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    user_message = messages.nth(0)
    assistant_message = messages.nth(1)
    expect_message_to_have_role(user_message, ElementIDs.USER_MESSAGE)
    expect_message_to_have_role(assistant_message, ElementIDs.ASSISTANT_MESSAGE)
    expect(user_message).to_have_text("Say hello to me")
    expect(assistant_message).to_contain_text("TESTING")

    system_prompt_button = chat_panel.get_open_system_prompt_button()
    expect(system_prompt_button).to_be_enabled()
    system_prompt_button.click()
    expect(chat_panel.get_system_prompt_text()).to_contain_text('Start all messages with "TESTING"')
