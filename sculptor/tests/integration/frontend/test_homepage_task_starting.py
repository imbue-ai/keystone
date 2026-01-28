"""Integration tests for Homepage - Task Starting functionality."""

from playwright.sync_api import expect
from syrupy import SnapshotAssertion

from imbue_core.async_monkey_patches_test import expect_exact_logged_errors
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.itertools import only
from sculptor.constants import ElementIDs
from sculptor.testing.computing_environment import get_branch_commit
from sculptor.testing.container_utils import with_mock_claude_output
from sculptor.testing.decorators import flaky
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import get_task_branch_name
from sculptor.testing.elements.task import get_task_status_locator
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_build
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.elements.task_starter import select_branch
from sculptor.testing.image_utils import get_project_id_for_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.user_stories import user_story
from sculptor.web.derived import TaskStatus


@user_story("the contents of the initial prompt to survive page reloads and navigation")
def test_prompt_draft_persists_from_home_page(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that the prompt draft persists when reloading the home page."""
    task_text = "Hello, this is a test message!"

    # Type a task in the task starter input
    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()
    task_starter.get_task_input().type(task_text)

    # Verify that we can reload and the prompt draft persists
    home_page.reload()
    task_starter = home_page.get_task_starter()
    expect(task_starter.get_task_input()).to_have_text(task_text)


@user_story("to see my current and past tasks at a glance")
def test_task_shows_building_running_ready_status(sculptor_page_: PlaywrightHomePage) -> None:
    """Test that a task shows up on the task list with 'Building' status immediately, then transitions to 'Running', then 'Ready'.
    Expectation: the task transition from Building to Running to Ready
    """

    home_page = sculptor_page_

    # Create a task
    task_starter = home_page.get_task_starter()
    task_starter.get_task_input().type("Say hello to me!")
    task_starter.get_start_button().click()

    # Verify task appears immediately with Building status
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    task = only(tasks.all())
    get_task_status_locator(task=task)

    # Check that it starts with Building status
    expect(task).to_have_attribute("data-status", TaskStatus.BUILDING)

    wait_for_tasks_to_build(task_list=task_list)

    # Wait for it to become ready
    wait_for_tasks_to_finish(task_list=task_list)


@user_story("to see my current and past tasks at a glance")
def test_task_shows_error_status(
    sculptor_page_: PlaywrightHomePage,
    sculptor_factory_: SculptorFactory,
    snapshot: SnapshotAssertion,
    test_root_concurrency_group: ConcurrencyGroup,
) -> None:
    """Test that a task shows up on the task list with 'Error' status."""

    # make a note that we expect the task to fail:
    sculptor_page_._imbue_server.is_unexpected_error_caused_by_test = True

    home_page = sculptor_page_

    # Create and start initial task
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Hello this is test message 1 of 2. Please respond briefly!")

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and verify initial state
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")

    # have to check that there were no earlier errors
    # otherwise this test ends up being flaky instead of failing when the snapshots are outdated!
    # this happens because we will see an error for each missing snapshot.
    # (and those errors are present before the other one that we expect)
    error_blocks = chat_panel.get_by_test_id(ElementIDs.ERROR_BLOCK)
    expect(error_blocks).to_have_count(0)

    if not snapshot.session.update_snapshots:
        with expect_exact_logged_errors(["Error handling user message: object_type='ChatInputUserMessage'"]):
            # hijack claude to return an error message
            task_id = chat_panel.get_attribute("data-taskid")
            assert task_id is not None
            project_id = get_project_id_for_task(sculptor_factory_.database_url, task_id, test_root_concurrency_group)
            with with_mock_claude_output(
                task_id, project_id, "This is invalid JSON and should cause an error", test_root_concurrency_group
            ):
                new_message_text = "Hello this is test message 2 of 2. Please respond briefly!"
                send_chat_message(chat_panel=chat_panel, message=new_message_text)

                error_blocks = chat_panel.get_by_test_id(ElementIDs.ERROR_BLOCK)
                # We don't really care about the exact number of error blocks here.
                # Ideally, a single error should manifest as a single error block.
                # Sometimes it can appear multiple times because of cascading and that's fine, too.
                # As long as it's not a ridiculously high number of times.
                expect(error_blocks).not_to_have_count(0)
                assert error_blocks.count() <= 3
                # for i in range(error_blocks.count()):
                #     element = error_blocks.nth(i)
                #     html = element.evaluate("el => el.outerHTML")
                #     logger.info("Element {}: {}", i, html)

                expect(chat_panel.get_error_block().first).to_be_visible()
                # task_page.screenshot(path="/tmp/debug_page.png")
                assert chat_panel.get_messages().count() >= 4
                expect(chat_panel).to_have_attribute("data-is-streaming", "false")

            sculptor_page_.go_back()
            home_page = sculptor_page_
            task_list = home_page.get_task_list()
            tasks = task_list.get_tasks()
            expect(tasks).to_have_count(1)
            task = only(tasks.all())
            expect(task).to_have_attribute("data-status", TaskStatus.ERROR)


@flaky
@user_story("to start agent tasks operating on my codebase")
def test_multiple_tasks_from_branch(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    """Test creating multiple tasks from the same branch.
    Related to: the contents of the text box are the initial prompt
    """
    initial_branch = pure_local_repo_.get_current_branch_name()
    assert initial_branch == "testing"

    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()

    # Create first task
    create_task(task_starter=task_starter, task_text="Say hello to me!")
    expect(tasks).to_have_count(1)

    # Create second task from same branch
    create_task(task_starter=task_starter, task_text="Say goodbye to me!")
    expect(tasks).to_have_count(2)

    # Verify both tasks reach ready state
    wait_for_tasks_to_finish(task_list=task_list)


@user_story("to start agent tasks operating on my codebase")
def test_tasks_from_multiple_branches(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    """Create tasks from multiple different source branches."""

    # Set up test branches
    initial_branch_name = pure_local_repo_.get_current_branch_name()
    assert initial_branch_name == "testing"
    second_branch_name = "second_branch"
    pure_local_repo_.create_reset_and_checkout_branch(second_branch_name)
    pure_local_repo_.write_file("src/second_branch.py", "print('Hello from second branch!')")
    pure_local_repo_.stage_all_changes()
    pure_local_repo_.commit("Add second branch file", commit_time="2025-01-01T00:00:02")

    home_page = sculptor_page_

    task_starter = home_page.get_task_starter()
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()

    # Create task from first branch (default)
    select_branch(task_starter=task_starter, branch_name=initial_branch_name)
    create_task(task_starter=task_starter, task_text="Say hello to me!")
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Verify first task is created from "testing" branch
    first_task = tasks.nth(0)
    first_task_branch_name = get_task_branch_name(first_task)

    # Check if this branch exists in the repo
    all_branches = pure_local_repo_.get_branches()
    assert first_task_branch_name in all_branches, (
        f"Branch {first_task_branch_name} not found in repo. Available branches: {all_branches}"
    )

    # Verify the first task branch points to the same commit as the testing branch
    first_task_commit = get_branch_commit(pure_local_repo_.repo, first_task_branch_name)
    testing_commit = get_branch_commit(pure_local_repo_.repo, "testing")
    assert first_task_commit == testing_commit, (
        f"First task branch {first_task_branch_name} points to {first_task_commit}, but testing branch points to {testing_commit}"
    )

    # Create task from second branch
    select_branch(task_starter=task_starter, branch_name=second_branch_name)
    create_task(task_starter=task_starter, task_text="Say goodbye to me!")
    expect(tasks).to_have_count(2)
    wait_for_tasks_to_finish(task_list=task_list)

    # Verify second task is created from "second_branch" branch
    second_task = tasks.nth(0)
    second_task_branch_name = get_task_branch_name(second_task)

    # Check if this branch exists in the repo
    all_branches = pure_local_repo_.get_branches()
    assert second_task_branch_name in all_branches, (
        f"Branch {second_task_branch_name} not found in repo. Available branches: {all_branches}"
    )

    # Verify the second task branch points to the same commit as the second_branch
    second_task_commit = get_branch_commit(pure_local_repo_.repo, second_task_branch_name)
    second_branch_commit = get_branch_commit(pure_local_repo_.repo, second_branch_name)
    assert second_task_commit == second_branch_commit, (
        f"Second task branch {second_task_branch_name} points to {second_task_commit}, but second_branch points to {second_branch_commit}"
    )

    # Verify both tasks work regardless of source branch
    wait_for_tasks_to_finish(task_list=task_list)


@user_story("to start agent tasks with the current state of my codebase")
def test_tasks_with_uncommitted_changes(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    # Set up test branches
    initial_branch_name = pure_local_repo_.get_current_branch_name()
    assert initial_branch_name == "testing"
    # staged change
    pure_local_repo_.write_file("src/second_branch.py", "print('Hello from second branch!')")
    pure_local_repo_.stage_all_changes()
    # unstaged, but tracked file
    pure_local_repo_.write_file("src/app.py", "# fancy comment\n\nimport flask\n\nflask.run()")
    # untracked file
    pure_local_repo_.write_file("src/special_file.py", "print('weirdly tracked')")

    home_page = sculptor_page_

    # Create a task that will generate a file
    task_starter = home_page.get_task_starter()
    create_task(task_starter=task_starter, task_text="Just reply with 'hello'")

    # Verify task was created
    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)

    wait_for_tasks_to_finish(task_list=task_list)

    # Get the task from the task list
    task = only(tasks.all())

    # Navigate to task and wait for assistant to complete the file creation
    task_page = navigate_to_task_page(task=task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify artifact panel shows the created file
    artifacts_panel = task_page.get_artifacts_panel()
    expect(artifacts_panel).to_be_visible()

    # FIXME: This doesn't work when the window is narrow and the tab is turned into a dropdown.
    artifacts_panel.get_combined_diff_tab().click()
    diff_artifact = artifacts_panel.get_combined_diff_section()

    # Check that the assistant created exactly one file
    uncommitted_section = diff_artifact.get_uncommitted_section()
    file_artifacts = uncommitted_section.get_file_artifacts()
    expect(file_artifacts).to_have_count(3)

    # Verify the expected content
    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(0)
    expect(file_artifact_element.get_file_name()).to_contain_text("src/app.py")
    file_artifact_element.toggle_body()
    expect(file_artifact_element.get_file_body()).to_contain_text("fancy comment")

    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(1)
    expect(file_artifact_element.get_file_name()).to_contain_text("src/second_branch.py")
    file_artifact_element.toggle_body()
    expect(file_artifact_element.get_file_body()).to_contain_text("Hello from second branch")

    file_artifact_element = uncommitted_section.get_nth_file_artifact_element(2)
    expect(file_artifact_element.get_file_name()).to_contain_text("src/special_file.py")
    file_artifact_element.toggle_body()
    expect(file_artifact_element.get_file_body()).to_contain_text("weirdly tracked")
