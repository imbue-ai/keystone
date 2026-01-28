"""
Integration tests for Local Sync stashing functionality.

Tests verify the stashing behavior when enabling/disabling local sync, including:
- Validation errors when is_stashing_ok=False
- Validation errors when is_stashing_ok=True for invalid cases
- Stash creation, clearing, and restoration
- Stash deletion
- Stash preservation after pause state
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from imbue_core.async_monkey_patches_test import expect_at_least_logged_errors
from imbue_core.testing_utils import integration_test
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.services.local_sync_service import local_sync_session
from sculptor.web.auth import authenticate_anonymous
from tests.integration.api.conftest import create_test_task_with_state
from tests.integration.api.local_sync_test_helpers import delete_stash
from tests.integration.api.local_sync_test_helpers import disable_sync
from tests.integration.api.local_sync_test_helpers import enable_sync
from tests.integration.api.local_sync_test_helpers import get_stash_singleton
from tests.integration.api.local_sync_test_helpers import put_user_ahead_on_task_branch
from tests.integration.api.local_sync_test_helpers import restore_stash
from tests.integration.api.local_sync_test_helpers import setup_dirty_files
from tests.integration.api.local_sync_test_helpers import setup_staged_files
from tests.integration.api.local_sync_test_helpers import setup_untracked_files
from tests.integration.api.local_sync_test_helpers import validate_disabled_sync_was_stopped_from_pause
from tests.integration.api.local_sync_test_helpers import validate_no_local_sync_sessions_or_stashes_bleed_across_tests
from tests.integration.api.local_sync_test_helpers import verified_dirty_repo_preservation_scenario
from tests.integration.api.local_sync_test_helpers import verify_file_does_not_exist
from tests.integration.api.local_sync_test_helpers import verify_file_exists_with_content
from tests.integration.api.local_sync_test_helpers import verify_working_directory_clean


@pytest.fixture(autouse=True)
def validate_no_bleeding_across_tests(
    mock_repo_path: Path,  # just to force sequencing so a crash doesn't create misleading errors
    request: pytest.FixtureRequest,
    test_service_collection: CompleteServiceCollection,
) -> Generator[None, None, None]:
    function_name = request.function.__name__
    return validate_no_local_sync_sessions_or_stashes_bleed_across_tests(test_service_collection, function_name)


@integration_test
def test_refuses_sync_without_stashing_for_all_dirty_states(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
) -> None:
    """
    Test that sync refuses to start when is_stashing_ok=False for any type of working directory changes.

    Tests all user-side conditions from _validate_branches_are_safely_syncable for user-side git state when is_stashing_ok=False:
    - Dirty (modified) files
    - Staged files
    - Untracked files
    """
    user_session = authenticate_anonymous(test_service_collection, RequestID())
    task = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)

    @contextmanager
    def git_scene(setup: Callable[[WritableGitRepo], None]) -> Generator[None, None, None]:
        with open_repo() as repo:
            setup(repo)
        yield
        with open_repo() as repo:
            repo._run_git(["reset", "--hard"])

    with git_scene(setup_dirty_files):
        status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=False)
        assert status == 409, f"Should refuse sync with dirty files when stashing disabled. {status=}, {error=}"

    with git_scene(setup_staged_files):
        status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=False)
        assert status == 409, f"Should refuse sync with staged files when stashing disabled. {status=}, {error=}"

    with git_scene(setup_untracked_files):
        status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=False)
        assert status == 409, f"Should refuse sync with untracked files when stashing disabled. {status=}, {error=}"


@integration_test
def test_refuses_sync_with_intermediate_git_state_even_with_stashing(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
) -> None:
    """
    Test that sync refuses to start when git is in an intermediate state, even with stashing enabled.

    Tests ExpectedStartupBlocker.USER_GIT_STATE_UNSTASHABLE - intermediate states like merge/rebase/
    cherry-pick in progress cannot be stashed, so sync must be refused regardless of is_stashing_ok.
    """
    user_session = authenticate_anonymous(test_service_collection, RequestID())
    task = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)

    # get merge conflict
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_write(active_test_project) as repo:
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
        with pytest.raises(GitRepoError):
            repo._run_git(["merge", "conflict-branch"])

    status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
    assert status == 409, (
        f"Should refuse sync with intermediate git state even when stashing is enabled. {status=}, {error=}"
    )
    assert isinstance(error, str), f"Error should be a string, got {type(error)}"
    # Should mention intermediate state or similar issue
    assert "intermediate" in error.lower() or "merge" in error.lower() or "conflict" in error.lower(), (
        f"Error message should mention intermediate git state, got: {error}"
    )


@integration_test
def test_refuses_sync_with_user_ahead_even_with_stashing(
    client: TestClient,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
    fresh_task_and_state: tuple[Task, AgentTaskStateV1, str],
) -> None:
    """
    Test that sync refuses when user is ahead of agent, even with stashing enabled.

    Tests ExpectedStartupBlocker.USER_BRANCH_AHEAD_OF_AGENT - when user has commits that the agent
    doesn't have, those commits would be lost if sync proceeded. This is blocked regardless of
    is_stashing_ok because stashing only handles working directory changes, not commits.
    """
    task, _, branch = fresh_task_and_state

    # Make user ahead of the task branch
    with open_repo() as repo:
        # Make sure we're on the task branch
        repo._run_git(["checkout", branch])

        ahead_file = Path(repo.get_repo_path()) / "user_ahead.txt"
        ahead_file.write_text("user is ahead")
        repo._run_git(["add", "user_ahead.txt"])
        repo._run_git(["commit", "-m", "User ahead commit"])

    # Try to enable sync with stashing - should fail because user is ahead
    status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
    assert status == 409, f"Should refuse sync when user is ahead of agent, even with stashing. {status=}, {error=}"


@integration_test
def test_stash_created_on_sync_and_popped_on_clean_unsync(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
) -> None:
    """
    Test that stash is created when sync is enabled with dirty state, and automatically popped when disabled.

    This test verifies the normal stashing flow:
    1. Working directory has changes
    2. Enable sync with is_stashing_ok=True
    3. Stash is created and working directory is cleared
    4. Disable sync from non-paused state
    5. Stash is automatically restored and deleted
    """
    user_session = authenticate_anonymous(test_service_collection, RequestID())

    task = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)

    # Create dirty files
    with open_repo() as repo:
        setup_dirty_files(repo)

    verify_file_exists_with_content(open_repo, "dirty_file.txt", "dirty content")

    # Enable sync with stashing
    status, result, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
    assert status == 200, f"Should enable sync with stashing. {status=}, {result=}"

    # Verify stash was created
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should be created"

    # Verify working directory is clean
    verify_working_directory_clean(active_test_project, test_service_collection)
    verify_file_does_not_exist(active_test_project, test_service_collection, "dirty_file.txt")

    # Disable sync (from non-paused state)
    status, result, _ = disable_sync(client, active_test_project, task)
    assert status == 200, f"Should successfully disable sync. {status=}, {result=}"

    # Verify stash was automatically restored
    verify_file_exists_with_content(open_repo, "dirty_file.txt", "dirty content")

    # Verify stash was deleted
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is None, "Stash should be absent after restore"


@integration_test
def test_stash_deletion_during_sync_causes_no_issues(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
) -> None:
    """
    Test that deleting a stash while sync is active doesn't cause issues.

    This test verifies:
    1. Stash can be deleted while sync session is active
    2. Disabling sync after stash deletion completes successfully
    3. Working directory remains clean (since stash was deleted, nothing to restore)
    """
    user_session = authenticate_anonymous(test_service_collection, RequestID())

    task = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)

    # Create dirty fi
    with open_repo() as repo:
        og_branch = repo.get_current_git_branch()
        setup_dirty_files(repo)

    # Enable sync with stashing
    status, result, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
    assert status == 200, f"Should successfully enable sync with stashing. {status=}, {result=}"

    # Get stash ref
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should be created"
    stash_ref = stash_singleton[0].stash.absolute_stash_ref

    # Delete stash while sync is running
    status, error, _ = delete_stash(client, active_test_project, stash_ref)
    assert status == 200, f"Should successfully delete stash during sync. {status=}, {error=}"

    # Verify stash is gone
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is None, f"Stash should be deleted {stash_singleton=}"

    # Disable sync - should complete successfully even though stash is gone
    status, result, _ = disable_sync(client, active_test_project, task)
    assert status == 200, f"Should successfully disable sync after stash deletion. {status=}, {result=}"

    # Working directory should remain clean (no stash to restore)
    with open_repo() as repo:
        new_branch = repo.get_current_git_branch()
        assert new_branch == og_branch, f"should return to original branch after unsync {new_branch=}, {og_branch=}"
    verify_working_directory_clean(active_test_project, test_service_collection)
    verify_file_does_not_exist(active_test_project, test_service_collection, "dirty_file.txt")


@integration_test
def test_stash_remains_after_disabling_from_pause(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
) -> None:
    """
    Test that stash remains when sync is disabled from a paused state.

    This test verifies:
    1. Sync can be paused by changing branches
    2. When sync is disabled from a paused state, the stash is NOT automatically restored
    3. Stash remains for manual triage
    4. User can manually restore or delete the stash later
    """
    user_session = authenticate_anonymous(test_service_collection, RequestID())

    task = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)

    with open_repo() as repo:
        setup_dirty_files(repo)

    # Enable sync with stashing
    status, result, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
    assert status == 200, f"Should successfully enable sync with stashing. {status=}, {result=}"

    # Verify stash was created
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should be created"
    stash_ref = stash_singleton[0].stash.absolute_stash_ref

    # Cause a pause by switching branches (this is a "pause-worthy" action)
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_write(active_test_project) as repo:
        repo._run_git(["checkout", "-b", "different-branch"])

    # Disable sync (from paused state)
    status, result, _ = disable_sync(client, active_test_project, task)
    assert status == 200 and isinstance(result, dict), (
        f"Should successfully disable sync from paused state. {status=}, {result=}"
    )
    validate_disabled_sync_was_stopped_from_pause(result)

    # Validate response payload includes information about the stash being preserved
    assert isinstance(result, dict), f"Result should be a dict, got {type(result)}"
    assert "result" in result, "Response should include result information"
    unsync_result = result["result"]
    assert "wasExistingSyncStoppedFromPause" in unsync_result, "Result should indicate if sync was stopped from pause"
    assert unsync_result["wasExistingSyncStoppedFromPause"], "Should indicate that sync was stopped from paused state"

    # Verify stash still exists (NOT automatically restored from pause)
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should remain after disable from pause"
    assert stash_singleton[0].stash.absolute_stash_ref == stash_ref, "Should be the same stash"

    # Working directory should be clean (stash was not restored)
    verify_working_directory_clean(active_test_project, test_service_collection)
    verify_file_does_not_exist(active_test_project, test_service_collection, "dirty_file.txt")

    # User can manually delete the stash
    status, error, _ = delete_stash(client, active_test_project, stash_ref)
    assert status == 200, f"Should be able to delete stash after disable from pause. {status=}, {error=}"

    # Verify stash is gone
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is None, "Stash should be deleted"


@integration_test
def test_standalone_stash_restore_behavior(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
) -> None:
    """
    Test standalone stash restore behavior outside of active sync session.

    This test verifies:
    1. After disabling from pause, stash remains for manual restore
    2. Stash restore is refused when working directory is dirty
    3. Stash restore succeeds when working directory is clean
    4. Stash restores to the correct branch with correct content
    5. Stash is absent after successful restore
    """
    user_session = authenticate_anonymous(test_service_collection, RequestID())

    original_branch = "main"

    task = create_test_task_with_state(
        client, user_session, active_test_project, test_service_collection, branch=original_branch
    )

    with open_repo() as repo:
        setup_dirty_files(repo)

    # Enable sync with stashing
    status, result, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
    assert status == 200, f"Should successfully enable sync with stashing. {status=}, {result=}"

    # Verify stash was created
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should be created"
    stash_ref = stash_singleton[0].stash.absolute_stash_ref
    assert stash_singleton[0].stash.source_branch == original_branch, "Stash should track source branch"

    # Cause pause by switching branches
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_write(active_test_project) as repo:
        repo._run_git(["checkout", "-b", "pause-branch"])

    # Disable sync from paused state (stash should remain)
    status, result, _ = disable_sync(client, active_test_project, task)
    assert status == 200 and isinstance(result, dict), (
        f"Should successfully disable sync from paused state. {status=}, {result=}"
    )
    validate_disabled_sync_was_stopped_from_pause(result)

    # Verify stash still exists
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should remain after disable from pause"

    # Create dirty state on current branch
    with open_repo() as repo:
        setup_staged_files(repo)

    # Try to restore stash with dirty working directory - should be refused
    status, error, _ = restore_stash(client, active_test_project, stash_ref)
    assert status == 409, f"Should refuse to restore stash with dirty working directory. {status=}, {error=}"

    # Verify stash still exists
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should still exist after failed restore"

    # Clean the dirty state
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_write(active_test_project) as repo:
        repo._run_git(["reset", "--hard"])

    # Now restore should succeed
    status, result, _ = restore_stash(client, active_test_project, stash_ref)
    assert status == 200, f"Should successfully restore stash with clean working directory. {status=}, {result=}"

    # Verify we're back on the source branch
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_read(active_test_project) as repo:
        current_branch = repo.get_current_git_branch()
        assert current_branch == original_branch, "Should be back on source branch after restore"

    # Verify files were restored
    verify_file_exists_with_content(open_repo, "dirty_file.txt", "dirty content")

    # Verify stash was deleted
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is None, "Stash should be deleted after successful restore"


@integration_test
def test_can_switch_sync_between_tasks_with_stash_present(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
) -> None:
    """
    Test that we can switch syncs between tasks even when a stash is present.

    This test verifies:
    1. Task 1 sync can be enabled with dirty files (creating a stash)
    2. Task 2 sync can be enabled, which switches sync from task 1 to task 2
    3. The stash from task 1 remains after the switch
    4. Task 2 becomes the active sync
    5. Stash is restored after disabling sync from task 2
    """
    user_session = authenticate_anonymous(test_service_collection, RequestID())

    # Create two tasks
    task1 = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)
    task2 = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)

    assert isinstance(task1.current_state, AgentTaskStateV1)
    assert isinstance(task2.current_state, AgentTaskStateV1)
    task1_branch = task1.current_state.branch_name
    task2_branch = task2.current_state.branch_name
    assert task1_branch is not None, "Task 1 branch name should be set"
    assert task2_branch is not None, "Task 2 branch name should be set"
    assert task1_branch != task2_branch, "Tasks should have different branches"

    # Create dirty files on main branch
    with open_repo() as repo:
        repo._run_git(["checkout", "main"])
        setup_dirty_files(repo)

    # Enable sync on task 1 with stashing
    status, result, _ = enable_sync(client, active_test_project, task1, is_stashing_ok=True)
    assert status == 200, f"Should successfully enable sync on task 1 with stashing. {status=}, {result=}"

    # Verify stash was created
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should be created for task 1"
    stash_ref = stash_singleton[0].stash.absolute_stash_ref

    # Verify we're on task 1's branch
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_read(active_test_project) as repo:
        current_branch = repo.get_current_git_branch()
        assert current_branch == task1_branch, (
            f"Should be on task 1's branch after sync. {current_branch=}, {task1_branch=}"
        )

    # Verify working directory is clean
    verify_working_directory_clean(active_test_project, test_service_collection)

    with test_service_collection.git_repo_service.open_local_user_git_repo_for_write(active_test_project) as repo:
        setup_dirty_files(repo)

    # Now enable sync on task 2 - this should switch from task 1 to task 2
    status, result, _ = enable_sync(client, active_test_project, task2, is_stashing_ok=True)
    assert status == 200, f"Should successfully enable sync on task 2 (switching from task 1). {status=}, {result=}"

    # Verify we're now on task 2's branch
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_read(active_test_project) as repo:
        current_branch = repo.get_current_git_branch()
        assert current_branch == task2_branch, (
            f"Should be on task 2's branch after switch. {current_branch=}, {task2_branch=}"
        )

    # Verify working directory is still clean
    verify_working_directory_clean(active_test_project, test_service_collection)

    # Verify stash still exists (switching doesn't delete the stash)
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is not None, "Stash should still exist after switching tasks"
    assert stash_singleton[0].stash.absolute_stash_ref == stash_ref, "Should be the same stash"

    # Disable sync on task 2
    status, result, _ = disable_sync(client, active_test_project, task2)
    assert status == 200, f"Should successfully disable sync on task 2. {status=}, {result=}"

    # Verify we're back on main branch
    with test_service_collection.git_repo_service.open_local_user_git_repo_for_read(active_test_project) as repo:
        current_branch = repo.get_current_git_branch()
        assert current_branch == "main", f"Should be back on main branch after disable. {current_branch=}"

    # Stash should still exist (we disabled from non-paused state, but stash restoration only happens
    # if the stash was created by the current sync session)
    stash_singleton = get_stash_singleton(test_service_collection, user_session)
    assert stash_singleton is None, "Stash should be restored after switch (transitive)"

    verify_file_exists_with_content(open_repo, "dirty_file.txt", "dirty content")


@integration_test
def test_no_reset_on_unsync_after_failed_sync_due_to_user_ahead(
    client: TestClient,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
    fresh_task_and_state: tuple[Task, AgentTaskStateV1, str],
    mocker: MockerFixture,
) -> None:
    """
    Test that unsync after failed sync (user ahead of agent) doesn't reset when it shouldn't.

    1. User is ahead of agent
    2. Attempt to sync fails due to being ahead
    3. Unsync is called (is_startup_error=True)
    4. Git reset should NOT happen as no git state was changed
    """
    task, _, task_branch = fresh_task_and_state

    # Create some uncommitted work on main branch
    with open_repo() as repo:
        original_branch = repo.get_current_git_branch()

    injected_error = "no reset should happen"

    def _broken_reset(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(injected_error)

    mocker.patch.object(LocalWritableGitRepo, "reset_working_directory", _broken_reset)

    put_user_ahead_on_task_branch(open_repo, task_branch)

    status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=False)
    assert status == 409, f"Should refuse sync when user is ahead of agent. {status=}, {error=}"
    # Verify we're still on the original branch
    with open_repo() as repo:
        current_branch = repo.get_current_git_branch()
        assert current_branch == original_branch, (
            f"Should still be on original branch after failed sync. {current_branch=}, {original_branch=}"
        )


@integration_test
def test_state_preserved_on_unsync_after_failed_sync_when_user_ahead(
    client: TestClient,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
    fresh_task_and_state: tuple[Task, AgentTaskStateV1, str],
) -> None:
    """
    Test that unsync after failed sync (user ahead of agent, with file needing stash) doesn't reset.

    Same as above but with actual state to lose.
    """
    task, _, task_branch = fresh_task_and_state
    put_user_ahead_on_task_branch(open_repo, task_branch)

    with verified_dirty_repo_preservation_scenario(open_repo):
        # Try to enable sync with stashing - should fail because user is ahead of agent
        # Even though we allow stashing, being ahead should still block the sync
        status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
        assert status == 409, (
            f"Should refuse sync when user is ahead of agent, even with stashing. {status=}, {error=}"
        )


@integration_test
def test_stash_restored_on_mocked_mutagen_error(
    client: TestClient,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
    fresh_task_and_state: tuple[Task, AgentTaskStateV1, str],
    mocker: MockerFixture,
) -> None:
    """
    Test that mid-startup sync sessions fully restore state on unexpected issues
    """
    task, _, _ = fresh_task_and_state

    def _broken_whatever(*args: Any, **kwargs: Any) -> None:
        # note - shows up in printed traceback but not in expected_at_least_logged_errors
        raise RuntimeError("broken state or whatevah, yeah")

    mocker.patch.object(local_sync_session, "create_bidirectional_user_prioritized_sync", _broken_whatever)

    with verified_dirty_repo_preservation_scenario(open_repo):
        with expect_at_least_logged_errors({"Failed to enable sync for task"}):
            status, error, _ = enable_sync(client, active_test_project, task, is_stashing_ok=True)
        assert status == 500, f"Should refuse sync when user is behind agent, even with stashing. {status=}, {error=}"
