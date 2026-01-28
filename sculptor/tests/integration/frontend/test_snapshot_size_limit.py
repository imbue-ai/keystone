from sculptor.testing.elements.chat_panel import send_chat_message
from sculptor.testing.elements.chat_panel import wait_for_completed_message_count
from sculptor.testing.elements.snapshots import get_container_id_for_task
from sculptor.testing.elements.snapshots import verify_container_restart
from sculptor.testing.elements.snapshots import verify_no_container_restart
from sculptor.testing.elements.snapshots import verify_snapshot_count
from sculptor.testing.elements.snapshots import wait_for_possible_snapshot
from sculptor.testing.elements.task_starter import create_and_navigate_to_task
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.user_stories import user_story


def _verify_single_restart_after_large_file(sculptor_page: PlaywrightHomePage, message: str) -> None:
    # Create the task. The initial message will trigger the first snapshot.
    task_page = create_and_navigate_to_task(
        sculptor_page.get_task_starter(), sculptor_page.get_task_list(), task_text="Hello!"
    )
    chat_panel = task_page.get_chat_panel()
    task_id = task_page.get_task_id()
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=2)
    initial_container_id = get_container_id_for_task(task_id)
    wait_for_possible_snapshot(task_id, initial_count=0)
    verify_snapshot_count(task_id, expected_count=1, step_description="After initial task creation")

    # Send command to create large file. This should trigger both a snapshot and a restart.
    send_chat_message(chat_panel=chat_panel, message=message)
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=4)
    wait_for_possible_snapshot(task_id, initial_count=1, timeout_seconds=30)
    verify_snapshot_count(task_id, expected_count=2, step_description="After creating large file")
    new_container_id = verify_container_restart(task_id, initial_container_id, timeout_seconds=120)

    # Send a small message and verify there is a new smaller snapshot but no restart
    send_chat_message(chat_panel=chat_panel, message="Hello")
    wait_for_completed_message_count(chat_panel=chat_panel, expected_message_count=6)
    wait_for_possible_snapshot(task_id, initial_count=2)
    verify_snapshot_count(task_id, expected_count=3, step_description="After message post-restart")
    verify_no_container_restart(task_id, new_container_id)


@user_story("to have tasks restart when snapshots exceed size limits")
def test_snapshot_size_limit_with_large_file_and_chat_interactions(
    sculptor_page_: PlaywrightHomePage,
) -> None:
    """
    Integration test that verifies a task restarts when snapshot size limit is exceeded:
    1. Creates a new task through the UI (triggers first snapshot)
    2. Creates a file that exceeds max_snapshot_size_bytes
       - This should trigger a snapshot and container restart
    3. Sends another message to verify that additional snapshots are smaller and don't trigger restart
    """
    _verify_single_restart_after_large_file(sculptor_page_, f"Run this command: fallocate -l 100000000 big_file.img")


@user_story("to not restart containers due to temporary git objects")
def test_temp_git_objects_do_not_cause_restart(
    sculptor_page_: PlaywrightHomePage,
) -> None:
    """
    Verifies that temporary git objects do not get included in snapshots.

    This is the same as the test above, except that it makes sure it writes random data
    so that the git object it produces does not compress down.
    This is a regression test. Previously, untracked files were being `git add`ed and
    polluting every subsequent snapshot with temporary git objects.
    """
    # Tell the agent to create a 100MB file with random data. Random data is important,
    # because git compresses the contents when it creates its object files.
    _verify_single_restart_after_large_file(
        sculptor_page_, "Write 100MB of random data to big_file.bin. Do not .gitignore this file."
    )
