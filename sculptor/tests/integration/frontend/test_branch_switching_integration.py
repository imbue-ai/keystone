"""Integration test for branch switching and task creation from dropdown menu."""

from playwright.sync_api import expect

from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.elements.task_starter import select_branch
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


@user_story("to create tasks from different branches using dropdown menu")
def test_branch_switching_with_untracked_file(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that sets up a test repo with two branches A and B as well as a single untracked file,
    checks out branch A, starts sculptor, and uses the dropdown menu to start a task on branch B.
    """

    # Set up test branches
    branch_a = "branch_a"
    branch_b = "branch_b"

    # Create and set up branch A
    pure_local_repo_.create_reset_and_checkout_branch(branch_a)
    pure_local_repo_.write_file("src/file_a.py", "print('Hello from branch A!')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add file A", commit_time="2025-01-01T00:00:01")

    # Create and set up branch B
    pure_local_repo_.create_reset_and_checkout_branch(branch_b)
    pure_local_repo_.write_file("src/file_b.py", "print('Hello from branch B!')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add file B", commit_time="2025-01-01T00:00:02")

    # Switch back to branch A (this is our current branch when Sculptor starts)
    pure_local_repo_.checkout_branch(branch_a)

    # Create an untracked file
    pure_local_repo_.write_file("untracked_file.txt", "This is an untracked file")

    # Verify initial state
    current_branch = pure_local_repo_.get_current_branch_name()
    assert current_branch == branch_a, f"Expected to be on {branch_a}, but on {current_branch}"

    # Verify both branches exist
    all_branches = pure_local_repo_.get_branches()
    assert branch_a in all_branches, f"Branch {branch_a} not found in repo. Available branches: {all_branches}"
    assert branch_b in all_branches, f"Branch {branch_b} not found in repo. Available branches: {all_branches}"

    # Start Sculptor and navigate to home page
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()

    # Verify no tasks initially
    expect(tasks).to_have_count(0)

    # Use dropdown menu to select branch B and create a task
    select_branch(task_starter=task_starter, branch_name=branch_b)
    create_task(task_starter=task_starter, task_text="Hello!")

    # Verify task was created
    expect(tasks).to_have_count(1)

    # Wait for task to finish building and become ready
    wait_for_tasks_to_finish(task_list=task_list)

    # Verify the task is ready and working
    task = tasks.nth(0)
    expect(task).to_be_visible()

    # The test passes if we successfully:
    # 1. Set up a repo with two branches A and B
    # 2. Added an untracked file
    # 3. Checked out branch A
    # 4. Used Sculptor's dropdown menu to create a task from branch B
    # 5. The task was created successfully and became ready
