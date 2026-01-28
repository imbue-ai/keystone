import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from playwright.sync_api import expect

from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_and_navigate_to_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.test_repo_factory import TestRepoFactory
from sculptor.testing.user_stories import user_story

SIMPLE_TASK_PROMPT = "Echo 'hello world'"
IDLE_OBSERVATION_SECONDS = 65
TASK_START_ALLOWED_LOG_BYTES = 2_000_000
SUBSEQUENT_MESSAGE_ALLOWED_LOG_BYTES = 400_000
PROJECT_CREATION_ALLOWED_LOG_BYTES = 300_000
IDLE_WITHOUT_SYNC_ALLOWED_LOG_BYTES = 500_000
IDLE_WITH_SYNC_ALLOWED_LOG_BYTES = 700_000


@contextmanager
def file_growth_limit(
    file_to_measure: Path, growth_limit_bytes: int, assertion_message: str
) -> Generator[None, None, None]:
    original_size = file_to_measure.stat().st_size
    yield
    assert file_to_measure.stat().st_size <= original_size + growth_limit_bytes, assertion_message


def _get_sculptor_logs_file(sculptor_folder: Path) -> Path:
    return sculptor_folder / "logs" / "server" / "logs.jsonl"


@user_story("to leave a task idle for a minute, without producing logs")
def test_idle_task_does_not_log(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
    sculptor_folder_: Path,
) -> None:
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    create_and_navigate_to_task(
        task_starter=task_starter,
        task_list=sculptor_page_.get_task_list(),
        task_text=SIMPLE_TASK_PROMPT,
    )
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    with file_growth_limit(
        _get_sculptor_logs_file(sculptor_folder_),
        IDLE_WITHOUT_SYNC_ALLOWED_LOG_BYTES,
        "Idle tasks should not produce logs",
    ):
        time.sleep(IDLE_OBSERVATION_SECONDS)


@user_story("to leave a task idle with local sync on, without producing logs")
def test_idle_task_with_active_local_sync_does_not_log(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
    sculptor_folder_: Path,
) -> None:
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    task_page = create_and_navigate_to_task(
        task_starter=task_starter,
        task_list=sculptor_page_.get_task_list(),
        task_text=SIMPLE_TASK_PROMPT,
    )
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Turn on local sync
    task_header = task_page.get_task_header()
    expect(task_header.get_sync_button()).to_have_attribute("sync-status", "INACTIVE")
    task_header.get_sync_button().click()
    expect(task_header.get_sync_button()).to_have_attribute("sync-status", "ACTIVE")

    with file_growth_limit(
        _get_sculptor_logs_file(sculptor_folder_),
        IDLE_WITH_SYNC_ALLOWED_LOG_BYTES,
        "Idle tasks in pairing mode should not produce logs",
    ):
        time.sleep(IDLE_OBSERVATION_SECONDS)


@user_story("to start and interact with a task without excessive logging")
def test_task_actions_produce_reasonably_sized_logs(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
    sculptor_folder_: Path,
) -> None:
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    log_file_path = _get_sculptor_logs_file(sculptor_folder_)

    with file_growth_limit(
        log_file_path,
        TASK_START_ALLOWED_LOG_BYTES,
        f"Task start should not add more than {TASK_START_ALLOWED_LOG_BYTES} bytes to the log",
    ):
        task_page = create_and_navigate_to_task(
            task_starter=task_starter,
            task_list=sculptor_page_.get_task_list(),
            task_text=SIMPLE_TASK_PROMPT,
        )
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        wait_for_tasks_to_finish(task_list=task_list)

    with file_growth_limit(
        log_file_path,
        SUBSEQUENT_MESSAGE_ALLOWED_LOG_BYTES,
        f"Sending a message on an existing task should not add more than {SUBSEQUENT_MESSAGE_ALLOWED_LOG_BYTES} bytes to the log",
    ):
        chat_panel = task_page.get_chat_panel()
        send_chat_message(chat_panel, SIMPLE_TASK_PROMPT)
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)


@user_story("to create a new project without excessive logging")
def test_project_creation_produces_reasonably_sized_logs(
    sculptor_page_: PlaywrightHomePage,
    test_repo_factory_: TestRepoFactory,
    sculptor_folder_: Path,
) -> None:
    other_project_name = "other project"
    other_branch_name = "other-branch"
    repo = test_repo_factory_.create_repo(name=other_project_name, branch=other_branch_name)

    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()

    with file_growth_limit(
        _get_sculptor_logs_file(sculptor_folder_),
        PROJECT_CREATION_ALLOWED_LOG_BYTES,
        f"Creating a project should not add more than {PROJECT_CREATION_ALLOWED_LOG_BYTES} bytes to the log",
    ):
        sidebar.create_project(project_path=repo.base_path, project_name=other_project_name)
