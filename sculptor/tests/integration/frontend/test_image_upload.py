import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Sequence

import pytest
from PIL import Image
from playwright.sync_api import Locator
from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.constants import ElementIDs
from sculptor.testing.elements.chat_panel import PlaywrightChatPanelElement
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import delete_task
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_modal import PlaywrightTaskModalElement
from sculptor.testing.elements.task_starter import PlaywrightTaskStarterElement
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.launch_mode import LaunchMode
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.task_page import PlaywrightTaskPage
from sculptor.testing.user_stories import user_story


def _create_test_image(color: tuple[int, int, int]) -> Generator[str, None, None]:
    """Create a temporary test image with the specified color and clean it up after use.

    Args:
        color: RGB tuple (e.g., (255, 0, 0) for red)

    Yields:
        Path to the temporary test image file
    """
    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_path = temp_file.name
    temp_file.close()

    img = Image.new("RGB", (100, 100), color)
    img.save(temp_path)

    yield temp_path

    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture
def test_image_red_() -> Generator[str, None, None]:
    yield from _create_test_image((255, 0, 0))


@pytest.fixture
def test_image_green_() -> Generator[str, None, None]:
    yield from _create_test_image((0, 255, 0))


@pytest.fixture
def test_image_blue_() -> Generator[str, None, None]:
    yield from _create_test_image((0, 0, 255))


def _attach_image_and_verify_preview(
    element: PlaywrightTaskStarterElement | PlaywrightChatPanelElement | PlaywrightTaskModalElement,
    images: str | Sequence[str],
    expected_count: int = 1,
) -> Locator:
    element.attach_files(images)
    image_previews = element.get_file_previews()
    expect(image_previews).to_have_count(expected_count)
    return image_previews


def _verify_image_in_message(
    chat_panel: PlaywrightChatPanelElement, message_index: int, expected_image_count: int = 1
) -> Locator:
    messages = chat_panel.get_messages()
    user_message = messages.nth(message_index)
    expect(user_message).to_have_attribute("data-testid", ElementIDs.USER_MESSAGE)

    image_in_message = user_message.locator('img[alt^="Attachment"]')
    expect(image_in_message).to_have_count(expected_image_count)
    return image_in_message


def _create_task_and_navigate(
    home_page: PlaywrightHomePage, task_starter: PlaywrightTaskStarterElement, task_text: str, task_index: int = 0
) -> tuple[PlaywrightTaskPage, PlaywrightChatPanelElement]:
    create_task(task_starter=task_starter, task_text=task_text)

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    wait_for_tasks_to_finish(task_list=task_list)

    task = tasks.nth(task_index)
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()

    return task_page, chat_panel


@pytest.mark.skip(reason="FIXME(bry): This test doesn't pass in CI")
@user_story("to attach images from the home page task starter")
def test_image_upload_from_create_task_form(
    sculptor_page_: PlaywrightHomePage, test_image_red_: str, sculptor_launch_mode_: LaunchMode
) -> None:
    """Test that users can attach images when creating a task from the home page."""
    if not sculptor_launch_mode_.is_electron():
        pytest.skip("This test only works when using an Electron build of")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    _attach_image_and_verify_preview(task_starter, test_image_red_)

    task_text = "Describe this image in detail."
    task_page, chat_panel = _create_task_and_navigate(home_page, task_starter, task_text)

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    _verify_image_in_message(chat_panel, message_index=0)


@pytest.mark.skip(reason="FIXME(bry): This test doesn't pass in CI")
@user_story("to attach images from the chat input")
def test_image_upload_from_chat_input(
    sculptor_page_: PlaywrightHomePage, test_image_green_: str, sculptor_launch_mode_: LaunchMode
) -> None:
    """Test that users can attach images when sending messages in an existing task."""
    if not sculptor_launch_mode_.is_electron():
        pytest.skip("This test only works when using an Electron build of")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    task_page, chat_panel = _create_task_and_navigate(home_page, task_starter, "Say hello!")
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    _attach_image_and_verify_preview(chat_panel, test_image_green_)
    send_chat_message(chat_panel=chat_panel, message="What's in this image?")
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
    _verify_image_in_message(chat_panel, message_index=2)


@pytest.mark.skip(reason="FIXME(bry): This test doesn't pass in CI")
@user_story("to upload multiple images in a single message")
def test_multiple_image_upload(
    sculptor_page_: PlaywrightHomePage,
    test_image_red_: str,
    test_image_blue_: str,
    test_image_green_: str,
    sculptor_launch_mode_: LaunchMode,
) -> None:
    """Test that users can attach multiple images to a single message."""
    if not sculptor_launch_mode_.is_electron():
        pytest.skip("This test only works when using an Electron build of")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    _attach_image_and_verify_preview(
        task_starter, [test_image_red_, test_image_blue_, test_image_green_], expected_count=3
    )
    task_text = "Compare these three images."
    task_page, chat_panel = _create_task_and_navigate(home_page, task_starter, task_text)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    _verify_image_in_message(chat_panel, message_index=0, expected_image_count=3)


@pytest.mark.skip(reason="FIXME(bry): This test doesn't pass in CI")
@user_story("to see uploaded images persist in chat history")
def test_image_persistence_in_chat_history(
    sculptor_page_: PlaywrightHomePage, test_image_red_: str, sculptor_launch_mode_: LaunchMode
) -> None:
    """Test that uploaded images persist in chat history after page reload."""
    if not sculptor_launch_mode_.is_electron():
        pytest.skip("This test only works when using an Electron build of")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    _attach_image_and_verify_preview(task_starter, test_image_red_)

    task_text = "Describe this image."
    task_page, chat_panel = _create_task_and_navigate(home_page, task_starter, task_text)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    _verify_image_in_message(chat_panel, message_index=0)
    task_page.reload()
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    _verify_image_in_message(chat_panel, message_index=0)


@pytest.mark.skip(reason="FIXME(bry): This test doesn't pass in CI")
@user_story("to attach images from the task modal")
def test_image_upload_from_task_modal(
    sculptor_page_: PlaywrightHomePage, test_image_blue_: str, sculptor_launch_mode_: LaunchMode
) -> None:
    """Test that users can attach images when creating a task from the task modal."""
    if not sculptor_launch_mode_.is_electron():
        pytest.skip("This test only works when using an Electron build of")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    task_page, chat_panel = _create_task_and_navigate(home_page, task_starter, "Initial task")

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    new_agent_modal_button = task_page.get_by_test_id(ElementIDs.NEW_AGENT_BUTTON)
    new_agent_modal_button.click()

    task_modal = PlaywrightTaskModalElement(
        locator=sculptor_page_.get_by_test_id(ElementIDs.TASK_MODAL), page=sculptor_page_
    )
    expect(task_modal).to_be_visible()

    _attach_image_and_verify_preview(task_modal, test_image_blue_)

    task_modal.get_input_element().type("Analyze this image")
    task_modal.start_task()

    expect(task_modal).not_to_be_visible()

    task_page.navigate_to_home()
    home_page = sculptor_page_
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)

    wait_for_tasks_to_finish(task_list=task_list)

    new_task = tasks.nth(0)
    task_page = navigate_to_task_page(task=new_task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    _verify_image_in_message(chat_panel, message_index=0)


@pytest.mark.skip(reason="FIXME(bry): This test doesn't pass in CI")
@user_story("to remove attached images before sending")
def test_remove_attached_image(
    sculptor_page_: PlaywrightHomePage, test_image_red_: str, test_image_green_: str, sculptor_launch_mode_: LaunchMode
) -> None:
    """Test that users can remove attached images before sending a message."""
    if not sculptor_launch_mode_.is_electron():
        pytest.skip("This test only works when using an Electron build of")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    _attach_image_and_verify_preview(task_starter, [test_image_red_, test_image_green_], expected_count=2)
    task_starter.remove_file(index=0)
    expect(task_starter.get_file_previews()).to_have_count(1)

    task_text = "Describe this image."
    task_page, chat_panel = _create_task_and_navigate(home_page, task_starter, task_text)

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    _verify_image_in_message(chat_panel, message_index=0, expected_image_count=1)


@pytest.mark.skip(reason="FIXME(bry): This test doesn't pass in CI")
@user_story("to have images deleted when a task is deleted")
def test_images_deleted_when_task_deleted(
    sculptor_page_: PlaywrightHomePage, test_image_red_: str, test_image_green_: str, sculptor_launch_mode_: LaunchMode
) -> None:
    """Test that image files are deleted from disk when a task is deleted."""
    if not sculptor_launch_mode_.is_electron():
        pytest.skip("This test only works when using an Electron build of")

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    _attach_image_and_verify_preview(task_starter, [test_image_red_, test_image_green_], expected_count=2)
    task_text = "Describe these images."
    task_page, chat_panel = _create_task_and_navigate(home_page, task_starter, task_text)

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    messages = chat_panel.get_messages()
    user_message = messages.nth(0)
    images = user_message.locator('img[alt^="Attachment"]')
    expect(images).to_have_count(2)

    # Extract image paths from src attributes
    image_paths = []
    for i in range(2):
        image_path = images.nth(i).get_attribute("data-path")
        image_paths.append(Path(image_path))

    # Verify images exist on disk before deletion
    assert len(image_paths) == 2, "Should have extracted 2 image paths"
    for image_path in image_paths:
        assert image_path.exists(), f"Image should exist at {image_path}"

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)
    task = only(tasks.all())

    delete_task(task=task)
    expect(tasks).to_have_count(0)

    # Verify images no longer exist on disk
    for image_path in image_paths:
        assert not image_path.exists(), f"Image should be deleted at {image_path}"
