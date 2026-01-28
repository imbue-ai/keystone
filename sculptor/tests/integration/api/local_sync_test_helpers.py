"""Shared test helpers for Local Sync integration tests."""

import time
from contextlib import contextmanager
from pathlib import Path
from queue import Empty
from queue import Queue
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Generator
from typing import Iterable
from typing import TypeVar
from typing import cast

import httpx
from fastapi.testclient import TestClient

from imbue_core.pydantic_serialization import model_dump
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import MessageTypes
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.services.git_repo_service.ref_namespace_stasher import read_global_stash_singleton_if_present
from sculptor.web.auth import UserSession
from sculptor.web.data_types import DeleteSyncStashRequest
from sculptor.web.data_types import EnableLocalSyncRequest
from sculptor.web.data_types import RestoreSyncStashRequest

T = TypeVar("T")


def setup_dirty_files(repo: WritableGitRepo) -> None:
    """Create uncommitted modified files in the user repo."""
    test_file = Path(repo.get_repo_path()) / "dirty_file.txt"
    test_file.write_text("dirty content")


def setup_staged_files(repo: WritableGitRepo) -> None:
    """Create staged files in the user repo."""
    staged_file = Path(repo.get_repo_path()) / "staged_file.txt"
    staged_file.write_text("staged content")
    repo._run_git(["add", "staged_file.txt"])


def setup_untracked_files(repo: WritableGitRepo) -> None:
    """Create untracked files in the user repo."""
    untracked_file = Path(repo.get_repo_path()) / "untracked_file.txt"
    untracked_file.write_text("untracked content")


def setup_intermediate_merge_state(project: Project, services: CompleteServiceCollection) -> None:
    """Setup git repo in an intermediate merge state (conflicting merge in progress)."""
    with services.git_repo_service.open_local_user_git_repo_for_write(project) as repo:
        # Create a branch with a conflicting change
        repo._run_git(["checkout", "-b", "conflict-branch"])
        conflict_file = Path(repo.get_repo_path()) / "conflict.txt"
        conflict_file.write_text("branch content")
        repo._run_git(["add", "conflict.txt"])
        repo._run_git(["commit", "-m", "Add conflict file on branch"])

        # Switch back to main and create conflicting change
        repo._run_git(["checkout", "main"])
        conflict_file.write_text("main content")
        repo._run_git(["add", "conflict.txt"])
        repo._run_git(["commit", "-m", "Add conflict file on main"])

        # Start merge to create intermediate state
        try:
            repo._run_git(["merge", "conflict-branch"])
        except Exception:
            # Expected to fail with conflict - this leaves us in intermediate state
            pass


def get_stash_singleton(services: CompleteServiceCollection, user_session: UserSession):
    """Helper to get the current stash singleton."""
    with services.data_model_service.open_transaction(user_session.request_id) as transaction:
        return read_global_stash_singleton_if_present(services.git_repo_service, transaction)


def verify_working_directory_clean(project: Project, services: CompleteServiceCollection) -> None:
    """Verify that the working directory is clean."""
    with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
        status = repo.get_current_status()
        assert status.files.are_clean_including_untracked, "Working directory should be clean"


def verify_file_exists_with_content(
    open_repo: Callable[[], ContextManager[WritableGitRepo]], filename: str, expected_content: str
) -> None:
    """Verify that a file exists with expected content."""
    with open_repo() as repo:
        file_path = Path(repo.get_repo_path()) / filename
        assert file_path.exists(), f"File {filename} should exist"
        assert file_path.read_text() == expected_content, f"File {filename} should have expected content"


def verify_file_does_not_exist(project: Project, services: CompleteServiceCollection, filename: str) -> None:
    """Verify that a file does not exist."""
    with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
        file_path = Path(repo.get_repo_path()) / filename
        assert not file_path.exists(), f"File {filename} should not exist"


def enable_sync(
    client: TestClient, project: Project, task: Task, is_stashing_ok: bool
) -> tuple[int, dict[str, Any] | str, httpx.Response]:
    """
    Helper to enable sync and return (status_code, result_or_error, full_response).

    Returns:
        tuple containing:
        - status_code: HTTP status code
        - result_or_error: Parsed SyncToTaskResult dict if successful, or error detail string if failed
        - full_response: Full httpx.Response object for additional inspection
    """
    response = client.post(
        f"/api/sync/projects/{project.object_id}/tasks/{task.object_id}/enable",
        json=model_dump(EnableLocalSyncRequest(is_stashing_ok=is_stashing_ok), is_camel_case=True),
    )
    if response.status_code == 200:
        return response.status_code, response.json(), response
    else:
        return response.status_code, response.json().get("detail", "Unknown error"), response


def validate_disabled_sync_was_stopped_from_pause(result: dict) -> None:
    """Helper to check if disable sync result indicates it was stopped from a paused state."""
    assert "wasExistingSyncStoppedFromPause" in result["result"], f"disable_sync result malformed {result=}"
    assert result["result"]["wasExistingSyncStoppedFromPause"], f"sync should have stopped from pause {result=}"


def disable_sync(client: TestClient, project: Project, task: Task) -> tuple[int, dict[str, Any] | str, httpx.Response]:
    """
    Helper to disable sync and return (status_code, result_or_error, full_response).

    Returns:
        tuple containing:
        - status_code: HTTP status code
        - result_or_error: Parsed DisableLocalSyncResponse dict if successful, or error detail string if failed
        - full_response: Full httpx.Response object for additional inspection
    """
    response = client.post(
        f"/api/sync/projects/{project.object_id}/tasks/{task.object_id}/disable",
    )
    if response.status_code == 200:
        return 200, response.json(), response
    else:
        return response.status_code, response.json().get("detail", "Unknown error"), response


def delete_stash(client: TestClient, project: Project, stash_ref: str) -> tuple[int, None | str, httpx.Response]:
    """
    Helper to delete stash and return (status_code, result_or_error, full_response).

    Returns:
        tuple containing:
        - status_code: HTTP status code
        - result_or_error: None if successful, or error detail string if failed
        - full_response: Full httpx.Response object for additional inspection
    """
    response = client.post(
        f"/api/sync/projects/{project.object_id}/stash/delete",
        json=model_dump(DeleteSyncStashRequest(absolute_stash_ref=stash_ref), is_camel_case=True),
    )
    if response.status_code == 200:
        return response.status_code, None, response
    else:
        return response.status_code, response.json().get("detail", "Unknown error"), response


def restore_stash(
    client: TestClient, project: Project, stash_ref: str
) -> tuple[int, dict | None | str, httpx.Response]:
    """
    Helper to restore stash and return (status_code, result_or_error, full_response).

    Returns:
        tuple containing:
        - status_code: HTTP status code
        - result_or_error: Parsed LocalRepoInfo dict (or None) if successful, or error detail string if failed
        - full_response: Full httpx.Response object for additional inspection
    """
    response = client.post(
        f"/api/sync/projects/{project.object_id}/stash/restore",
        json=model_dump(RestoreSyncStashRequest(absolute_stash_ref=stash_ref), is_camel_case=True),
    )
    if response.status_code == 200:
        return response.status_code, response.json(), response
    else:
        return response.status_code, response.json().get("detail", "Unknown error"), response


def _validate_no_local_sync_state_present(
    test_service_collection: CompleteServiceCollection, failure_message: str
) -> None:
    """Validate that no local sync sessions or stashes are active."""
    ls_state = test_service_collection.local_sync_service.get_session_state()
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        stash_singleton = read_global_stash_singleton_if_present(test_service_collection.git_repo_service, transaction)

    assert ls_state is None and stash_singleton is None, f"{failure_message}: {ls_state=}, {stash_singleton=}"


def validate_no_local_sync_sessions_or_stashes_bleed_across_tests(
    services: CompleteServiceCollection, function_name: str
) -> Generator[None, None, None]:
    _validate_no_local_sync_state_present(services, f"{function_name} pre-check found active local_sync/stash state")
    yield
    _validate_no_local_sync_state_present(services, f"{function_name} left around active local_sync/stash state")


def put_user_ahead_on_task_branch(open_repo: Callable[[], ContextManager[WritableGitRepo]], task_branch: str) -> None:
    """Put the user ahead of the agent on the task branch by committing a new file."""
    with open_repo() as repo:
        original_branch = repo.get_current_git_branch()
        repo._run_git(["checkout", task_branch])
        ahead_file = Path(repo.get_repo_path()) / "user_ahead.txt"
        ahead_file.write_text("user is ahead")
        repo._run_git(["add", "user_ahead.txt"])
        repo._run_git(["commit", "-m", "user ahead commit"])
        repo._run_git(["checkout", original_branch])


@contextmanager
def verified_dirty_repo_preservation_scenario(
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
) -> Generator[str, None, None]:
    """
    Context manager to set up a simple dirty repo scenario for testing.

    Creates a dirty repo with both modified and staged files.
    Yields the original branch name for verification.
    Cleans up changes on exit.
    """
    # Create some uncommitted work on main branch
    with open_repo() as repo:
        original_branch = repo.get_current_git_branch()
        setup_dirty_files(repo)
        setup_staged_files(repo)
    verify_file_exists_with_content(open_repo, "dirty_file.txt", "dirty content")
    verify_file_exists_with_content(open_repo, "staged_file.txt", "staged content")

    yield original_branch

    # Verify we end on the original branch with changes
    with open_repo() as repo:
        current_branch = repo.get_current_git_branch()
        assert current_branch == original_branch, (
            f"Should still be on original branch after failed sync. {current_branch=}, {original_branch=}"
        )
    verify_file_exists_with_content(open_repo, "dirty_file.txt", "dirty content")
    verify_file_exists_with_content(open_repo, "staged_file.txt", "staged content")


def enable_sync_and_expect_success(
    client: TestClient, project: Project, task: Task, is_stashing_ok: bool = False
) -> None:
    """Enable sync and assert it succeeds."""
    response = client.post(
        f"/api/sync/projects/{project.object_id}/tasks/{task.object_id}/enable",
        json=model_dump(EnableLocalSyncRequest(is_stashing_ok=is_stashing_ok), is_camel_case=True),
    )
    assert response.status_code == 200, f"Should successfully enable sync. {response.status_code=}, {response.text=}"


def _drain_queue_into_list(queue: Queue[T]) -> list[T]:
    items = []
    while queue.qsize() > 0:
        try:
            items.append(queue.get_nowait())
        except Empty:
            break
    return items


def validate_messages_in_order(messages: Iterable[MessageTypes], message_types: tuple[type, ...]) -> None:
    current_type_index = 0
    for i, msg in enumerate(messages):
        if current_type_index >= len(message_types):
            return
        if not isinstance(msg, message_types[current_type_index]):
            continue
        current_type_index += 1
    if current_type_index >= len(message_types):
        return
    ordered_message_needles = f"ordered messages: [{', '.join(mt.__name__ for mt in message_types)}]"
    missed = message_types[current_type_index].__name__
    ordered_haystack = "seen: " + ", ".join(type(m).__name__ for m in messages)
    raise AssertionError(f"Missing {ordered_message_needles}[{current_type_index}]. {ordered_haystack} ({missed}=)")


class BatchExtractor:
    def __init__(self, queue: Queue[MessageTypes]) -> None:
        self.queue = queue

    def get_batch(self) -> tuple[MessageTypes, ...]:
        return tuple(_drain_queue_into_list(self.queue))

    def wait_for_new_messages(
        self, expected_types_in_order: tuple[type[Any], ...], timeout: float = 5.0, clear_first: bool = False
    ) -> tuple[MessageTypes, ...]:
        if clear_first:
            _discard = self.get_batch()
        start_time = time.time()
        seen = []
        while start_time + timeout > time.time():
            seen.extend(self.get_batch())
            try:
                validate_messages_in_order(seen, expected_types_in_order)
                return tuple(seen)
            except AssertionError:
                if time.time() - start_time > timeout:
                    raise
            time.sleep(0.1)
        validate_messages_in_order(seen, expected_types_in_order)
        return tuple(seen)


@contextmanager
def task_message_batch_extractor(
    task: Task, services: CompleteServiceCollection
) -> Generator[BatchExtractor, None, None]:
    """subscribe to task messages and provide a callable to extract all messages currently in the queue."""
    with services.task_service.subscribe_to_task(task.object_id) as message_queue:
        message_queue = cast(Queue[MessageTypes], message_queue)
        yield BatchExtractor(message_queue)


def make_open_repo_helper(
    services: CompleteServiceCollection, project: Project
) -> Callable[[], ContextManager[WritableGitRepo]]:
    """Create an open_repo helper function for a given project."""
    return lambda: services.git_repo_service.open_local_user_git_repo_for_write(project)
