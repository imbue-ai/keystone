import time

import pytest
from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.decorators import flaky
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.user_stories import user_story

SECONDS_MS = 1000


@pytest.mark.skip("PROD-3551: Restarting the sculptor factory loses the selections")
@user_story("my selections to stay on backend restarts")
def test_home_page_prompts_persist_on_restart(sculptor_factory_: SculptorFactory) -> None:
    testing_prompt = "testing prompt"
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        task_starter = home_page.get_task_starter()
        task_starter.get_task_input().type(testing_prompt)
        time.sleep(0.5)

    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        task_starter = home_page.get_task_starter()
        expect(task_starter.get_task_input()).to_contain_text(testing_prompt, timeout=3 * SECONDS_MS)


@user_story("my progress to stay on backend restarts")
def test_tasks_persist_on_restart(sculptor_factory_: SculptorFactory) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        create_task(task_starter=home_page.get_task_starter(), task_text="Say hi to me")
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        wait_for_tasks_to_finish(task_list=task_list)
        task_page = navigate_to_task_page(task=only(tasks.all()))
        chat_panel = task_page.get_chat_panel()
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1, timeout=3 * SECONDS_MS)
        wait_for_tasks_to_finish(task_list=task_list)


# TODO [PROD-2117]: unmark as flaky
@flaky
@user_story("my progress to stay on backend restarts")
def test_chats_persist_on_restart(sculptor_factory_: SculptorFactory) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        create_task(task_starter=home_page.get_task_starter(), task_text="Say hi to me")
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        wait_for_tasks_to_finish(task_list=task_list)
        task_page = navigate_to_task_page(task=only(tasks.all()))

    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, home_page):
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        wait_for_tasks_to_finish(task_list=task_list)
        task_page = navigate_to_task_page(task=only(tasks.all()))
        send_chat_message(chat_panel=task_page.get_chat_panel(), message="Say bye to me")
        wait_for_completed_message_count(chat_panel=task_page.get_chat_panel(), expected_message_count=4)
