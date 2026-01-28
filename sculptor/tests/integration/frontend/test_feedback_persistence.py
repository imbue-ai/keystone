"""Integration tests for feedback button persistence."""

import re

from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


@user_story("thumbs up feedback persists across page reloads")
def test_thumbs_up_persists_after_reload(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that thumbs up feedback persists when reloading the task page."""
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

    feedback_dialog = chat_panel.open_feedback_dialog(thumbs_up_button=True, message_index=1)

    submit_button = feedback_dialog.get_submit_button()
    submit_button.click()

    expect(feedback_dialog).not_to_be_visible()

    action_bar = chat_panel.get_action_bar(message_index=1)
    thumbs_up_button = action_bar.get_thumbs_up_button()

    expect(thumbs_up_button).to_have_class(re.compile("feedbackSubmitted"))

    task_page.reload()

    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    action_bar = chat_panel.get_action_bar(message_index=1)
    thumbs_up_button = action_bar.get_thumbs_up_button()
    expect(thumbs_up_button).to_have_class(re.compile("feedbackSubmitted"))


@user_story("thumbs down feedback persists across page reloads")
def test_thumbs_down_persists_after_reload(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that thumbs down feedback persists when reloading the task page."""
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

    feedback_dialog = chat_panel.open_feedback_dialog(thumbs_up_button=False, message_index=1)

    issue_type_dropdown = feedback_dialog.get_issue_type_dropdown()
    issue_type_dropdown.click()
    sculptor_page_.get_by_role("option").first.click()

    submit_button = feedback_dialog.get_submit_button()
    submit_button.click()

    expect(feedback_dialog).not_to_be_visible()

    action_bar = chat_panel.get_action_bar(message_index=1)
    thumbs_down_button = action_bar.get_thumbs_down_button()

    expect(thumbs_down_button).to_have_class(re.compile("feedbackSubmitted"))

    task_page.reload()

    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    action_bar = chat_panel.get_action_bar(message_index=1)
    thumbs_down_button = action_bar.get_thumbs_down_button()
    expect(thumbs_down_button).to_have_class(re.compile("feedbackSubmitted"))


@user_story("thumbs up feedback persists across navigation")
def test_thumbs_up_persists_after_navigation(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that thumbs up feedback persists when navigating away and back."""
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

    feedback_dialog = chat_panel.open_feedback_dialog(thumbs_up_button=True, message_index=1)

    submit_button = feedback_dialog.get_submit_button()
    submit_button.click()

    expect(feedback_dialog).not_to_be_visible()

    action_bar = chat_panel.get_action_bar(message_index=1)
    thumbs_up_button = action_bar.get_thumbs_up_button()
    expect(thumbs_up_button).to_have_class(re.compile("feedbackSubmitted"))

    task_page.navigate_to_home()

    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    action_bar = chat_panel.get_action_bar(message_index=1)
    thumbs_up_button = action_bar.get_thumbs_up_button()
    expect(thumbs_up_button).to_have_class(re.compile("feedbackSubmitted"))


@user_story("messages can be sent after submitting feedback")
def test_message_sending_after_feedback(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that messages can be sent after submitting feedback and agent responds without blocking."""
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

    feedback_dialog = chat_panel.open_feedback_dialog(thumbs_up_button=True, message_index=1)
    submit_button = feedback_dialog.get_submit_button()
    submit_button.click()

    expect(feedback_dialog).not_to_be_visible()

    send_chat_message(chat_panel=chat_panel, message="Tell me a joke!")

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    messages = chat_panel.get_messages()
    expect(messages).to_have_count(4)
