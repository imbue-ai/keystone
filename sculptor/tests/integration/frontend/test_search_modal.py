"""Integration tests for the Search Modal functionality."""

import pytest
from playwright.sync_api import expect

from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.pages.task_page import PlaywrightTaskPage


def test_open_search_modal_via_sidebar_button(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the search modal can be opened via the sidebar button."""
    home_page = sculptor_page_

    # Ensure sidebar is open and click search button
    sidebar = home_page.ensure_sidebar_is_open()
    sidebar.open_search_modal()

    # Verify search modal is visible
    search_modal = home_page.ensure_search_modal_is_open()
    expect(search_modal).to_be_visible()

    # Verify input is focused (autoFocus)
    search_input = search_modal.get_input_element()
    expect(search_input).to_be_focused()

    # Close the modal
    search_modal.close()
    expect(search_modal).not_to_be_visible()


def test_open_search_modal_via_keyboard_shortcut(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the search modal can be opened via keyboard shortcut (Cmd/Ctrl+P)."""
    home_page = sculptor_page_

    # Open search modal with keyboard shortcut
    search_modal = home_page.open_search_modal_with_keyboard()

    # Verify search modal is visible
    expect(search_modal).to_be_visible()

    # Verify input is focused
    search_input = search_modal.get_input_element()
    expect(search_input).to_be_focused()

    # Press escape to close
    search_modal.press_escape()
    expect(search_modal).not_to_be_visible()


def test_search_filtering_works(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that search filtering correctly filters tasks."""
    # Test data
    task_1_text = "Hello blue"
    task_1_title = "Task 1"
    task_2_text = "Hello green"
    task_2_title = "Task 2"
    task_3_text = "Hello purple"

    home_page = sculptor_page_

    # Create multiple tasks with different names
    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    create_task(task_starter=task_starter, task_text=task_1_text)
    wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=1)
    create_task(task_starter=task_starter, task_text=task_2_text)
    wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=2)
    create_task(task_starter=task_starter, task_text=task_3_text)
    wait_for_tasks_to_finish(task_list=task_list)

    # Open search modal
    search_modal = home_page.ensure_search_modal_is_open()

    # Verify all tasks are visible initially
    expect(search_modal.get_task_items()).to_have_count(3)

    # Search for "search"
    search_modal.type_text(text="green")
    expect(search_modal.get_task_items()).to_have_count(1)
    expect(search_modal.get_task_items().first).to_contain_text(task_2_title)

    # Search for "test"
    search_modal.type_text(text="blue")
    expect(search_modal.get_task_items()).to_have_count(1)
    expect(search_modal.get_task_items().first).to_contain_text(task_1_title)

    # Search for non-existent task
    search_modal.type_text(text="nonexistent")
    expect(search_modal.get_task_items()).to_have_count(0)
    search_modal.wait_for_no_tasks_message()

    # Clear search should show all tasks again
    search_modal.type_text(text="")
    expect(search_modal.get_task_items()).to_have_count(3)


def test_keyboard_navigation_works(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that keyboard navigation (arrow keys and Enter) works correctly."""
    # Test data
    task_1_text = "Hello blue"
    task_2_text = "Hello green"
    task_2_title = "Task 2"
    task_3_text = "Hello red"

    home_page = sculptor_page_

    # Create multiple tasks
    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    create_task(task_starter=task_starter, task_text=task_1_text)
    wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=1)
    create_task(task_starter=task_starter, task_text=task_2_text)
    wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=2)
    create_task(task_starter=task_starter, task_text=task_3_text)
    wait_for_tasks_to_finish(task_list=task_list)

    # Open search modal
    search_modal = home_page.ensure_search_modal_is_open()

    # First item should be selected by default
    search_modal.assert_x_selected(expected_index=0)

    # Navigate down
    search_modal.press_arrow_down()
    search_modal.assert_x_selected(expected_index=1)

    search_modal.press_arrow_down()
    search_modal.assert_x_selected(expected_index=2)

    # Navigate up
    search_modal.press_arrow_up()
    search_modal.assert_x_selected(expected_index=1)

    # Verify the second task is selected before pressing Enter
    task_items = search_modal.get_task_items()
    expect(task_items.nth(1)).to_contain_text(task_2_title)

    # Press Enter to select the second task
    search_modal.press_enter()

    # Should navigate to the task page
    task_page = PlaywrightTaskPage(page=sculptor_page_)
    expect(task_page.get_chat_panel()).to_be_visible()

    # Verify we're on the correct task page
    chat_panel = task_page.get_chat_panel()
    first_message = chat_panel.get_messages().first
    expect(first_message).to_contain_text(task_2_text)

    # Modal should be closed
    expect(search_modal).not_to_be_visible()


def test_mouse_interaction_works(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that mouse hover and click work correctly."""
    # Test data
    task_1_text = "Hello blue"
    task_1_title = "Task 1"
    task_2_text = "Hello green"
    task_2_title = "Task 2"

    home_page = sculptor_page_

    # Create multiple tasks
    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    create_task(task_starter=task_starter, task_text=task_1_text)
    wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=1)
    create_task(task_starter=task_starter, task_text=task_2_text)
    wait_for_tasks_to_finish(task_list=task_list)

    # Open search modal
    search_modal = home_page.ensure_search_modal_is_open()

    # Verify initial state - tasks are displayed newest first
    task_items = search_modal.get_task_items()
    expect(task_items.nth(0)).to_contain_text(task_2_title)
    expect(task_items.nth(1)).to_contain_text(task_1_title)

    # Hover over second task (task_1_text) should select it
    search_modal.hover_task_by_index(1)
    search_modal.assert_x_selected(expected_index=1)

    # Click on first task (task_2_text - newest)
    search_modal.select_task_by_index(0)

    # Should navigate to task and close modal
    task_page = PlaywrightTaskPage(page=sculptor_page_)
    expect(task_page.get_chat_panel()).to_be_visible()

    # Verify we navigated to the correct task
    chat_panel = task_page.get_chat_panel()
    first_message = chat_panel.get_messages().first
    expect(first_message).to_contain_text(task_2_text)

    expect(search_modal).not_to_be_visible()


@pytest.mark.skip(
    reason="Modal has issues with building containers, which causes this to time out more than it should"
)
def test_scroll_with_many_tasks(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that scrolling works when there are more than 5 visible tasks."""
    home_page = sculptor_page_

    # Create more than 5 tasks
    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    for i in range(6):
        create_task(task_starter=task_starter, task_text=f"Hello {i + 1}")
        wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=i + 1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Open search modal
    search_modal = home_page.ensure_search_modal_is_open()

    # Should only show 5 tasks initially (due to VISIBLE_ITEMS = 5)
    visible_tasks = search_modal.get_task_items()
    expect(visible_tasks).to_have_count(5)

    # Navigate down to the 5th task, which should be at the bottom of the visible list
    for i in range(4):
        initial_selected_index = search_modal.get_selected_task_index()
        search_modal.press_arrow_down()
        new_selected_index = search_modal.get_selected_task_index()

        # When navigating within the visible window (first 4 moves), index should increment
        assert new_selected_index == initial_selected_index + 1, (
            f"Expected index to increment from {initial_selected_index} to {initial_selected_index + 1}, but got {new_selected_index}"
        )

    # On the 5th move, we're at the last visible item, so window should scroll
    # and selected index should remain at the last visible position (4)
    for i in range(4, 6):
        search_modal.press_arrow_down()
        new_selected_index = search_modal.get_selected_task_index()
        assert new_selected_index == 4, (
            f"Expected index to remain at 4 when scrolling window, but got {new_selected_index}"
        )

    # Should still have at most 5 visible tasks (window has scrolled)
    visible_tasks = search_modal.get_task_items()
    expect(visible_tasks).to_have_count(5)

    search_modal.press_escape()
