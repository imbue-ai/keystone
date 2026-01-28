"""Integration tests for Task Page - Plan Tab functionality."""

from playwright.sync_api import expect

from imbue_core.itertools import only
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.plan_item import get_plan_checkmark
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


@user_story("to see the plan that agent defined for itself")
def test_plans_show_up_in_artifact_panel(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that plans are correctly extracted and displayed in the artifact panel."""

    home_page = sculptor_page_

    # Create a task that will generate plans
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Add the following steps to the plan. Do not start them: 1. Step 1; 2. Step 2",
    )

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task page
    task_page = navigate_to_task_page(task=only(tasks.all()))
    chat_panel = task_page.get_chat_panel()

    # Wait for initial response
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Access the artifacts panel and click plan tab
    artifacts_panel = task_page.get_artifacts_panel()
    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    plan_tab = artifacts_panel.get_plan_tab()
    plan_tab.click()

    # Get plan section and verify plans are displayed
    plan_section = artifacts_panel.get_plan_section()
    plan_items = plan_section.get_plan_items()

    # Verify we have at least one plan
    expect(plan_items).to_have_count(2)

    # Verify each plan item contains expected keywords
    expect(plan_items.nth(0)).to_contain_text("Step 1")
    expect(plan_items.nth(1)).to_contain_text("Step 2")


@user_story("to see the plan that agent defined for itself")
def test_plans_update_with_completion(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that plans update their completion status correctly."""

    home_page = sculptor_page_

    # Create a task with plans
    task_starter = home_page.get_task_starter()
    create_task(
        task_starter=task_starter,
        task_text="Add the following steps to the plan. Do not start them: 1. Step 1; 2. Step 2; 3. Step 3",
    )

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task and wait for initial response
    task_page = navigate_to_task_page(task=only(tasks.all()))
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Check initial plan state
    artifacts_panel = task_page.get_artifacts_panel()
    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    plan_tab = artifacts_panel.get_plan_tab()
    plan_tab.click()

    plan_section = artifacts_panel.get_plan_section()
    plan_items = plan_section.get_plan_items()
    expect(plan_items).to_have_count(3)

    # Verify plan is not completed initially - no checkmark should be visible
    for plan_item in plan_items.all():
        expect(get_plan_checkmark(plan_item=plan_item)).to_have_count(0)

    # Send a message to complete the plan
    send_chat_message(chat_panel=chat_panel, message="Mark the first two steps of the plan as completed")

    # Wait for response
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

    # Verify plan is now marked as completed - checkmark should be visible on the first two steps
    expect(plan_items).to_have_count(3)
    expect(get_plan_checkmark(plan_item=plan_items.nth(0))).to_have_count(1)
    expect(get_plan_checkmark(plan_item=plan_items.nth(1))).to_have_count(1)
    expect(get_plan_checkmark(plan_item=plan_items.nth(2))).to_have_count(0)
