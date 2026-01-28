from enum import Enum

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.errors import ExpectedError
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion


class LocalSyncError(ExpectedError):
    """Base exception for all local sync operations."""


# Local Sync Exceptions


class OtherSyncTransitionInProgressError(LocalSyncError):
    """Exception raised when sync startup fails for a task."""

    def __init__(self, action: str, new_task_id: TaskID) -> None:
        self.task_id = new_task_id
        self.action = action
        message = f"Cannot {action} {new_task_id}: Another sync state transition is in progress"
        super().__init__(message)


class NewNoticesInSyncHandlingError(LocalSyncError):
    def __init__(self, notices: tuple[LocalSyncNoticeUnion, ...]) -> None:
        super().__init__(", AND ".join([n.reason for n in notices]))
        self.notices = notices


class SyncStartupError(LocalSyncError):
    """Exception raised when sync startup fails for a task."""

    def __init__(
        self,
        message: str,
        task_id: str | None = None,
        task_branch: str | None = None,
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.task_branch = task_branch

    def __str__(self) -> str:
        details = [super().__str__()]
        if self.task_id:
            details.append(f"Task ID: {self.task_id}")
        if self.task_branch:
            details.append(f"Task branch: {self.task_branch}")
        return "\n".join(details)


class ExpectedStartupBlocker(Enum):
    # agent side got into bad state
    AGENT_BRANCH_MISSING = "AGENT_BRANCH_MISSING"
    AGENT_REPO_WRONG_BRANCH = "AGENT_REPO_WRONG_BRANCH"

    # possible with stashing is disabled
    USER_GIT_STATE_DIRTY = "USER_GIT_STATE_DIRTY"

    # any git state synced from user->agent would be masked by the filesystem overwrite
    USER_BRANCH_AHEAD_OF_AGENT = "USER_BRANCH_AHEAD_OF_AGENT"

    BRANCHES_DIVERGED = "BRANCHES_DIVERGED"

    # caused by intermediate git states
    USER_GIT_STATE_UNSTASHABLE = "USER_GIT_STATE_UNSTASHABLE"

    # caused by existing stash
    USER_GIT_STATE_STASHING_PREVENTED = "USER_GIT_STATE_STASHING_PREVENTED"


class ExpectedSyncStartupError(SyncStartupError):
    def __init__(
        self,
        message: str,
        blockers: list[ExpectedStartupBlocker],
        task_id: str | None = None,
        task_branch: str | None = None,
    ) -> None:
        super().__init__(message, task_id, task_branch)
        self.message = message
        self.blockers = blockers

    def __str__(self) -> str:
        details = [super().__str__()]
        details.append(f"Expected blockers: {self.blockers}")
        return "\n".join(details)


class SyncCleanupError(LocalSyncError):
    """Exception raised when sync cleanup fails."""

    def __init__(
        self,
        message: str,
        task_id: TaskID | None = None,
        cleanup_step: str | None = None,
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.cleanup_step = cleanup_step

    def __str__(self) -> str:
        details = [super().__str__()]
        if self.task_id:
            details.append(f"Task ID: {self.task_id}")
        if self.cleanup_step:
            details.append(f"Cleanup step: {self.cleanup_step}")
        return "\n".join(details)


class MutagenSyncError(LocalSyncError):
    """Exception raised when mutagen operations fail during sync."""

    def __init__(
        self,
        message: str,
        operation: str,
        session_name: str | None = None,
        sync_mode: str | None = None,
        source_path: str | None = None,
        dest_path: str | None = None,
        exit_code: int | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.session_name = session_name
        self.sync_mode = sync_mode
        self.source_path = source_path
        self.dest_path = dest_path
        self.exit_code = exit_code
        self.stderr = stderr

    def __str__(self) -> str:
        details = [super().__str__()]
        details.append(f"Operation: {self.operation}")
        if self.session_name:
            details.append(f"Session: {self.session_name}")
        if self.sync_mode:
            details.append(f"Sync mode: {self.sync_mode}")
        if self.source_path:
            details.append(f"Source: {self.source_path}")
        if self.dest_path:
            details.append(f"Destination: {self.dest_path}")
        if self.exit_code is not None:
            details.append(f"Exit code: {self.exit_code}")
        if self.stderr:
            details.append(f"Stderr: {self.stderr}")
        return "\n".join(details)
