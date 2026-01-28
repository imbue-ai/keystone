"""Integration tests for Task Page - MCP & Tools functionality."""

import pytest
from playwright.sync_api import expect

from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.playwright_utils import start_task_and_wait_for_ready
from sculptor.testing.user_stories import user_story


# FIXME: delete these tests if we are happy with this MR. Will sync with capabilities first based on loss of coverage.
@pytest.mark.skip(reason="Not displaying MCP status to users on launch")
@user_story("to inspect the MCP servers and tools available to the agent")
def test_default_mcp_server_starts(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that MCP servers start correctly and are visible in the UI."""
    task_page = start_task_and_wait_for_ready(sculptor_page_, "Hello, this is a test message! Please respond briefly!")

    mcp_servers_button = task_page.get_task_header().get_mcp_servers_button()

    expect(mcp_servers_button).to_be_visible()
    expect(mcp_servers_button, "color and icon should indicate success").to_have_attribute(
        "data-accent-color", "green"
    )


# FIXME: delete these tests if we are happy with this MR. Will sync with capabilities first based on loss of coverage.
@pytest.mark.skip(reason="Not displaying MCP status to users on launch")
@user_story("to inspect the MCP servers and tools available to the agent")
def test_mcp_server_starts_with_tools(sculptor_page_: PlaywrightHomePage) -> None:
    task_page = start_task_and_wait_for_ready(
        sculptor_page_, "Hello, this is a test message! Please respond briefly!!!!"
    )

    modal = task_page.get_task_header().open_mcp_server_modal()

    # NOTE: these have an opportunity for false successes if there's overlap
    #       between the server name and the expected tool names
    # TODO: attach the server info into the custom data-servers attribute or
    #       parse the frontend more precisely
    expect(modal, "to include the name of our MCP server and have a good connection status").to_contain_text(
        " imbue connected ", use_inner_text=True
    )
    for tool_name in ["check", "verify"]:
        expect(modal, f"to include the '{tool_name}' tool as available").to_contain_text(
            f" {tool_name} ", use_inner_text=True
        )
