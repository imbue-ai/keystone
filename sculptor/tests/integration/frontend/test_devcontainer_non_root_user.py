"""Integration tests for non-root devcontainer user functionality."""

from __future__ import annotations

from typing import Final
from typing import Generator

import pytest
from playwright.sync_api import expect

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.itertools import only
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_default_devcontainer_image_reference,
)
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.repo_resources import generate_test_project_repo
from sculptor.testing.user_stories import user_story

_DEVCONTAINER_CONTENTS: Final[str] = """
{
  "name": "Non-Root User Development",
  "build": {
    "dockerfile": "../Dockerfile",
    "context": ".."
  }
}
"""


def _get_dockerfile_contents() -> str:
    """Generate Dockerfile that parents from default image and adds a non-root user."""
    default_image = get_default_devcontainer_image_reference()
    return f"""
FROM {default_image}

# Create a non-root user without sudo permissions
RUN adduser --disabled-password devuser

# # Switch to the non-root user
USER devuser
RUN cd && whoami > ./whoami.txt
"""


@pytest.fixture
def pure_local_repo_(
    request: pytest.FixtureRequest, test_root_concurrency_group: ConcurrencyGroup
) -> Generator[MockRepoState, None, None]:
    """Creates a local repository with a devcontainer that uses a non-root user without sudo."""
    with generate_test_project_repo(request, test_root_concurrency_group) as repo:
        repo.create_reset_and_checkout_branch("testing")
        repo.write_file("Dockerfile", _get_dockerfile_contents())
        repo.write_file(".devcontainer/devcontainer.json", _DEVCONTAINER_CONTENTS)
        repo.stage_all_changes()
        repo.commit("Add non-root devcontainer", commit_time="2025-01-01T00:00:01")
        yield repo


@user_story("to use a devcontainer with a non-root user without sudo permissions")
def test_artifact_panel_diff_tab_non_root_user(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the artifact panel diff tab works correctly with a non-root devcontainer user."""

    home_page = sculptor_page_

    # Create a task that will generate a file
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Run `whoami`.")

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and wait for assistant to complete the file creation
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    # Wait for the assistant to complete (2 messages: user request + assistant response)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify the assistant's response contains "devuser" (the non-root user)
    messages = chat_panel.get_messages()
    assistant_response = messages.nth(1)  # Second message is the assistant's response
    expect(assistant_response).to_contain_text("devuser")
