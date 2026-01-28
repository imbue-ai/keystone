"""Integration tests for the forking functionality."""

import pytest
from playwright.sync_api import expect

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.itertools import only
from sculptor.config.settings import SculptorSettings
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.image_tags import get_current_sculptor_images_info
from sculptor.services.environment_service.environments.image_tags import get_v1_image_ids_and_metadata_for_task
from sculptor.services.environment_service.environments.image_tags import (
    parse_image_info_associated_with_this_sculptor_instance,
)
from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.task import delete_task
from sculptor.testing.elements.task import navigate_to_task_page
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.elements.task_starter import create_task
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.user_stories import user_story
from tests.integration.frontend.test_image_cleanup import trigger_image_cleanup

# TODO: more tests to write:
# - Ask for changes, then local sync, make some manual changes, then fork. Verify fork has all changes.
# - Ask for changes, verify that we have a check for that state, then fork. Then verify we can still see that check. Then ask for more changes and make sure we can see a new check
# - Fork to a task, then delete the original task. Make sure the forked task is still usable and that the backlink is gone


# FIXME: This doesn't work when the window is narrow because of verify_uncommitted_file.
@pytest.mark.flaky
@user_story("to fork a task and have it inherit conversation history and file state")
def test_fork_inherits_conversation_and_state(
    sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState
) -> None:
    """Test that forked task inherits conversation up to fork point and file changes."""
    # Test constants
    TEST_FILE_NAME = "test_file.py"
    HELLO_WORLD_CONTENT = 'print("hello world")'
    GOODBYE_WORLD_CONTENT = 'print("goodbye world")'
    CREATE_FILE_PROMPT = f"Create a file called {TEST_FILE_NAME} with content '{HELLO_WORLD_CONTENT}'. Do NOT commit."
    MODIFY_FILE_PROMPT = f"Modify {TEST_FILE_NAME} to say {GOODBYE_WORLD_CONTENT}. Do NOT commit."
    FORK_PROMPT = "Say hello to me"
    CHILD_MESSAGE_PROMPT = "Say goodbye to me."

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # Create task that will make file changes
    create_task(
        task_starter=task_starter,
        task_text=CREATE_FILE_PROMPT,
    )

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to task and verify file was created
    parent_task = only(tasks.all())
    task_page = navigate_to_task_page(task=parent_task)
    chat_panel = task_page.get_chat_panel()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify the file exists in parent's uncommitted changes
    task_page.verify_uncommitted_file(file_name=TEST_FILE_NAME, expected_content=HELLO_WORLD_CONTENT)

    # Fork at this point (from the last message).
    # IMPORTANT: we must wait for the snapshot to be created before forking, otherwise the fork request will fail
    expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")
    chat_panel.fork_task(prompt=FORK_PROMPT, message_index=None)

    # Verify new task was created
    task_list = task_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)
    wait_for_tasks_to_finish(task_list=task_list)

    # Send a 4th message to parent that modifies the file (should NOT appear in child)
    send_chat_message(chat_panel=chat_panel, message=MODIFY_FILE_PROMPT)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=5)

    # Verify parent's file was modified
    task_page.verify_uncommitted_file(file_name=TEST_FILE_NAME, expected_content=GOODBYE_WORLD_CONTENT)

    # Navigate to child task via the ForkedToBlock (first/only forked task)
    chat_panel.navigate_to_forked_task(block_index=0)

    # Verify child has the forked from block
    expect(chat_panel.get_forked_from_block(block_index=0)).to_be_visible()

    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=5)

    messages = chat_panel.get_messages()
    expect(messages).to_have_count(5)
    expect(messages.nth(0)).to_contain_text(TEST_FILE_NAME)
    expect(messages.nth(3)).to_contain_text(FORK_PROMPT)

    # Verify child has the file with ORIGINAL content (not parent's modification)
    task_page.verify_uncommitted_file(
        file_name=TEST_FILE_NAME, expected_content=HELLO_WORLD_CONTENT, not_expected_content=GOODBYE_WORLD_CONTENT
    )

    # Send a new message in child to verify independence
    send_chat_message(chat_panel=chat_panel, message=CHILD_MESSAGE_PROMPT)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=7)

    # Navigate back to parent via the ForkedFromBlock (first/only parent)
    chat_panel.navigate_to_parent_task(block_index=0)

    expect(chat_panel).not_to_contain_text(CHILD_MESSAGE_PROMPT)

    # Verify parent still has the modified file
    task_page.verify_uncommitted_file(file_name=TEST_FILE_NAME, expected_content=GOODBYE_WORLD_CONTENT)


def _get_task_id_from_url(url: str) -> TaskID:
    return TaskID(url.rsplit("/", 1)[-1])


# FIXME: This doesn't work when the window is narrow because of verify_uncommitted_file.
@user_story("to fork a task from another forked task")
def test_fork_from_forked_task(
    sculptor_page_: PlaywrightHomePage,
    pure_local_repo_: MockRepoState,
    container_prefix_: str,
    test_root_concurrency_group: ConcurrencyGroup,
) -> None:
    """Test that forking from a forked task establishes correct grandparent-parent-child relationships."""
    # Test constants
    BASE_FILE_NAME = "base.txt"
    BASE_FILE_CONTENT = "grandparent content"
    GRANDPARENT_PROMPT = f"Create a file called {BASE_FILE_NAME} with '{BASE_FILE_CONTENT}'. Do NOT commit."
    PARENT_FORK_PROMPT = "Say hello from parent"
    CHILD_FORK_PROMPT = "Say hello from child"

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # Create grandparent task
    create_task(task_starter=task_starter, task_text=GRANDPARENT_PROMPT)

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to grandparent task and fork it to create parent
    grandparent_task = tasks.first
    task_page = navigate_to_task_page(task=grandparent_task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Fork to create parent task
    expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")
    grandparent_task_id = _get_task_id_from_url(task_page.url)
    chat_panel.fork_task(prompt=PARENT_FORK_PROMPT, message_index=None)

    # Verify parent task was created
    task_list = task_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to parent task
    chat_panel.navigate_to_forked_task(block_index=0)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=5)

    # Verify parent has forked from block pointing to grandparent
    expect(chat_panel.get_forked_from_block(block_index=0)).to_be_visible()

    # Verify parent has the base file from grandparent
    task_page.verify_uncommitted_file(file_name=BASE_FILE_NAME, expected_content=BASE_FILE_CONTENT)

    # Fork from parent to create child
    expect(chat_panel).to_have_attribute("data-number-of-snapshots", "2")
    parent_task_id = _get_task_id_from_url(task_page.url)
    chat_panel.fork_task(prompt=CHILD_FORK_PROMPT, message_index=None)

    # Verify child task was created
    task_list = task_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(3)
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to child task
    chat_panel.navigate_to_forked_task(block_index=0)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=8)

    child_task_id = _get_task_id_from_url(task_page.url)

    # Verify child has forked from block pointing to parent
    expect(chat_panel.get_forked_from_block(block_index=1)).to_be_visible()

    # Verify child has all messages from grandparent, parent fork, and its own fork
    messages = chat_panel.get_messages()
    expect(messages).to_have_count(8)
    expect(messages.nth(0)).to_contain_text(BASE_FILE_NAME)
    expect(messages.nth(3)).to_contain_text(PARENT_FORK_PROMPT)
    expect(messages.nth(6)).to_contain_text(CHILD_FORK_PROMPT)

    # Verify child has the base file from grandparent (inherited through parent)
    task_page.verify_uncommitted_file(file_name=BASE_FILE_NAME, expected_content=BASE_FILE_CONTENT)

    # Wait for the child to be done snapshotting
    chat_panel.get_fork_button()

    # Navigate back to parent and verify it has forked to block for child
    chat_panel.navigate_to_parent_task(block_index=1)
    expect(chat_panel).to_contain_text(PARENT_FORK_PROMPT)
    expect(chat_panel).not_to_contain_text(CHILD_FORK_PROMPT)

    # Verify parent still has the base file
    task_page.verify_uncommitted_file(file_name=BASE_FILE_NAME, expected_content=BASE_FILE_CONTENT)

    # Navigate back to grandparent and verify it has forked to block for parent
    chat_panel.navigate_to_parent_task(block_index=0)
    expect(chat_panel).not_to_contain_text(PARENT_FORK_PROMPT)
    expect(chat_panel).not_to_contain_text(CHILD_FORK_PROMPT)

    # Verify grandparent still has the base file
    task_page.verify_uncommitted_file(file_name=BASE_FILE_NAME, expected_content=BASE_FILE_CONTENT)

    # We observed that there is an extra snapshot for the base task. This gets retagged
    # for all child tasks, which is why the numbers in range(...) are one higher than you might expect.
    # As far as we know, this is not load-bearing for image tagging functionality.
    expected_image_metadata: set[ImageMetadataV1] = {
        *(ImageMetadataV1.from_task(grandparent_task_id, i) for i in range(2)),
        *(ImageMetadataV1.from_task(parent_task_id, i) for i in range(3)),
        *(ImageMetadataV1.from_task(child_task_id, i) for i in range(4)),
    }

    assert {
        parse_image_info_associated_with_this_sculptor_instance(image_info)
        for image_info in get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
    } == expected_image_metadata


# FIXME: This doesn't work when the window is narrow because of verify_committed_file.
@user_story("to merge changes from a forked task back to the original task")
def test_fork_then_merge_from_forked_task(sculptor_page_: PlaywrightHomePage, pure_local_repo_: MockRepoState) -> None:
    """Test forking a task, making changes in the forked task, then merging back to original."""
    # Test constants
    ORIGINAL_FILE_NAME = "hello.py"
    ORIGINAL_FILE_CONTENT = "print('hello world')"
    ORIGINAL_PROMPT = f"Create a file called {ORIGINAL_FILE_NAME} with '{ORIGINAL_FILE_CONTENT}'. and commit it with message 'add hello file'."
    FORK_PROMPT = "Say hello"
    FORKED_FILE_NAME = "goodbye.py"
    FORKED_FILE_CONTENT = "print('goodbye world')"
    FORKED_TASK_CHANGE = f"Create a file called {FORKED_FILE_NAME} with '{FORKED_FILE_CONTENT}' and commit it with message 'add goodbye file'."

    home_page = sculptor_page_
    task_starter = home_page.get_task_starter()

    # Create original task
    create_task(task_starter=task_starter, task_text=ORIGINAL_PROMPT)

    task_list = home_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(1)
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to original task and fork it
    original_task = tasks.first
    task_page = navigate_to_task_page(task=original_task)
    chat_panel = task_page.get_chat_panel()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

    # Verify original task has the original file
    task_page.verify_committed_file(file_name=ORIGINAL_FILE_NAME, expected_content=ORIGINAL_FILE_CONTENT)

    # Get the original task's branch name
    original_branch = task_page.get_branch_name()

    # Fork the task
    expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")
    chat_panel.fork_task(prompt=FORK_PROMPT, message_index=None)

    # Verify forked task was created
    task_list = task_page.get_task_list()
    tasks = task_list.get_tasks()
    expect(tasks).to_have_count(2)
    wait_for_tasks_to_finish(task_list=task_list)

    # Navigate to forked task
    chat_panel.navigate_to_forked_task(block_index=0)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=5)

    # Verify forked task has the original file (inherited)
    task_page.verify_committed_file(file_name=ORIGINAL_FILE_NAME, expected_content=ORIGINAL_FILE_CONTENT)

    # Make changes in the forked task
    send_chat_message(chat_panel=chat_panel, message=FORKED_TASK_CHANGE)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=7)

    # Get the forked task's branch name
    forked_branch = task_page.get_branch_name()

    # Checkout the original task's branch locally to make it appear in the merge panel dropdown
    pure_local_repo_.checkout_branch(original_branch)

    # Open merge panel and pull changes to original task
    merge_panel = task_page.get_task_header().open_and_get_merge_panel_content()
    merge_panel.select_target_branch(branch_name=original_branch)

    # Click pull button
    merge_panel.pull_or_fetch(expect_text="Pull")

    # Wait for merge to complete
    notices = merge_panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=10000)
    expect(notices).to_contain_text("Finished successfully")

    # Navigate back to original task
    chat_panel.navigate_to_parent_task(block_index=0)

    # Open merge panel and pull changes to original task
    merge_panel = task_page.get_task_header().open_and_get_merge_panel_content()
    merge_panel.select_target_branch(branch_badge="agent's mirror")

    # Click push button
    push_button = merge_panel.get_push_button()
    expect(push_button).to_be_visible()
    expect(push_button).to_be_enabled()
    push_button.click()

    # Wait for merge to complete
    notices = merge_panel.get_footer_with_notices()
    expect(notices).to_be_visible(timeout=10000)
    expect(notices).to_contain_text("Finished successfully")

    # click out to dismiss the modal
    task_page.get_chat_panel().click()

    # Verify original task now has both files in Changes tab
    task_page.verify_committed_file(file_name=ORIGINAL_FILE_NAME, expected_content=ORIGINAL_FILE_CONTENT, file_index=1)
    # The forked file should be committed (merged from forked task)
    task_page.verify_committed_file(file_name=FORKED_FILE_NAME, expected_content=FORKED_FILE_CONTENT, file_index=0)


# FIXME: This doesn't work when the window is narrow because of verify_uncommitted_file.
@user_story("to fork from an earlier message and get the state at that point")
def test_fork_from_earlier_message(
    sculptor_factory_: SculptorFactory,
    pure_local_repo_: MockRepoState,
    test_root_concurrency_group: ConcurrencyGroup,
    container_prefix_: str,
    test_settings: SculptorSettings,
) -> None:
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        """Test that forking from a non-last message captures state at that fork point, not the latest state."""
        # Test constants
        FILE_A_NAME = "hello.py"
        FILE_A_CONTENT = "print('hello world')"
        CREATE_FILE_A_PROMPT = f"Create a file called {FILE_A_NAME} with '{FILE_A_CONTENT}'. Do NOT commit."
        FILE_B_NAME = "goodbye.py"
        FILE_B_CONTENT = "print('goodbye world')"
        CREATE_FILE_B_PROMPT = f"Create a file called {FILE_B_NAME} with '{FILE_B_CONTENT}'. Do NOT commit."
        FORK_PROMPT = "Say hello from fork"

        home_page = sculptor_page
        task_starter = home_page.get_task_starter()

        # Create original task with file A
        create_task(task_starter=task_starter, task_text=CREATE_FILE_A_PROMPT)

        task_list = home_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(1)
        wait_for_tasks_to_finish(task_list=task_list)

        # Navigate to original task
        original_task = tasks.first
        task_page = navigate_to_task_page(task=original_task)
        chat_panel = task_page.get_chat_panel()
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)

        # Verify original task has file A
        task_page.verify_uncommitted_file(file_name=FILE_A_NAME, expected_content=FILE_A_CONTENT)

        # Send a second message to create file B
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "1")
        send_chat_message(chat_panel=chat_panel, message=CREATE_FILE_B_PROMPT)
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)

        original_task_id = TaskID(task_page.get_task_id())

        # Fork from the first assistant message (index 1, after file A was created, before file B)
        # Message indices: 0=user (create A), 1=assistant (created A), 2=user (create B), 3=assistant (created B)
        expect(chat_panel).to_have_attribute("data-number-of-snapshots", "2")
        chat_panel.fork_task(prompt=FORK_PROMPT, message_index=1)

        # Verify forked task was created
        task_list = task_page.get_task_list()
        tasks = task_list.get_tasks()
        expect(tasks).to_have_count(2)
        wait_for_tasks_to_finish(task_list=task_list)

        # Navigate to forked task
        chat_panel.navigate_to_forked_task(block_index=0)
        wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=5)

        # Verify forked task has the forked from block
        expect(chat_panel.get_forked_from_block(block_index=0)).to_be_visible()

        # Verify forked task has file A (from fork point)
        task_page.verify_uncommitted_file(file_name=FILE_A_NAME, expected_content=FILE_A_CONTENT)

        # Verify forked task does NOT have file B (created after fork point)
        # We can check this by ensuring the artifacts panel only shows one file
        task_page.verify_uncommitted_file_count(expected_count=1)  # Only file A, not file B

        # Verify that deleting the original task only gets rid of the second snapshot of the first task,
        # leaving the first snapshot alone so that the second task can still fork from it.
        task_image_ids_and_metadata = get_v1_image_ids_and_metadata_for_task(
            original_task_id,
            test_root_concurrency_group,
            container_prefix_,
        )
        assert len(task_image_ids_and_metadata) == 3
        _, (first_snapshot, _), (second_snapshot, _) = sorted(
            task_image_ids_and_metadata, key=lambda x: x[1].sequence_number
        )
        delete_task(task=tasks.last)
        expect(tasks).to_have_count(1)
        trigger_image_cleanup(sculptor_page, sculptor_server)

        current_image_ids = [
            info.id for info in get_current_sculptor_images_info(test_root_concurrency_group, container_prefix_)
        ]
        assert first_snapshot in current_image_ids
        assert second_snapshot not in current_image_ids
