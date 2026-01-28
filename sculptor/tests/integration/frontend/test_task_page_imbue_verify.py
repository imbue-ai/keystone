"""Integration tests for Task Page - Imbue Verify functionality."""

import textwrap

import pytest
from playwright.sync_api import expect

from sculptor.constants import ElementIDs
from sculptor.testing.decorators import mark_acceptance_test
from sculptor.testing.elements.chat_panel import expect_message_to_have_role
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.playwright_utils import start_task_and_wait_for_ready
from sculptor.testing.user_stories import user_story


# PROD-1545: Implement and verify LLM caching in tests for `imbue-cli`
@user_story("the agent to have access to Imbue's verification functionality")
@mark_acceptance_test
def test_agent_invokes_verify(
    testing_mode_: str, sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    faulty_file_content = textwrap.dedent(
        """\
    def process_data():
        raise NotImplementedError()

    def main():
        process_data()
        print("Data processed successfully.")
    """
    )

    pure_local_repo_.write_file("src/data_processor.py", faulty_file_content)
    pure_local_repo_.stage_all_changes()

    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        "Please verify the data processing code in src/data_processor.py using the verify tool. Do not use the `verify_slow` tool.",
    )

    messages = task_page.get_chat_panel().get_messages()

    # messages.last contains all the consecutive agent messages including tool calls
    last_message = messages.last
    expect_message_to_have_role(last_message, ElementIDs.ASSISTANT_MESSAGE)
    # TODO: do better
    expect(last_message).to_contain_text("verify")

    # click the verify tool header to reveal the action outputs
    verify_tool_headers = task_page.get_chat_panel().get_by_test_id(ElementIDs.TOOL_HEADER).filter(has_text="erif")
    verify_tool_headers.last.click()

    expect(task_page.get_chat_panel().get_by_test_id("NO_ACTION_OUTPUTS")).to_have_count(0)


@pytest.mark.skip("This should work, but snapshot updates are broken currently.")
@user_story("I want to get feedback when imbue_verify fails to run.")
def test_agent_invokes_verify_with_empty_diff_shows_error(
    testing_mode_: str, sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that imbue_verify shows proper error title and description when there are no changes to verify."""

    pure_local_repo_.write_file("src/example.py", "def hello():\n    return 'world'")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Initial commit")

    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        "Please verify the code using the verify tool.",
    )

    messages = task_page.get_chat_panel().get_messages()
    last_message = messages.last
    expect_message_to_have_role(last_message, ElementIDs.ASSISTANT_MESSAGE)
    expect(last_message).to_contain_text("verify")

    verify_tool_headers = task_page.get_chat_panel().get_by_test_id(ElementIDs.TOOL_HEADER).filter(has_text="erif")
    verify_tool_headers.last.click()

    tool_content = task_page.get_chat_panel().get_by_test_id("TOOL_CONTENT")
    expect(tool_content).to_contain_text("Unable to complete requested action.")
    expect(tool_content).to_contain_text("No code changes detected")


@pytest.mark.skip("This should work, but snapshot updates are broken currently.")
@user_story("I want to get feedback when imbue_verify fails to run due to large diff.")
def test_agent_invokes_verify_with_large_diff_shows_error(
    testing_mode_: str, sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that imbue_verify shows proper error when diff is too large."""

    large_content = "def hello():\n    return 'world'\n" * 50000
    pure_local_repo_.write_file("src/example.py", large_content)
    pure_local_repo_.stage_all_changes()

    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        "Please verify the code using the verify tool.",
    )

    messages = task_page.get_chat_panel().get_messages()
    last_message = messages.last
    expect_message_to_have_role(last_message, ElementIDs.ASSISTANT_MESSAGE)
    expect(last_message).to_contain_text("verify")

    verify_tool_headers = task_page.get_chat_panel().get_by_test_id(ElementIDs.TOOL_HEADER).filter(has_text="erif")
    verify_tool_headers.last.click()

    tool_content = task_page.get_chat_panel().get_by_test_id("TOOL_CONTENT")
    expect(tool_content).to_contain_text("Unable to complete requested action.")
    expect(tool_content).to_contain_text("The diff is too large")


# PROD-1545: Implement and verify LLM caching in tests for `imbue-cli`
@pytest.mark.skip("Imbue's retrieve tool needs to be configured in the local tools.toml first")
@mark_acceptance_test
def test_agent_invokes_retrieve(
    testing_mode_: str, sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    file_content_1 = textwrap.dedent(
        """\
    from src import process_data

    def main():
        process_data()
        print("lorem ipsum dolor sit amet")
    """
    )
    pure_local_repo_.write_file("src/lorem_2.py", file_content_1)

    file_content_2 = textwrap.dedent(
        """\
    def main():
        print("haha! no lorem ipsum here")
    """
    )
    pure_local_repo_.write_file("src/lorem_1.py", file_content_2)

    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add data processing")

    task_page = start_task_and_wait_for_ready(
        sculptor_page_,
        "Please use the retrieve tool to find which file prints 'lorem ipsum dolor sit amet'.",
    )

    messages = task_page.get_chat_panel().get_messages()

    last_message = messages.last
    expect_message_to_have_role(last_message, ElementIDs.ASSISTANT_MESSAGE)
    expect(last_message).to_contain_text("lorem_2")
    expect(last_message).not_to_contain_text("lorem_1")
