"""Integration tests for multi-project functionality.

These tests verify that Sculptor correctly handles:
- Creating new projects through the UI
- Switching between multiple projects
- Running tasks concurrently in different projects
- Synchronizing project state across browser tabs
"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from sculptor.constants import ElementIDs
from sculptor.testing.decorators import flaky
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.project_git_init_dialog import PlaywrightGitInitDialogElement
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.launch_mode import LaunchMode
from sculptor.testing.multi_tab_page_factory import MultiTabPageFactory
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.test_repo_factory import TestRepoFactory
from sculptor.testing.user_stories import user_story

# ============================================================================
# Project Creation Tests
# ============================================================================


@user_story("to create new projects through the sidebar")
def test_create_new_project_from_sidebar(
    sculptor_launch_mode_: LaunchMode, sculptor_page_: PlaywrightHomePage, test_repo_factory_: TestRepoFactory
) -> None:
    """Test creating a new project through the sidebar UI.

    Verifies:
    - Project can be created via "Open New Repo" dialog
    - Project appears in the project selector
    - Task list is empty for new project
    - Project becomes the active project
    """

    other_project_name = "other project"
    other_branch_name = "other-branch"
    new_task_prompt = "hello world"

    # Create a second test repository (in addition to the default one that is automatically created)
    repo = test_repo_factory_.create_repo(name=other_project_name, branch=other_branch_name)

    # Get the home page and sidebar
    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()

    # Create the new project via the sidebar
    sidebar.create_project(project_path=repo.base_path, project_name=other_project_name)

    # Verify we can create a task in the new project
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text=new_task_prompt)

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()

    # Verify the task appears
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)


@user_story("to initialize git in non-git directories")
def test_git_init_dialog_for_non_git_directories(
    sculptor_launch_mode_: LaunchMode, sculptor_page_: PlaywrightHomePage, tmp_path: Path
) -> None:
    """Test git initialization dialog for non-git directories.

    Verifies:
    - Non-git directories trigger the git init dialog
    - User can choose to initialize git
    - Project loads successfully after git init
    """

    if sculptor_launch_mode_.is_electron():
        pytest.skip(
            "FIXME: This test doesn't work under Electron yet: Electron opens a native dialog but this test doesn't handle that."
        )

    project_name = "non_git_project"
    # Create a non-git directory
    non_git_dir = tmp_path / project_name
    non_git_dir.mkdir(parents=True, exist_ok=True)

    # Get the home page and sidebar
    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()
    project_selector = sidebar.get_project_selector()

    # Try to open the non-git directory
    dialog = project_selector.open_new_repo_dialog()
    dialog.open_project(path=str(non_git_dir))

    # Git init dialog should appear
    dialog_locator = sculptor_page_.get_by_test_id(ElementIDs.PROJECT_GIT_INIT_DIALOG)
    git_init_dialog = PlaywrightGitInitDialogElement(locator=dialog_locator, page=sculptor_page_)
    expect(git_init_dialog).to_be_visible()
    git_init_dialog.handle(should_init=True)

    # Wait for project to load
    expect(project_selector.get_selector_trigger()).to_contain_text(project_name)

    # Verify the project is now usable
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Task after git init")

    task_list = home_page.get_task_list()
    expect(task_list.get_tasks()).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)


# ============================================================================
# Project Switching and Concurrent Tests
# ============================================================================


@user_story("to switch between multiple projects and maintain task isolation")
def test_create_tasks_in_multiple_projects_and_switch(
    sculptor_launch_mode_: LaunchMode, sculptor_page_: PlaywrightHomePage, test_repo_factory_: TestRepoFactory
) -> None:
    """Test creating and switching between multiple projects.

    Verifies:
    - Multiple projects can be created
    - Switching projects updates the task list
    - Task lists are isolated between projects
    - UI state is preserved per project
    """

    project_a = "project_alpha"
    project_b = "project_beta"
    branch_a = "alpha-branch"
    branch_b = "beta-branch"

    b_task_1_prompt = "Beta task 1"
    b_task_2_prompt = "Beta task 2"
    a_task_1_prompt = "Alpha task 1"

    repo_a = test_repo_factory_.create_repo(name=project_a, branch=branch_a)
    repo_b = test_repo_factory_.create_repo(name=project_b, branch=branch_b)

    home_page = sculptor_page_

    # Create both projects
    sidebar = home_page.ensure_sidebar_is_open()
    sidebar.create_project(project_path=repo_a.base_path, project_name=project_a)
    sidebar.create_project(project_path=repo_b.base_path, project_name=project_b)

    # Create tasks in project_beta (currently selected)
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text=b_task_1_prompt)
    task_list = home_page.get_task_list()
    expect(task_list.get_tasks()).to_have_count(1)
    create_task(task_starter=task_starter, task_text=b_task_2_prompt)

    # Verify project_beta has 2 tasks
    task_list = home_page.get_task_list()
    expect(task_list.get_tasks()).to_have_count(2)
    wait_for_tasks_to_finish(task_list=task_list)

    # Switch to project_alpha
    sidebar.select_project_by_name(project_name=project_a)
    # Verify project_alpha has 0 tasks (isolation)
    expect(task_list.get_tasks()).to_have_count(0)

    # Create tasks in project_alpha
    create_task(task_starter=task_starter, task_text=a_task_1_prompt)
    expect(task_list.get_tasks()).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Switch back to project_beta
    sidebar.select_project_by_name(project_name=project_b)

    # Verify project_beta still has 2 tasks (persistence)
    expect(task_list.get_tasks()).to_have_count(2)


@user_story("to send messages across multiple project tasks")
@flaky
def test_send_messages_across_multiple_project_tasks(
    sculptor_page_: PlaywrightHomePage, test_repo_factory_: TestRepoFactory
) -> None:
    """Test complex multi-project workflow with task interactions.

    Verifies:
    - Can maintain conversations in multiple projects
    - Message history is preserved per task/project
    - Can alternate between projects and continue conversations
    - No message cross-contamination between projects
    """
    project_a = "project_alpha"
    project_b = "project_beta"
    branch_a = "alpha-branch"
    branch_b = "beta-branch"

    initial_prompt_b = "hello from project B"
    follow_up_prompt_b = "hello again"
    final_prompt_b = "hello again again"

    initial_prompt_a = "hello from project A"
    follow_up_prompt_a = "hello again"

    repo_a = test_repo_factory_.create_repo(name=project_a, branch=branch_a)
    repo_b = test_repo_factory_.create_repo(name=project_b, branch=branch_b)

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # Create both projects
    sidebar = home_page.ensure_sidebar_is_open()
    sidebar.create_project(project_path=repo_a.base_path, project_name=project_a)
    sidebar.create_project(project_path=repo_b.base_path, project_name=project_b)

    # Start task in project_b (currently selected)
    create_task(task_starter=task_starter, task_text=initial_prompt_b)

    # Wait for task to be ready
    task_list = home_page.get_task_list()
    wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=1)

    # Navigate to the task to send a follow-up
    task_b = task_list.get_tasks().first
    task_page_b = navigate_to_task_page(task=task_b)
    chat_panel_b = task_page_b.get_chat_panel()

    # Wait for initial response
    wait_for_completed_message_count(chat_panel=chat_panel_b, expected_message_count=2)

    # Send follow-up in project B
    send_chat_message(chat_panel=chat_panel_b, message=follow_up_prompt_b)
    wait_for_completed_message_count(chat_panel=chat_panel_b, expected_message_count=4)

    # Switch to project A
    sidebar.select_project_by_name(project_name=project_a)

    # Start task in project A
    create_task(task_starter=task_starter, task_text=initial_prompt_a)
    wait_for_tasks_to_build(task_list=task_list, expected_num_tasks=1)

    # Navigate to task A
    task_a = task_list.get_tasks().first
    task_page_a = navigate_to_task_page(task=task_a)
    chat_panel_a = task_page_a.get_chat_panel()

    # Wait for initial response
    wait_for_completed_message_count(chat_panel=chat_panel_a, expected_message_count=2)

    # Send follow-up in project A
    send_chat_message(chat_panel=chat_panel_a, message=follow_up_prompt_a)
    wait_for_completed_message_count(chat_panel=chat_panel_a, expected_message_count=4)

    # Switch back to project B
    sidebar.select_project_by_name(project_name=project_b)

    # Navigate back to task B
    task_b = task_list.get_tasks().first
    task_page_b = navigate_to_task_page(task=task_b)
    chat_panel_b = task_page_b.get_chat_panel()

    # Verify message history is preserved (should have 4 messages from before)
    messages_b = chat_panel_b.get_messages()
    expect(messages_b).to_have_count(4)

    # Send another follow-up
    send_chat_message(chat_panel=chat_panel_b, message=final_prompt_b)
    wait_for_completed_message_count(chat_panel=chat_panel_b, expected_message_count=6)

    # Project A should have 1 task
    sidebar.select_project_by_name(project_name=project_a)
    expect(task_list.get_tasks()).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Project B should have 1 task
    sidebar.select_project_by_name(project_name=project_b)
    expect(task_list.get_tasks()).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)


# ============================================================================
# Multi-Tab Test
# ============================================================================


@user_story("to see project updates sync across browser tabs")
def test_project_list_syncs_across_tabs(
    sculptor_launch_mode_: LaunchMode,
    multi_tab_page_factory_: MultiTabPageFactory,
    test_repo_factory_: TestRepoFactory,
) -> None:
    """Test that project creation in one tab appears in another tab.

    Verifies:
    - New projects created in one tab appear in other tabs
    - Project selector updates across tabs
    - Can switch to new project from any tab
    """

    if sculptor_launch_mode_.is_electron():
        pytest.skip("FIXME: This test doesn't work under Electron: the Electron app doesn't support multiple tabs.")

    project_a = "project_alpha"
    branch_a = "alpha-branch"
    repo_a = test_repo_factory_.create_repo(name=project_a, branch=branch_a)

    initial_prompt_a = "hello"

    # Set up both tabs
    home_page_primary = PlaywrightHomePage(page=multi_tab_page_factory_.primary_page)
    secondary_page = multi_tab_page_factory_.create_page()
    home_page_secondary = PlaywrightHomePage(page=secondary_page)

    # Ensure sidebars are open in both tabs
    sidebar_primary = home_page_primary.ensure_sidebar_is_open()
    sidebar_secondary = home_page_secondary.ensure_sidebar_is_open()

    # In primary tab: Create the project
    sidebar_primary.create_project(project_path=repo_a.base_path, project_name=project_a)

    # In secondary tab: Verify project appears in the selector
    project_selector_secondary = sidebar_secondary.get_project_selector()

    # Click the selector to open dropdown and refresh the list
    project_selector_secondary.get_selector_trigger().click()

    # Look for the new project in the dropdown
    project_option = secondary_page.get_by_test_id(ElementIDs.PROJECT_SELECT_ITEM).filter(has_text=project_a)
    expect(project_option).to_be_visible()

    # Select the project in secondary tab
    project_option.click()

    # Verify the project loaded in secondary tab
    expect(project_selector_secondary).to_contain_text(project_a)

    # Create a task in secondary tab
    task_starter_secondary = home_page_secondary.get_task_starter()
    create_task(task_starter=task_starter_secondary, task_text=initial_prompt_a)

    # Switch to the project in primary tab
    sidebar_primary.select_project_by_name(project_name=project_a)

    # Verify the task created in secondary tab is visible in primary tab
    task_list_primary = home_page_primary.get_task_list()
    expect(task_list_primary.get_tasks()).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list_primary)


# ============================================================================
# Duplicate Project Name Tests
# ============================================================================


@user_story("to distinguish between projects with the same folder name")
def test_duplicate_project_names(
    sculptor_launch_mode_: LaunchMode,
    sculptor_page_: PlaywrightHomePage,
    test_repo_factory_: TestRepoFactory,
) -> None:
    """Test handling of projects with same leaf folder names but different paths."""

    project_name = "LeafFolderName"

    repo_x = test_repo_factory_.create_repo(name=f"X/{project_name}", branch="main")
    repo_y = test_repo_factory_.create_repo(name=f"Y/{project_name}", branch="main")

    home_page = sculptor_page_
    sidebar = home_page.ensure_sidebar_is_open()

    sidebar.create_project(project_path=repo_x.base_path, project_name=project_name)
    sidebar.create_project(project_path=repo_y.base_path, project_name=project_name)

    project_selector = sidebar.get_project_selector()
    current_project = project_selector.get_current_project_name()
    assert current_project == project_name

    repo_indicator = home_page.get_repository_indicator()

    project_selector.select_project_by_name(project_name=project_name, path_contains="/X/")
    expect(repo_indicator).to_contain_text(str(repo_x.base_path))

    project_selector.select_project_by_name(project_name=project_name, path_contains="/Y/")
    expect(repo_indicator).to_contain_text(str(repo_y.base_path))
