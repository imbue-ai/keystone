"""Integration tests for Sculptor tasks that use a custom devcontainer."""

from __future__ import annotations

from typing import Final
from typing import Generator

import pytest
from playwright.sync_api import expect

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.itertools import only
from sculptor.testing.decorators import mark_acceptance_test
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.repo_resources import generate_test_project_repo
from sculptor.testing.user_stories import user_story

assert mark_acceptance_test is not None, "Don't auto-remove this import."

_DEVCONTAINER_CONTENTS: Final[str] = """
{
  "name": "Crossfilter Development",
  "build": {
    "dockerfile": "../Dockerfile",
    "context": ".."
  },
  "customizations": {
    "vscode": {
      "extensions": [
        "ms-python.python"
      ]
    }
  }
}
"""

_DOCKERFILE_CONTENTS: Final[str] = """
FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1
# Even if we don't provide bash, we want to make sure it gets picked up form the Imbue control plane, since Claude needs it to work.
# We also don't provide git in this image, but Sculptor will need it to work and should pick it up from the Imbue control plane.
RUN apk add --no-cache python3 py3-pip jq
RUN python3 -m venv /venv
ENV VIRTUAL_ENV=/venv
ENV PATH="/venv/bin:$PATH"
RUN pip install --no-cache-dir uv
# Make the venv writeable by the Sculptor user.
RUN chmod a+rwX -R /venv
RUN which uv
RUN echo "Hello Imbue!" > /hello_sculptor.txt

# End the Dockerfile as a non-root user, to check that Sculptor's addons correctly switch back to root.
# Sculptor does require that it is possible to log in as the user.
# -D means "disabled password", so we don't have to set a password.
RUN adduser -D devuser
USER devuser
ENTRYPOINT ["python"]
"""

_PYPROJECT_TOML_CONTENTS: Final[str] = """
[project]
name = "dummy_project"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "pytest",
]

# Don't
[tool.pytest.ini_options]
# Eliminate all pytest output except for the return code, so that it doesn't print non-deterministic timing info.
addopts = "-q --disable-warnings --no-header --no-summary --durations=0 -p no:terminalreporter"
"""

_PYTEST_FILE_CONTENTS: Final[str] = """
def test_dummy():
    assert 2 + 2 == 5
"""


@pytest.fixture
def pure_local_repo_(
    request: pytest.FixtureRequest, test_root_concurrency_group: ConcurrencyGroup
) -> Generator[MockRepoState, None, None]:
    """Creates a local repository with a single commit that uses a devcontainer.

    The repo is constructed from scratch, so it's actually very fast."""
    with generate_test_project_repo(request, test_root_concurrency_group) as repo:
        repo.create_reset_and_checkout_branch("testing")
        repo.write_file("Dockerfile", _DOCKERFILE_CONTENTS)
        repo.write_file("pyproject.toml", _PYPROJECT_TOML_CONTENTS)
        repo.write_file(".devcontainer/devcontainer.json", _DEVCONTAINER_CONTENTS)
        repo.write_file("test_dummy.py", _PYTEST_FILE_CONTENTS)
        repo.stage_all_changes()
        repo.commit("Devcontainer commit", commit_time="2025-01-01T00:00:01")
        yield repo


@user_story("to use their own devcontainer and Dockerfile")
@mark_acceptance_test()
def test_devcontainer_exists_and_runs_commands(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that users can send messages after task starts, and the assistant responds.

    This test installs and fetches python and uv from the internet, so is too flaky for integration tests.
    """

    home_page = sculptor_page_

    # Create and start a task
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Can you please run `pwd`, `whoami`, `ls`, `echo $PATH`, `which bash`, `which python`, `which uv`, `which git`, "
        + "`jq keys /imbue/version.json`, `jq keys /imbue_addons/version.json`, `grep unknown /imbue_addons/version.json`, "
        + "and `cat /hello_sculptor.txt`?",
    )

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

    send_chat_message(
        chat_panel=chat_panel,
        # This prompt is designed to make the output from uv and pytest not contain any timing information,
        # which would invalidate the LLM prompt cache if it changes.
        message="Can you please run `uv sync -q --active && (pytest || echo 'pytest failed')` and fix the broken test?  "
        + "You will not see any output from pytest, and that's on purpose.  "
        + "Don't try to get any output from it other than its return code.  "
        + "Just read test_dummy.py and I think you'll figure it out, you're very smart.",
    )
    # Verify assistant has responded to both messages
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
