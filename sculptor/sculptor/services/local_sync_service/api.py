import abc
import datetime
from enum import auto
from typing import Any
from typing import ContextManager
from typing import Optional

from loguru import logger
from pydantic import computed_field

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.pydantic_serialization import FrozenModel
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.upper_case_str_enum import UpperCaseStrEnum
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import LocalSyncMessageUnion
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion
from sculptor.interfaces.agents.agent import LocalSyncUpdateMessage
from sculptor.primitives.service import Service
from sculptor.services.data_model_service.data_types import DataModelTransaction
from sculptor.services.git_repo_service.error_types import CommitRef
from sculptor.services.git_repo_service.ref_namespace_stasher import SculptorStash
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import ObserverLifecycle
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import SlightlySaferObserver
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncPathBatchSchedulerStatus


class SyncSessionInfo(MutableModel):
    """Represents an active sync process for a task."""

    task_id: TaskID
    project_id: ProjectID
    sync_name: str
    # TODO migrate to AbsoluteGitTransition
    sync_branch: str
    original_branch: str
    chained_sync_count: int = 1
    stash: SculptorStash | None

    @property
    def is_carried_forward_from_previous_sync(self) -> bool:
        return self.chained_sync_count > 1

    @property
    def is_switching_branches(self) -> bool:
        return self.original_branch != self.sync_branch


class LocalSyncHighLevelStatus(UpperCaseStrEnum):
    """This rolls intermediate, transient, and granular states into the simple high-level status"""

    ACTIVE = auto()
    PAUSED = auto()
    STOPPED = auto()

    @property
    def is_paused(self) -> bool:
        return self == LocalSyncHighLevelStatus.PAUSED


class LocalSyncSessionState(FrozenModel):
    info: SyncSessionInfo
    scheduler_status: LocalSyncPathBatchSchedulerStatus
    observer_lifecycle: ObserverLifecycle
    start_time: datetime.datetime
    stop_time: datetime.datetime | None
    last_sent_message: LocalSyncMessageUnion | None = None

    @property
    def notices(self) -> tuple[LocalSyncNoticeUnion, ...]:
        if isinstance(self.last_sent_message, LocalSyncUpdateMessage):
            return self.last_sent_message.all_notices
        return ()

    @classmethod
    def build_if_sensible(
        cls,
        info: SyncSessionInfo,
        observer: SlightlySaferObserver,
        last_sent_message: LocalSyncMessageUnion | None,
        scheduler_status: LocalSyncPathBatchSchedulerStatus,
    ) -> Optional["LocalSyncSessionState"]:
        start_time = observer.start_time
        if observer.lifecycle == ObserverLifecycle.INITIALIZED or start_time is None:
            logger.debug("surprising: reconciler state requested before observer started")
            return None

        return cls(
            info=info,
            scheduler_status=scheduler_status,
            observer_lifecycle=observer.lifecycle,
            start_time=start_time,
            stop_time=observer.stop_time,
            last_sent_message=last_sent_message,
        )

    @property
    def high_level_status(self) -> LocalSyncHighLevelStatus:
        # NOTE: the frontend handles addition ACTIVE_SYNCING state but
        #       it is derived from LocalSync events rather than this
        #       method. This method is only for the global stopgap state,
        #       which is actually polled by the frontend.
        if self.observer_lifecycle in (ObserverLifecycle.STOPPED, ObserverLifecycle.STOPPING):
            return LocalSyncHighLevelStatus.STOPPED
        if self.scheduler_status == LocalSyncPathBatchSchedulerStatus.STOPPING:
            return LocalSyncHighLevelStatus.STOPPED
        elif self.scheduler_status.is_paused:
            return LocalSyncHighLevelStatus.PAUSED

        assert self.scheduler_status.is_active, f"Impossible: Unexpected reconciler status: {self.scheduler_status}"
        return LocalSyncHighLevelStatus.ACTIVE


class LocalSyncDisabledActionTaken(UpperCaseStrEnum):
    STOPPED_FROM_PAUSED = auto()
    SYNC_NOT_FOUND = auto()
    STOPPED_CLEANLY = auto()


class UnsyncFromTaskResult(SerializableModel):
    action_taken: LocalSyncDisabledActionTaken
    stash_from_start_of_operation: SculptorStash | None

    # if we fail to pop in an unabigiuously clean way, we just let the user know about it
    #
    # NOTE: The result space of `git apply` is under-investigated.
    #       This means surfacing precise copy to the user or having nuanced handling would be premature.
    dangling_ref_from_unclean_pop: CommitRef | None = None

    @computed_field
    @property
    def was_existing_sync_stopped_from_pause(self) -> bool:
        return self.action_taken == LocalSyncDisabledActionTaken.STOPPED_FROM_PAUSED

    def model_post_init(self, context: Any) -> None:
        if self.action_taken == LocalSyncDisabledActionTaken.STOPPED_CLEANLY:
            return
        assert self.dangling_ref_from_unclean_pop is None, f"Shouldn't have popped after {self.action_taken} ({self=})"
        super().model_post_init(context)


class SyncToTaskResult(SerializableModel):
    newly_created_stash: SculptorStash | None


class LocalSyncService(Service, abc.ABC):
    """Manages bidirectional sync between local development environment and task containers"""

    @abc.abstractmethod
    def get_session_state(self) -> LocalSyncSessionState | None: ...

    @abc.abstractmethod
    def sync_to_task(
        self, task_id: TaskID, transaction: DataModelTransaction, task: Task | None = None, is_stashing_ok: bool = True
    ) -> SyncToTaskResult:
        """Start bidirectional working tree + unidirectional git sync for a task."""

    @abc.abstractmethod
    def unsync_from_task(self, task_id: TaskID, transaction: DataModelTransaction) -> UnsyncFromTaskResult:
        """Stop sync and restore original state.

        Args:
            task_id: The task to disable sync for
        """

    @abc.abstractmethod
    def cleanup_current_sync(self, transaction: DataModelTransaction) -> None:
        """Cleanup current sync and restore original state for currently synced task. NOTE: there should only ever be one sync active at a time."""

    @abc.abstractmethod
    def is_task_synced(self, task_id: TaskID) -> bool:
        # TODO(mjr): unify with session_state once old service is deleted
        """Check if a task is currently synced."""

    @abc.abstractmethod
    def maybe_acquire_sync_transition_lock(self) -> ContextManager[bool]:
        """Prevents concurrent local_sync transitions for the context if acquired, delegating handling to caller if not.

        Used when another system's actions could race with the transition logic (ie deleting a stash while a sync has it).
        """

    @abc.abstractmethod
    def maybe_guarantee_no_new_or_active_session(self) -> ContextManager[bool]:
        """Locks transitions if no session is active, otherwise failing immediately.

        for use by external systems whos actions would conflict with sync actions (ie the stash singleton).
        """
