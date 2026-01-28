"""Integration tests for Checks and Suggestions functionality."""

import pytest
from playwright.sync_api import expect

from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


@user_story("to see checks running and their status updates")
@pytest.mark.skip()
def test_checks_appear_in_artifact_panel(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_with_checks_: MockRepoState
) -> None:
    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Hello, this is a test message! Please respond briefly!")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    task = tasks.first
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    artifacts_panel = task_page.get_artifacts_panel()

    checks_tab = artifacts_panel.locator("[data-testid='ARTIFACT_CHECKS_TAB']")
    expect(checks_tab).to_be_visible()
    checks_tab.click()

    checks_content = artifacts_panel.locator("text=successful_check")
    expect(checks_content).to_be_visible()

    failing_check_content = artifacts_panel.locator("text=failing_check")
    expect(failing_check_content).to_be_visible()


@user_story("to see suggestions appear in the artifact panel")
@pytest.mark.skip()
def test_suggestions_appear_in_artifact_panel(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_with_checks_: MockRepoState
) -> None:
    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Hello, this is a test message! Please respond briefly!")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    task = tasks.first
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    artifacts_panel = task_page.get_artifacts_panel()

    suggestions_tab = artifacts_panel.locator("[data-testid='ARTIFACT_SUGGESTIONS_TAB']")
    expect(suggestions_tab).to_be_visible()
    suggestions_tab.click()

    failing_check_suggestion = artifacts_panel.locator("text=Fix failing_check")
    expect(failing_check_suggestion).to_be_visible()

    pytest_check_suggestion = artifacts_panel.locator("text=Fix pytest_check")
    expect(pytest_check_suggestion).to_be_visible()

    lint_check_suggestion = artifacts_panel.locator("text=Fix lint_check")
    expect(lint_check_suggestion).to_be_visible()


@user_story("to see suggestions show up in chat area")
@pytest.mark.skip()
def test_suggestions_appear_in_chat_area(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_with_checks_: MockRepoState
) -> None:
    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Hello, this is a test message! Please respond briefly!")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    task = tasks.first
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    chat_input_area = sculptor_page_.locator("#chat-input")

    failing_check_suggestion = chat_input_area.locator("text=Fix failing_check")
    expect(failing_check_suggestion).to_be_visible()

    lint_check_suggestion = chat_input_area.locator("text=Fix lint_check")
    expect(lint_check_suggestion).to_be_visible()

    pytest_check_suggestion = chat_input_area.locator("text=Fix pytest_check")
    expect(pytest_check_suggestion).to_be_visible()


@user_story("to verify usage of suggestions")
@pytest.mark.skip()
def test_suggestion_usage(sculptor_page_: PlaywrightHomePage, pure_local_repo_with_checks_: MockRepoState) -> None:
    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Hello, this is a test message! Please respond briefly!")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    task = tasks.first
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    artifacts_panel = task_page.get_artifacts_panel()

    suggestions_tab = artifacts_panel.locator("[data-testid='ARTIFACT_SUGGESTIONS_TAB']")
    expect(suggestions_tab).to_be_visible()
    suggestions_tab.click()

    use_button = artifacts_panel.locator("button:has-text('Use')").first
    expect(use_button).to_be_visible()

    chat_input = chat_panel.get_chat_input()
    initial_text = chat_input.text_content()

    use_button.click()

    expect(chat_input).not_to_have_text(initial_text)


@user_story("to verify status shows correctly")
@pytest.mark.skip()
def test_check_status_shows_correctly(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_with_checks_: MockRepoState
) -> None:
    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Hello, this is a test message! Please respond briefly!")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    task = tasks.first
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    artifacts_panel = task_page.get_artifacts_panel()

    checks_tab = artifacts_panel.locator("[data-testid='ARTIFACT_CHECKS_TAB']")
    expect(checks_tab).to_be_visible()
    checks_tab.click()

    play_buttons = artifacts_panel.locator("button[class*='actionButton']")
    expect(play_buttons).to_have_count(5)

    for i in range(5):
        play_button = play_buttons.nth(i)
        play_button.click()

    # verify re-running
    spinner = artifacts_panel.locator(".rt-Spinner").first
    expect(spinner).to_be_visible()
