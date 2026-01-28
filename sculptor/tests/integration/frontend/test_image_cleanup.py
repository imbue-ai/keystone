"""Integration tests for Docker image cleanup functionality."""

import json

from playwright.sync_api import Page
from playwright.sync_api import expect

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.itertools import only
from imbue_core.processes.local_process import run_blocking
from sculptor.services.environment_service.environments.image_tags import get_current_sculptor_images_info
from sculptor.services.environment_service.environments.image_tags import get_v1_image_ids_and_metadata_for_task
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import delete_task
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.playwright_utils import navigate_to_frontend
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.server_utils import SculptorServer


def trigger_image_cleanup(sculptor_page: Page, sculptor_server: SculptorServer) -> None:
    # Now trigger the image cleanup via the testing route
    response = sculptor_page.request.post(f"{sculptor_server.url}/api/v1/testing/cleanup-images")
    assert response.ok, f"Cleanup request failed with status {response.status}"


def _get_docker_image_ids() -> tuple[str, ...]:
    result = run_blocking(command=["docker", "images", "--no-trunc", "--format", "json"])
    image_ids = set()
    for line in result.stdout.strip().splitlines():
        if line.strip():
            full_image_id = json.loads(line)["ID"]
            image_ids.add(full_image_id.split(":")[1].strip())
    return tuple(image_ids)


def test_cleanup_simple(
    sculptor_factory_: SculptorFactory, test_root_concurrency_group: ConcurrencyGroup, container_prefix_: str
) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        home_page = PlaywrightHomePage(page=sculptor_page)

        # Create a simple task
        task_text = "Hello, this is test message 1 of 1! Please do nothing."
        task_starter = home_page.get_task_starter()
        create_task(task_starter=task_starter, task_text=task_text)

        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)

        wait_for_tasks_to_build(task_list=task_list)

        # Wait for it to become ready
        wait_for_tasks_to_finish(task_list=task_list)

        # Get the task from the task list
        task = only(tasks.all())

        # Navigate to task and verify initial state
        task_page = navigate_to_task_page(task=task)
        chat_panel = task_page.get_chat_panel()
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

        images_before = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        trigger_image_cleanup(sculptor_page, sculptor_server)
        images_after = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        assert set(images_before) == set(images_after), "No images should be deleted"


def test_cleanup_historical_images(
    sculptor_factory_: SculptorFactory, test_root_concurrency_group: ConcurrencyGroup, container_prefix_: str
) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        home_page = PlaywrightHomePage(page=sculptor_page)

        # Create a simple task
        task_text = "Hello, this is test message 1 of 2! Please do nothing."
        task_starter = home_page.get_task_starter()
        create_task(task_starter=task_starter, task_text=task_text)

        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)

        wait_for_tasks_to_build(task_list=task_list)

        # Wait for it to become ready
        wait_for_tasks_to_finish(task_list=task_list)

        # Get the task from the task list
        task = only(tasks.all())

        # Navigate to task and verify initial state
        task_page = navigate_to_task_page(task=task)
        chat_panel = task_page.get_chat_panel()
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

        send_chat_message(
            chat_panel=chat_panel,
            message="Hello this is test message 2 of 2. Please do nothing ",
        )
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "2")

        images_before = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        trigger_image_cleanup(sculptor_page, sculptor_server)
        images_after = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        assert set(images_before) == set(images_after), "No images should be deleted"


def test_cleanup_keeps_running_and_latest_images(
    sculptor_factory_: SculptorFactory, test_root_concurrency_group: ConcurrencyGroup, container_prefix_: str
) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        home_page = PlaywrightHomePage(page=sculptor_page)

        # Create a simple task
        task_text = "Hello, this is test message 1 of 2! Please do nothing."
        task_starter = home_page.get_task_starter()
        create_task(task_starter=task_starter, task_text=task_text)

        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)

        wait_for_tasks_to_build(task_list=task_list)

        # Wait for it to become ready
        wait_for_tasks_to_finish(task_list=task_list)

        # Get the task from the task list
        task = only(tasks.all())

        # Navigate to task and verify initial state
        task_page = navigate_to_task_page(task=task)
        chat_panel = task_page.get_chat_panel()
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        home_page = PlaywrightHomePage(page=sculptor_page)
        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        # Get the task from the task list
        task = only(tasks.all())

        # Navigate to task and verify initial state
        task_page = navigate_to_task_page(task=task)
        chat_panel = task_page.get_chat_panel()
        send_chat_message(
            chat_panel=chat_panel,
            message="Hello this is test message 2 of 2. Please do nothing ",
        )
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "2")

        images_before = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        trigger_image_cleanup(sculptor_page, sculptor_server)
        images_after = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        assert set(images_before) == set(images_after), "No images should be deleted"


def test_cleanup_with_multiple_tasks(
    sculptor_factory_: SculptorFactory, test_root_concurrency_group: ConcurrencyGroup, container_prefix_: str
) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        home_page = PlaywrightHomePage(page=sculptor_page)

        # Create a simple task
        first_task_text = "Hello, this is test message 1 of 2! Please do nothing."
        task_starter = home_page.get_task_starter()
        create_task(task_starter=task_starter, task_text=first_task_text)

        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        second_task_text = "Hello, this is a test message! Please do nothing."
        create_task(task_starter=task_starter, task_text=second_task_text)
        expect(tasks).to_have_count(2)

        wait_for_tasks_to_build(task_list=task_list)

        # Wait for it to become ready
        wait_for_tasks_to_finish(task_list=task_list)

        # Get the task from the task list
        task = tasks.all()[-1]

        # Navigate to task and verify initial state
        task_page = navigate_to_task_page(task=task)
        chat_panel = task_page.get_chat_panel()
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

        send_chat_message(
            chat_panel=chat_panel,
            message="Hello this is test message 2 of 2. Please do nothing ",
        )
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "2")
        chat_panel.get_attribute("data-taskid")

        images_before = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        trigger_image_cleanup(sculptor_page, sculptor_server)
        images_after = get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        assert set(images_before) == set(images_after), "No images should be deleted"


def test_cleanup_images_for_deleted_tasks(
    sculptor_factory_: SculptorFactory, test_root_concurrency_group: ConcurrencyGroup, container_prefix_: str
) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        home_page = PlaywrightHomePage(page=sculptor_page)

        # Create a simple task
        first_task_text = "Hello, this is test message 1 of 2! Please do nothing."
        task_starter = home_page.get_task_starter()
        create_task(task_starter=task_starter, task_text=first_task_text)

        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)

        wait_for_tasks_to_build(task_list=task_list)

        # Wait for it to become ready
        wait_for_tasks_to_finish(task_list=task_list)

        # Get the task from the task list
        task = only(tasks.all())

        # Navigate to task and verify initial state
        task_page = navigate_to_task_page(task=task)
        chat_panel = task_page.get_chat_panel()
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")
        send_chat_message(
            chat_panel=chat_panel,
            message="Hello this is test message 2 of 2. Please do nothing ",
        )
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "2")
        task_id = TaskID(chat_panel.get_attribute("data-taskid"))
        task_image_ids, _ = zip(
            *get_v1_image_ids_and_metadata_for_task(task_id, test_root_concurrency_group, container_prefix_)
        )

        navigate_to_frontend(page=sculptor_page, url=sculptor_server.url)
        # Delete the task
        delete_task(task=task)
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(0)

        trigger_image_cleanup(sculptor_page, sculptor_server)

        # Deleting a task should automatically delete all associated images
        current_image_ids = _get_docker_image_ids()
        assert all(image_id not in current_image_ids for image_id in task_image_ids), (
            "All images should not be in the current image ids"
        )
