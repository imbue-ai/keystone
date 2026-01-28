"""Integration tests for the forking functionality."""

import json
from pathlib import Path

from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.agents.default.claude_code_sdk.constants import CLAUDE_DIRECTORY
from sculptor.agents.default.claude_code_sdk.constants import CLAUDE_JSON_FILENAME
from sculptor.agents.default.claude_code_sdk.constants import CLAUDE_LOCAL_SETTINGS_FILENAME
from sculptor.agents.default.claude_code_sdk.constants import COMMANDS_DIRECTORY
from sculptor.constants import ElementIDs
from sculptor.testing.elements.chat_panel import expect_message_to_have_role
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.user_stories import user_story


@user_story("local claude settings are respected in tasks")
def test_claude_settings_propagate_from_users_computer_to_container(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that modifications of local claude settings get propagated to the container."""
    TEST_FILE_NAME = "test_file.py"
    HELLO_WORLD_CONTENT = 'print("hello world")'
    CREATE_FILE_PROMPT = f"Create a file called {TEST_FILE_NAME} with content '{HELLO_WORLD_CONTENT}'. Do NOT commit."
    LOCAL_SETTINGS_FILENAME = str(Path(CLAUDE_DIRECTORY) / CLAUDE_LOCAL_SETTINGS_FILENAME)

    pure_local_repo_.write_file(".gitignore", ".claude/settings.local.json")
    pure_local_repo_.commit(".gitignore commit", commit_time="2025-01-01T00:00:02")
    # TODO: remove this line after the config service properly watches for new directories, too.
    pure_local_repo_.write_file(LOCAL_SETTINGS_FILENAME, "{}")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # 1. Create a task with vanilla settings.
    create_task(
        task_starter=task_starter,
        task_text=CREATE_FILE_PROMPT,
    )

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    parent_task = only(tasks.all())
    task_page = navigate_to_task_page(task=parent_task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    task_page.verify_uncommitted_file(file_name=TEST_FILE_NAME, expected_content=HELLO_WORLD_CONTENT)

    # 2. Update the settings to clean uncommited changes after each tool use.
    GIT_CLEAN_HOOK = """
        {
          "hooks": {
            "PreToolUse": [
              {
                "matcher": "*",
                "hooks": [
                  {
                    "type": "command",
                    "command": "git clean -f",
                    "timeout": 8
                  }
                ]
              }
            ]
          }
        }
    """
    pure_local_repo_.write_file(LOCAL_SETTINGS_FILENAME, GIT_CLEAN_HOOK)

    # 3. Trigger tool use and verify that the hook defined in the settings got executed inside the container.
    send_chat_message(
        chat_panel=chat_panel,
        message="What is the number of environment variables in the current process?",
    )
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
    task_page.verify_uncommitted_file_count(0)


@user_story("local claude custom slash commands can be used from the frontend")
def test_claude_custom_slash_commands_can_be_used(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that custom slash commands defined in a local claude directory can be used from the frontend, including arguments."""
    SLASH_COMMAND_FILENAME = str(Path(CLAUDE_DIRECTORY) / COMMANDS_DIRECTORY / "count.md")
    SLASH_COMMAND_DEFINITION = "What is the number of $1 $2 in the current process?"
    pure_local_repo_.write_file(SLASH_COMMAND_FILENAME, SLASH_COMMAND_DEFINITION)
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    create_task(
        task_starter=task_starter,
        task_text="/count environment variables",
    )

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)
    parent_task = only(tasks.all())
    task_page = navigate_to_task_page(task=parent_task)
    chat_panel = task_page.get_chat_panel()
    messages = chat_panel.get_messages()
    agent_message = messages.nth(1)
    expect_message_to_have_role(message=agent_message, role=ElementIDs.ASSISTANT_MESSAGE)
    expect(agent_message).to_contain_text("current process")


@user_story("unknown slash commands result in a warning")
def test_claude_unknown_slash_commands_result_in_a_warning(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that custom slash commands defined in a local claude directory can be used from the frontend."""
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    create_task(
        task_starter=task_starter,
        task_text="Say hi to me",
    )

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    parent_task = only(tasks.all())
    task_page = navigate_to_task_page(task=parent_task)
    chat_panel = task_page.get_chat_panel()

    send_chat_message(
        chat_panel=chat_panel,
        message="/unknown_command",
    )

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
    messages = chat_panel.get_messages()
    agent_message = messages.nth(3)
    expect_message_to_have_role(message=agent_message, role=ElementIDs.ASSISTANT_MESSAGE)
    expect(agent_message).to_contain_text("Warning")
    expect(agent_message).to_contain_text("Invalid slash command")


@user_story("local claude configuration changes are picked up after restart")
def test_claude_configuration_changes_are_picked_up_after_restart(
    sculptor_factory_: SculptorFactory, pure_local_repo_: MockRepoState, tmp_path: Path
) -> None:
    """Test that modifications of local claude settings get propagated to the container after a restart."""

    slash_command_filename = tmp_path / CLAUDE_DIRECTORY / COMMANDS_DIRECTORY / "count.md"
    slash_command_filename.parent.mkdir(parents=True, exist_ok=True)
    slash_command_filename.write_text("What is the number of env vars in the current process?")
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        task_starter = home_page.get_task_starter()
        create_task(
            task_starter=task_starter,
            task_text="Say hi to me",
        )
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        wait_for_tasks_to_finish(task_list=task_list)

    slash_command_filename = tmp_path / CLAUDE_DIRECTORY / COMMANDS_DIRECTORY / "day_of_the_week.md"
    slash_command_filename.parent.mkdir(parents=True, exist_ok=True)
    slash_command_filename.write_text("What is the current day of the week?")
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        # home_page.reload()
        task_list = home_page.get_task_list()
        wait_for_tasks_to_finish(task_list=task_list)
        tasks = task_list.get_tasks()
        parent_task = only(tasks.all())
        task_page = navigate_to_task_page(task=parent_task)
        chat_panel = task_page.get_chat_panel()
        send_chat_message(
            chat_panel=chat_panel,
            # We don't need an actual response from Claude; the purpose is just to wait until the messages are processed.
            message="/non_existing_command",
        )
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
        chat_input = chat_panel.get_chat_input()
        # This actually also tests that the mention component works well and shows the new slash command.
        expect(home_page.locator("html")).not_to_contain_text("day_of_the_week")
        chat_input.type("/")
        expect(home_page.locator("html")).to_contain_text("day_of_the_week")


@user_story("claude mcp server settings from the users machine are respected in tasks")
def test_claude_mcp_server_settings_propagate_from_users_computer_to_container(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState, tmp_path: Path
) -> None:
    claude_json_path = tmp_path / CLAUDE_JSON_FILENAME
    assert not claude_json_path.exists()
    claude_config = {
        "numStartups": 3,
        "theme": "light",
        "customApiKeyResponses": {
            "approved": [],
            "rejected": [],
        },
        "firstStartTime": "2025-06-10T21:50:05.520Z",
        "projects": {},
        "isQualifiedForDataSharing": False,
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "1.0.17",
        "recommendedSubscription": "",
        "subscriptionNoticeCount": 0,
        "hasAvailableSubscription": False,
        # This is the important part (the rest above is here only because it seems to be required).
        "mcpServers": {
            "dummy": {
                "type": "http",
                "url": "https://example.com",
            }
        },
    }
    claude_json_path.write_text(json.dumps(claude_config))
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    create_task(
        task_starter=task_starter,
        # We don't actually want claude to use the listmcpresources tool because that sends requests to the mcp server.
        task_text="Looking at /root/.claude.json, tell me which mcp servers are currently configured.",
    )
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)
    parent_task = only(tasks.all())
    task_page = navigate_to_task_page(task=parent_task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    messages = chat_panel.get_messages()
    agent_message = messages.nth(1)
    expect(agent_message).to_contain_text("dummy")
