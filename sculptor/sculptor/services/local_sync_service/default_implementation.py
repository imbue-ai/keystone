import threading
from contextlib import contextmanager
from typing import Callable
from typing import ContextManager
from typing import Generator
from typing import TypeVar

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.serialization import SerializedException
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import LocalSyncDisabledMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupAndEnabledMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupStartedMessage
from sculptor.interfaces.agents.agent import LocalSyncTeardownProgressMessage
from sculptor.interfaces.agents.agent import LocalSyncTeardownStartedMessage
from sculptor.interfaces.agents.agent import LocalSyncTeardownStep
from sculptor.interfaces.agents.agent import UnexpectedErrorRunnerMessage
from sculptor.primitives.ids import RequestID
from sculptor.services.data_model_service.api import DataModelService
from sculptor.services.data_model_service.data_types import DataModelTransaction
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.git_repo_service.error_types import GitStashApplyError
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.services.git_repo_service.ref_namespace_stasher import build_sculptor_stash_reader
from sculptor.services.git_repo_service.ref_namespace_stasher import pop_namespaced_stash_into_source_branch
from sculptor.services.local_sync_service._environment_restart_helpers import EnvironmentRestartHandler
from sculptor.services.local_sync_service.api import LocalSyncDisabledActionTaken
from sculptor.services.local_sync_service.api import LocalSyncService
from sculptor.services.local_sync_service.api import LocalSyncSessionState
from sculptor.services.local_sync_service.api import SyncSessionInfo
from sculptor.services.local_sync_service.api import SyncToTaskResult
from sculptor.services.local_sync_service.api import UnsyncFromTaskResult
from sculptor.services.local_sync_service.errors import LocalSyncError
from sculptor.services.local_sync_service.errors import MutagenSyncError
from sculptor.services.local_sync_service.errors import OtherSyncTransitionInProgressError
from sculptor.services.local_sync_service.errors import SyncCleanupError
from sculptor.services.local_sync_service.errors import SyncStartupError
from sculptor.services.local_sync_service.local_sync_session import LocalSyncCommonInputs
from sculptor.services.local_sync_service.local_sync_session import LocalSyncSession
from sculptor.services.local_sync_service.local_sync_update_messenger import LocalSyncUpdateMessenger
from sculptor.services.local_sync_service.local_sync_update_messenger import emit_local_sync_posthog_event_if_tracked
from sculptor.services.local_sync_service.mutagen_utils import get_all_sculptor_mutagen_sessions_for_projects
from sculptor.services.local_sync_service.mutagen_utils import mutagen_sync_name_for
from sculptor.services.local_sync_service.mutagen_utils import stop_mutagen_daemon
from sculptor.services.local_sync_service.mutagen_utils import terminate_mutagen_session
from sculptor.services.task_service.api import TaskService
from sculptor.utils.timeout import log_runtime
from sculptor.utils.timeout import log_runtime_decorator

ExceptionT = TypeVar("ExceptionT", bound=Exception)
RepoOpener = Callable[[], ContextManager[WritableGitRepo]]


class DefaultLocalSyncService(LocalSyncService):
    """Manages bidirectional sync between local development environment and task containers using Mutagen"""

    git_repo_service: GitRepoService
    task_service: TaskService
    data_model_service: DataModelService

    # FIXME: add handling for multiple sessions_by_project_id
    _session: LocalSyncSession | None = PrivateAttr(default=None)

    # Used to reject concurrent sync state transitions (does _not_ block/enqueue)
    _sync_transition_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _current_sync_task_id(self) -> TaskID | None:
        info = self._session.session_info if self._session else None
        return info.task_id if info else None

    def get_session_state(self) -> LocalSyncSessionState | None:
        return self._session.state if self._session else None

    def start(self) -> None:
        logger.info("Starting local sync service")
        self._cleanup_dangling_mutagen_sessions()

    def stop(self) -> None:
        """Stop the service and clean up any active syncs."""
        # TODO: making exception to top-level transaction ownership here for now
        with self.data_model_service.open_transaction(request_id=RequestID()) as transaction:
            self.cleanup_current_sync(transaction=transaction)
        self.ensure_session_is_stopped()
        self._cleanup_dangling_mutagen_sessions()
        stop_mutagen_daemon(self.concurrency_group)

    def ensure_session_is_stopped(self) -> bool | None:
        session = self._session
        if session is None:
            return
        final_status = session.stop()
        self._ensure_no_active_mutagen_sessions_exist_for_project(session.session_info.project_id)
        self._session = None
        return final_status.is_paused

    @contextmanager
    def maybe_acquire_sync_transition_lock(self) -> Generator[bool, None, None]:
        is_acquired = self._sync_transition_lock.acquire(blocking=False)
        try:
            yield is_acquired
        finally:
            if is_acquired:
                self._sync_transition_lock.release()

    @contextmanager
    def maybe_guarantee_no_new_or_active_session(self) -> Generator[bool, None, None]:
        with self.maybe_acquire_sync_transition_lock() as is_acquired:
            if self._session is not None:
                yield False
                return
            yield is_acquired

    # TODO: Have sculptor add a double-tap test when integration testing
    @log_runtime_decorator("LOCAL_SYNC.sync_to_task")
    def sync_to_task(
        self, task_id: TaskID, transaction: DataModelTransaction, task: Task | None = None, is_stashing_ok: bool = True
    ) -> SyncToTaskResult:
        with self.maybe_acquire_sync_transition_lock() as is_acquired:
            if not is_acquired:
                raise OtherSyncTransitionInProgressError(action="sync to task", new_task_id=task_id)
            args = self._prep_task_task_branch_and_repo_opener(task_id=task_id, transaction=transaction, task=task)
            return self._sync_to_task(transaction, *args, is_stashing_ok=is_stashing_ok)

    def _sync_to_task(
        self,
        transaction: DataModelTransaction,
        task: Task,
        branch_name: str,
        repo_opener: RepoOpener,
        is_stashing_ok: bool,
    ) -> SyncToTaskResult:
        """Start bidirectional working tree + unidirectional git sync for a task."""
        logger.info("Starting sync for task {}", task.object_id)

        task_env = self.task_service.get_task_environment(task_id=task.object_id, transaction=transaction)
        assert task_env is not None, f"Task environment not found for task {task.object_id}"

        new_info: SyncSessionInfo | None = None
        previous_sync = self._session.session_info if self._session else None
        is_switching_within_same_project = previous_sync is not None and previous_sync.project_id == task.project_id
        try:
            # Disable any currently active sync
            # FIXME: multiple sessions_by_project_id
            if previous_sync:
                self._unsync_due_to_switch(transaction, previous_sync, task, branch_name)

            self._ensure_no_active_mutagen_sessions_exist_for_project(project_id=task.project_id)

            with repo_opener() as repo:
                if previous_sync and is_switching_within_same_project:
                    original_branch = previous_sync.original_branch
                    new_info = _carry_forward_info(
                        previous_sync,
                        new_task=task,
                        new_sync_branch=branch_name,
                    )
                else:
                    original_branch = repo.get_current_git_branch()
                    new_info = self._build_new_sync_info(task, repo, target_branch=branch_name)

                # Send started message ASAP as we consider everything here setup
                with self.data_model_service.open_transaction(request_id=RequestID()) as message_transaction:
                    self._send_message(
                        LocalSyncSetupStartedMessage(
                            sync_branch=branch_name,
                            original_branch=original_branch,
                        ),
                        task.object_id,
                        message_transaction,
                    )

                with log_runtime("LOCAL_SYNC.LocalSyncSession.build_and_start"):
                    session_concurrency_group = self.concurrency_group.make_concurrency_group(
                        name=f"local_sync_session_for_{task.object_id}"
                    )
                    session = LocalSyncSession.build_and_start(
                        inputs=LocalSyncCommonInputs(
                            agent_environment=task_env,
                            session_info=new_info,
                            user_repo_path=repo.get_repo_path(),
                            messenger=self._build_update_messenger(new_info, session_concurrency_group),
                        ),
                        restart_handler=EnvironmentRestartHandler(
                            task_id=new_info.task_id,
                            task_service=self.task_service,
                        ),
                        concurrency_group=session_concurrency_group,
                        is_stashing_ok=is_stashing_ok,
                    )
                    self._session = session
                # Setup messages handled by session now
                logger.info("Successfully enabled sync for task {}", task.object_id)
                return SyncToTaskResult(newly_created_stash=session.session_info.stash)
        # An expected known issue such as divergent git state
        except SyncStartupError:
            # we always at least want to send the message
            unsync_attempt = self._unsync_from_task(
                task_id=task.object_id,
                transaction=transaction,
                is_startup_error=True,
                failed_session_start_info=new_info,
            )
            if unsync_attempt == LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED:
                # TODO surface this to user (very annoying)
                message = "Failed to cleanly rewind repo state after failed sync startup for task {}: {} info={}"
                logger.error(message, task.object_id, unsync_attempt, new_info)
            raise
        # Any other error must be handled generically
        except Exception as e:
            log_exception(
                e,
                "LOCAL_SYNC: Failed to start sync for task {task_id}",
                ExceptionPriority.LOW_PRIORITY,
                task_id=task.object_id,
            )
            startup_error = _derive_exception(
                SyncStartupError(
                    f"Failed to start sync for task {task.object_id}: {e}",
                    task_id=str(task.object_id),
                    task_branch=branch_name,
                ),
                from_cause=e,
            )
            self._on_exception_send_message(transaction, task.object_id, startup_error)
            unsync_attempt = self._unsync_from_task(
                task_id=task.object_id,
                transaction=transaction,
                is_startup_error=True,
                failed_session_start_info=new_info,
            )
            if unsync_attempt == LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED:
                # TODO surface this to user (very annoying)
                message = "Failed to cleanly rewind repo state after failed sync startup for task {}: {} info={}"
                logger.error(message, task.object_id, unsync_attempt, new_info)
            raise startup_error from e

    def _unsync_due_to_switch(
        self, transaction: DataModelTransaction, previous_sync: SyncSessionInfo, new_task: Task, new_branch_name: str
    ) -> None:
        "Disable any currently active sync. returns previous_sync, is_switching_within_same_project"
        is_switching_within_same_project = previous_sync.project_id == new_task.project_id
        # FIXME: multiple sessions_by_project_id
        # NOTE: Before we were just ignoring disable errors, but IDK why - if we fail to disable previous sync,
        # we really shouldn't go around resetting working dir, etc.
        with log_runtime("LOCAL_SYNC.sync_to_task._unsync_from_previous_task"):
            unsync_attempt_results = self._unsync_from_task(
                previous_sync.task_id,
                transaction=transaction,
                switching_to_task_in_same_project=is_switching_within_same_project,
            )
        if unsync_attempt_results == LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED:
            raise SyncStartupError(
                f"Cannot switch to task {new_task.object_id}: prior task {previous_sync.task_id} paused leaving repo state in need of triage",
                task_id=str(new_task.object_id),
                task_branch=new_branch_name,
            )

    @log_runtime_decorator("LOCAL_SYNC.unsync_from_task")
    def unsync_from_task(self, task_id: TaskID, transaction: DataModelTransaction) -> UnsyncFromTaskResult:
        with self.maybe_acquire_sync_transition_lock() as is_acquired:
            if not is_acquired:
                raise OtherSyncTransitionInProgressError(action="unsync from task", new_task_id=task_id)
            return self._unsync_from_task(task_id, transaction)

    def _unsync_from_task(
        self,
        task_id: TaskID,
        transaction: DataModelTransaction,
        switching_to_task_in_same_project: bool = False,
        is_startup_error: bool = False,
        failed_session_start_info: SyncSessionInfo | None = None,
    ) -> UnsyncFromTaskResult:
        """Stop sync and restore original state unless paused.

        NOTE: Should be fairly idempotent because we call this in the event of a startup error as well, to ensure everything is cleaned up.
        """
        og_info = failed_session_start_info or (self._session.session_info if self._session else None)

        unsync_reason_for_log = (
            " (switching_to_task_in_same_project)"
            if switching_to_task_in_same_project
            else " (is_startup_error)"
            if is_startup_error
            else ""
        )
        logger.info("Stopping active sync for task {}{}", task_id, unsync_reason_for_log)
        try:
            action_taken = self._disable_sync_for_task(
                task_id, transaction, is_startup_error, failed_session_start_info
            )

            task, _, repo_opener = self._prep_task_task_branch_and_repo_opener(task_id, transaction)

            with repo_opener() as repo:
                if action_taken != LocalSyncDisabledActionTaken.STOPPED_CLEANLY:
                    return self._early_unsync_result(repo, task_id, action_taken)

                # can't stop cleanly if no session is present
                assert og_info is not None, f"Impossible: {og_info=} action_taken is STOPPED_CLEANLY"

                # Early return if this is a startup error and no git state was actually affected
                # (no stash created and still on original branch means sync never got far enough to change anything)
                #
                # NOTE: If mutagen initial sync somehow fails part-way it attempts a reset_working_directory, but if _that_ fails we come to this block.
                # The edge-case where this is not a validation error depends on an unexpected failure:
                # 1. User is on a clean `sculptor/feat` branch at or behind agent.
                # 2. Agent branch `sculptor/feat` is dirty.
                # 3. Startup: User dir is overwritten by agent code, making it diry.
                # 4. Startup: Fails, but also fails to `reset_working_directory` for some reason.
                # 5. We get here, preemptively return thinking the dirty user dir is their own changes.
                # Pragmatically this is fine - our other cleanup would likely fail anyways (already error state)
                #
                # TODO: `almost_certainly` here just references that we should raise/except clearer errors,
                # instead of doing this kind of state-based inference
                is_almost_certainly_a_validation_error = (
                    is_startup_error
                    and og_info.stash is None
                    and repo.get_current_git_branch() == og_info.original_branch
                )
                if is_almost_certainly_a_validation_error:
                    logger.info(
                        "Startup error for task {} with no git state changes (no stash, still on original branch), skipping git reset",
                        task_id,
                    )
                    return self._early_unsync_result(repo, task_id, action_taken)

                is_reset = self._git_reset_if_safe(og_info, repo)
                if not is_reset:
                    return self._early_unsync_result(repo, task_id, LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED)

                if switching_to_task_in_same_project:
                    return self._early_unsync_result(repo, task_id, action_taken)

                # we have a verified clean dir and arent switching, time to rewind via checkout or stash pop
                assert action_taken == LocalSyncDisabledActionTaken.STOPPED_CLEANLY, "impossible"
                fully_successful_unsync_result = self._rewind_repo_state(task, repo, og_info)

            with self.data_model_service.open_transaction(request_id=RequestID()) as completion_transaction:
                self._send_message(LocalSyncDisabledMessage(), task_id, completion_transaction)
            return fully_successful_unsync_result

        # If we encounter any error at all, we need to send a message to fast forward to "unclean halt" or backpedal our promise accordingly.
        # We won't detect if we've STOPPED_FROM_PAUSE, but at least the user will see that we errored
        except Exception as e:
            with self.data_model_service.open_transaction(request_id=RequestID()) as completion_transaction:
                # If we managed to stop the session but encountered an error after, still send disabled signal
                if self._session is None:
                    self._send_message(LocalSyncDisabledMessage(), task_id, completion_transaction)
                else:
                    # TODO: This is also a fairly unexpected state - kind of defensive programming but could use more consideration
                    # Session is still active, send message to restore UI to ACTIVE state
                    self._send_message(LocalSyncSetupAndEnabledMessage(), task_id, completion_transaction)

            log_exception(
                e,
                "LOCAL_SYNC: Failed to disable sync for task {task_id}",
                ExceptionPriority.LOW_PRIORITY,
                task_id=task_id,
            )
            if isinstance(e, SyncCleanupError):
                self._on_exception_send_message(transaction, task_id, e)
                raise  # don't double-wrap
            cleanup_error = _derive_exception(
                SyncCleanupError(
                    f"Failed to clean up sync for task {task_id}: {e}",
                    task_id=task_id,
                    cleanup_step=_describe_error(e),
                ),
                from_cause=e,
            )
            self._on_exception_send_message(transaction, task_id, cleanup_error)
            raise cleanup_error from e

    def _early_unsync_result(
        self, repo: WritableGitRepo, task_id: TaskID, action_taken: LocalSyncDisabledActionTaken
    ) -> UnsyncFromTaskResult:
        assert self._session is None or self._session.session_info.task_id != task_id, (
            f"logic error in unsync_from_task: session still active for task {task_id} (wanted to respond with {action_taken=})"
        )
        with self.data_model_service.open_transaction(request_id=RequestID()) as completion_transaction:
            self._send_message(LocalSyncDisabledMessage(), task_id, completion_transaction)
        leftover_stash = build_sculptor_stash_reader(repo).maybe_get_singleton_stash()
        return UnsyncFromTaskResult(action_taken=action_taken, stash_from_start_of_operation=leftover_stash)

    def _disable_sync_for_task(
        self,
        task_id: TaskID,
        transaction: DataModelTransaction,
        is_startup_error: bool = False,
        failed_session_start_info: SyncSessionInfo | None = None,
    ) -> LocalSyncDisabledActionTaken:
        """Just disables the ongoing sync session and sends the appropriate domain-level messages"""
        unsyncing_info = failed_session_start_info or (self._session.session_info if self._session else None)

        if (not unsyncing_info) or unsyncing_info.task_id != task_id:
            # FIXME: Figure out how we distinguish is_local_syncing_task on the frontend and correct that state at startup if mangled
            # This is a stopgap to enable manual mitigation of the database being out of sync with the current_state due to failed server-termination cleanup
            # Leaving in until persisted local sync has sufficient cleanup/resiliency
            logger.debug("No active sync found for task {}. Sending stop message in case of manual cleanup", task_id)
            self._send_message(LocalSyncDisabledMessage(), task_id, transaction)
            return LocalSyncDisabledActionTaken.SYNC_NOT_FOUND

        with self.data_model_service.open_transaction(request_id=RequestID()) as teardown_transaction:
            self._send_message(LocalSyncTeardownStartedMessage(), task_id, teardown_transaction)
            # TODO: check with Saeed if this is right
            self._send_message(
                LocalSyncTeardownProgressMessage(
                    next_step=LocalSyncTeardownStep.STOP_FILE_SYNC,
                    sync_branch=unsyncing_info.sync_branch,
                    original_branch=unsyncing_info.original_branch,
                ),
                task_id,
                teardown_transaction,
            )

        is_final_status_paused = self.ensure_session_is_stopped()

        if is_final_status_paused:
            logger.info("Unsyncing from paused task {} and leaving behind state as-is", task_id)
            return LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED

        logger.info("Sync cleaned up for task {}{}", task_id, " after startup error" if is_startup_error else "")
        return LocalSyncDisabledActionTaken.STOPPED_CLEANLY

    def _git_reset_if_safe(self, session: SyncSessionInfo, repo: WritableGitRepo) -> bool:
        status = repo.get_current_status()
        if status.is_in_intermediate_state:
            # NOTE: we shouldn't get here because the session should handle it by pausing,
            # but in case we race, just pretend we paused and leave state as-is
            message = "Unexpected git status {}: unsynced from task {} leaving behind git state without git reset"
            logger.info(message, status, session.task_id)
            return False

        # TODO: Lots of duplicate blocks of this, PLUS IDK if request_id should be tied to original request
        self._teardown_progress_message(session, about_to=LocalSyncTeardownStep.RESTORE_LOCAL_FILES)
        repo.reset_working_directory()
        return True

    def _rewind_repo_state(
        self, task: Task, repo: WritableGitRepo, from_session: SyncSessionInfo
    ) -> UnsyncFromTaskResult:
        """Rewinds the git state unless it seems dangerous - whether we're switching back to original branch or popping a full SculptorStash"""
        task_id = task.object_id
        stash = from_session.stash
        original_branch = from_session.original_branch
        current_stash = build_sculptor_stash_reader(repo).maybe_get_singleton_stash()
        if current_stash is None and stash is not None:
            logger.info("LOCAL_SYNC.rewind of session {}: stash deleted", ExceptionPriority.LOW_PRIORITY, from_session)
            stash = None
        try:
            assert current_stash == stash, f"{current_stash=} doesn't match expected {stash=}"
        except AssertionError as e:
            # This is truly truly unexpected - user (or bug) would have to replace stash during sync session
            msg = (
                "LOCAL_SYNC.rewind unexpected behavior mismatched non-None stashes for sync_session {session}",
                "Will continue to pop existing stash despite possible bug.",
            )
            log_exception(e, " ".join(msg), ExceptionPriority.LOW_PRIORITY, session=from_session)

        # Do this during stashing regardless
        self._teardown_progress_message(from_session, about_to=LocalSyncTeardownStep.RESTORE_ORIGINAL_BRANCH)

        action_taken = LocalSyncDisabledActionTaken.STOPPED_CLEANLY
        if stash is None:
            logger.debug("Restoring original branch: {}", original_branch)
            repo.git_checkout_branch(original_branch)
            return UnsyncFromTaskResult(action_taken=action_taken, stash_from_start_of_operation=None)

        # Handle error in parent
        try:
            logger.debug("Importing and popping sculptor stash for task {} (stash={})", task_id, stash)
            # TODO stash progress message LocalSyncTeardownProgressMessage
            #      maybe should unbundle the stashing api to compose with message, or just batch them
            # self._teardown_progress_message(from_session, about_to=LocalSyncTeardownStep.RESTORE_STASH)
            pop_namespaced_stash_into_source_branch(task.project_id, repo, stash)
            return UnsyncFromTaskResult(action_taken=action_taken, stash_from_start_of_operation=stash)
        except GitStashApplyError as e:
            logger.error("Failed to pop sculptor stash for {}: {}. Leaving behind git state as-is", task_id, e)
            return UnsyncFromTaskResult(
                action_taken=LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED,
                stash_from_start_of_operation=stash,
                dangling_ref_from_unclean_pop=e.source_ref,
            )

    def _prep_task_task_branch_and_repo_opener(
        self, task_id: TaskID, transaction: DataModelTransaction, task: Task | None = None
    ) -> tuple[Task, str, RepoOpener]:
        if task is None:
            task = self.task_service.get_task(task_id=task_id, transaction=transaction)
        assert task is not None, f"Task {task_id} not found"
        current_state = task.current_state
        assert isinstance(current_state, AgentTaskStateV1)
        branch_name = current_state.branch_name
        assert branch_name is not None, f"Impossible: Branch name is None for task {task_id}"
        project = transaction.get_project(task.project_id)
        assert project is not None, f"Impossible: Project {task.project_id} not found"
        return (
            task,
            branch_name,
            lambda: self.git_repo_service.open_local_user_git_repo_for_write(project),
        )

    def cleanup_current_sync(self, transaction: DataModelTransaction) -> None:
        """Clean up current sync (used on shutdown)."""
        current_task_id = self._current_sync_task_id
        if current_task_id is None:
            return
        logger.info("Cleaning up current sync for task {}", current_task_id)
        try:
            self.unsync_from_task(current_task_id, transaction=transaction)
            self.ensure_session_is_stopped()
        except LocalSyncError as e:
            log_exception(
                e, "LOCAL_SYNC: cleanup failed for task {tid}", ExceptionPriority.LOW_PRIORITY, tid=current_task_id
            )

    def _build_new_sync_info(self, task: Task, repo: WritableGitRepo, target_branch: str) -> SyncSessionInfo:
        current_state = task.current_state
        assert isinstance(current_state, AgentTaskStateV1)
        original_branch = repo.get_current_git_branch()
        return SyncSessionInfo(
            task_id=task.object_id,
            project_id=task.project_id,
            sync_name=mutagen_sync_name_for(task_id=task.object_id, project_id=task.project_id),
            sync_branch=target_branch,
            original_branch=original_branch,
            stash=None,
        )

    def _build_update_messenger(
        self, session_info: SyncSessionInfo, concurrency_group: ConcurrencyGroup
    ) -> LocalSyncUpdateMessenger:
        return LocalSyncUpdateMessenger(
            info=session_info,
            task_service=self.task_service,
            data_model_service=self.data_model_service,
            session_level_shutdown_event=concurrency_group.shutdown_event,
        )

    def _ensure_no_active_mutagen_sessions_exist_for_project(self, project_id: ProjectID) -> None:
        existing_sessions = get_all_sculptor_mutagen_sessions_for_projects(
            lambda: (project_id,), self.concurrency_group
        )
        try:
            assert len(existing_sessions) == 0, f"{existing_sessions=} but should be empty when starting a new sync"
        except AssertionError as e:
            message = (
                "LOCAL_SYNC_STATE_MISMATCH in project {project_id}:",
                "existing_sessions={existing_sessions} but should be empty when starting a new sync.",
                "Cleaning up existing sessions.",
            )
            log_exception(e, " ".join(message), project_id=project_id, existing_sessions=existing_sessions)
            for session_name in existing_sessions:
                terminate_mutagen_session(self.concurrency_group, session_name)

    def _get_all_project_ids_in_db(self) -> tuple[ProjectID, ...]:
        with self.data_model_service.open_transaction(RequestID(), is_user_request=False) as transaction:
            return tuple(p.object_id for p in transaction.get_projects())

    def _cleanup_dangling_mutagen_sessions(self) -> None:
        """First, finds all sculptor- prefixed mutagen sessions.

        Then if they exist, query for project ids, and terminate the sessions we know are being managed by this db.
        NOTE: we don't have to worry about sending a stop message, because local sync messages are now ephemeral
        """
        existing_sessions = get_all_sculptor_mutagen_sessions_for_projects(
            self._get_all_project_ids_in_db, self.concurrency_group
        )
        for session_name in existing_sessions:
            logger.info("Cleaning up dangling mutagen session {}", session_name)
            terminate_mutagen_session(self.concurrency_group, session_name)

    def _send_message(
        self,
        message: (
            LocalSyncSetupStartedMessage
            | LocalSyncSetupAndEnabledMessage
            | LocalSyncTeardownStartedMessage
            | LocalSyncTeardownProgressMessage
            | LocalSyncDisabledMessage
        ),
        task_id: TaskID,
        transaction: DataModelTransaction,
    ) -> None:
        self.task_service.create_message(
            message=message,
            task_id=task_id,
            transaction=transaction,
        )
        emit_local_sync_posthog_event_if_tracked(task_id, message)

    def _on_exception_send_message(
        self, transaction: DataModelTransaction, task_id: TaskID, exception: Exception
    ) -> None:
        self.task_service.create_message(
            message=UnexpectedErrorRunnerMessage(error=SerializedException.build(exception), full_output_url=None),
            task_id=task_id,
            transaction=transaction,
        )

    def _teardown_progress_message(self, session: SyncSessionInfo, about_to: LocalSyncTeardownStep) -> None:
        # TODO: IDK if request_id should be tied to original request
        with self.data_model_service.open_transaction(request_id=RequestID()) as progress_transaction:
            self._send_message(
                LocalSyncTeardownProgressMessage(
                    next_step=about_to,
                    sync_branch=session.sync_branch,
                    original_branch=session.original_branch,
                ),
                session.task_id,
                progress_transaction,
            )

    def is_task_synced(self, task_id: TaskID) -> bool:
        return self._current_sync_task_id == task_id


def _carry_forward_info(previous: SyncSessionInfo, new_task: Task, new_sync_branch: str) -> SyncSessionInfo:
    assert previous.project_id == new_task.project_id, "Cannot carry forward stash between different projects"
    return SyncSessionInfo(
        original_branch=previous.original_branch,
        stash=previous.stash,
        project_id=new_task.project_id,
        task_id=new_task.object_id,
        sync_name=mutagen_sync_name_for(new_task.project_id, new_task.object_id),
        sync_branch=new_sync_branch,
        chained_sync_count=previous.chained_sync_count + 1,
    )


def _derive_exception(reraise_and_capture: ExceptionT, from_cause: Exception) -> ExceptionT:
    """Derives a new exception from_cause, carrying forward traceback"""
    try:
        raise reraise_and_capture from from_cause
    except Exception as e:
        assert e is reraise_and_capture, "Derived exception should be the same as the input reraise_and_capture"
    assert reraise_and_capture.__traceback__ is not None, "Derived exception should have a traceback after derivation"
    return reraise_and_capture


def _describe_error(error: Exception) -> str:
    if isinstance(error, MutagenSyncError):
        return "mutagen_termination"
    elif isinstance(error, GitRepoError):
        if hasattr(error, "operation"):
            return f"git_{error.operation}"
        return "git_operation"
    else:
        return "unknown"
