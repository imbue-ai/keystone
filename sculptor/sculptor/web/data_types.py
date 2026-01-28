from enum import auto
from pathlib import Path
from typing import Annotated
from typing import Any

from pydantic import EmailStr
from pydantic import Field
from pydantic import Tag

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.pydantic_serialization import build_discriminator
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.upper_case_str_enum import UpperCaseStrEnum
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.agents.agent import MessageTypes
from sculptor.interfaces.agents.artifacts import DiffArtifact
from sculptor.interfaces.agents.artifacts import LogsArtifact
from sculptor.interfaces.agents.artifacts import SuggestionsArtifact
from sculptor.interfaces.agents.artifacts import TodoListArtifact
from sculptor.interfaces.agents.artifacts import UsageArtifact
from sculptor.interfaces.environments.base import ProviderTag
from sculptor.interfaces.environments.provider_status import ProviderStatusTypes
from sculptor.primitives.ids import UserReference
from sculptor.services.data_model_service.api import CompletedTransaction
from sculptor.services.local_sync_service.api import UnsyncFromTaskResult
from sculptor.services.task_service.api import TaskMessageContainer
from sculptor.web.derived import LocalRepoInfo
from sculptor.web.derived import TaskInterface


class RequestModel(SerializableModel):
    pass


class StartTaskRequest(RequestModel):
    prompt: str
    interface: str = TaskInterface.TERMINAL.value
    source_branch: str | None = None
    model: LLMModel
    is_including_uncommitted_changes: bool = False
    files: list[str] = Field(default_factory=list)


class ForkTaskRequest(RequestModel):
    chat_message_id: AgentMessageID
    prompt: str
    model: LLMModel
    files: list[str] = Field(default_factory=list)


class FixTaskRequest(RequestModel):
    description: str


class SendMessageRequest(RequestModel):
    message: str
    model: LLMModel
    files: list[str] = Field(default_factory=list)


class MessageRequest(RequestModel):
    message: MessageTypes
    is_awaited: bool = False
    timeout_seconds: int | None = None


class FeedbackRequest(RequestModel):
    feedback_type: str
    comment: str | None = None
    issue_type: str | None = None


class CompactTaskMessageRequest(RequestModel):
    pass


class SystemPromptRequest(RequestModel):
    system_prompt: str


class DefaultSystemPromptRequest(RequestModel):
    default_system_prompt: str


class ArchiveTaskRequest(RequestModel):
    is_archived: bool


ArtifactDataResponse = Annotated[
    Annotated[TodoListArtifact, Tag("TodoListArtifact")]
    | Annotated[LogsArtifact, Tag("LogsArtifact")]
    | Annotated[DiffArtifact, Tag("DiffArtifact")]
    | Annotated[SuggestionsArtifact, Tag("SuggestionsArtifact")]
    | Annotated[UsageArtifact, Tag("UsageArtifact")],
    build_discriminator(),
]


class ReadFileRequest(RequestModel):
    file_path: str


class MergeActionNoticeKind(UpperCaseStrEnum):
    SUCCESS = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()


class MergeActionNotice(SerializableModel):
    message: str
    kind: MergeActionNoticeKind = MergeActionNoticeKind.INFO
    details: str | None = None


class TransferRepoDecisionOption(SerializableModel):
    option: str
    # for visual indication of the option
    is_destructive: bool = False
    is_default: bool = False


class TransferRepoUserChoice(SerializableModel):
    decision_id: str
    choice: str

    # optionally for decisions that involve a commit being created
    # this is yucky and another case in point of needing to couple
    # the frontend and backend types around these dialogs.
    commit_message: str | None = None


# TODO: consider if these can be somewhat strongly typed
#       or available to frontend *before* the request
class TransferRepoDecision(SerializableModel):
    id: str

    title: str
    message: str
    detailed_context_title: str | None = None
    detailed_context: str | None = None

    # NOTE: Maybe this is a part of a decision option instead? but then frontend would have to reconcile them
    #       and understand which options actually cares, while it actually pertains to the dialog alert show
    #       to the user.
    is_commit_message_required: bool = (
        False  # if True, show commit box in the decision dialog and pass its contents back in the decision
    )

    options: tuple[TransferRepoDecisionOption, ...]

    def resolve_user_choice(self, user_choices: list[TransferRepoUserChoice] | None) -> TransferRepoUserChoice | None:
        if user_choices is None:
            return None
        for choice in user_choices:
            if choice.decision_id == self.id:
                return choice
        return None


class TransferRepoAssumptions(SerializableModel):
    local_branch: str


class TransferRepoBaseRequest(RequestModel):
    target_local_branch: str

    assumptions: TransferRepoAssumptions
    user_choices: list[TransferRepoUserChoice] | None = None

    @property
    def user_choice_by_decision_id(self) -> dict[str, str] | None:
        if self.user_choices:
            return {choice.decision_id: choice.choice for choice in self.user_choices}
        return None


class TransferRepoBaseResponse(SerializableModel):
    success: bool

    notices: tuple[MergeActionNotice, ...] | None = None
    missing_decisions: list[TransferRepoDecision] | None = None


class TransferFromTaskToLocalRequest(TransferRepoBaseRequest):
    pass


class TransferFromTaskToLocalResponse(TransferRepoBaseResponse):
    # TODO: a bit of a stop-gap to get better posthog tracking
    reached_operation_or_failure_label: str | None = None


class TransferFromLocalToTaskRequest(TransferRepoBaseRequest):
    pass


class TransferFromLocalToTaskResponse(TransferRepoBaseResponse):
    pass


class GitCommitAndPushRequest(RequestModel):
    commit_message: str


# TODO: LocalSync API methods and data types have inconsistent naming
#       based on which layers of abstraction need to add i/o
class EnableLocalSyncRequest(RequestModel):
    is_stashing_ok: bool


class DisableLocalSyncResponse(RequestModel):
    result: UnsyncFromTaskResult
    resulting_repo_info: LocalRepoInfo | None


class RestoreSyncStashRequest(RequestModel):
    absolute_stash_ref: str


class DeleteSyncStashRequest(RequestModel):
    absolute_stash_ref: str


class RepoInfo(SerializableModel):
    """Repository information"""

    repo_path: Path
    current_branch: str
    recent_branches: list[str]
    project_id: ProjectID
    num_uncommitted_changes: int


class CurrentBranchInfo(SerializableModel):
    """Lightweight repository information with just current branch"""

    current_branch: str
    num_uncommitted_changes: int


class UserInfo(SerializableModel):
    """Current user information"""

    user_reference: UserReference | None
    email: EmailStr | None


class ProviderStatusInfo(SerializableModel):
    """Status information for a single provider"""

    provider: ProviderTag
    status: ProviderStatusTypes


class InitializeGitRepoRequest(RequestModel):
    """Request to initialize a directory as a git repository"""

    project_path: str


class DownloadDockerTarRequest(RequestModel):
    """Request to download a .tar-ed docker image into our cache."""

    url: str


class CreateInitialCommitRequest(RequestModel):
    """Request to create an initial commit in a new git repository"""

    project_path: str


class ProjectInitializationRequest(RequestModel):
    """Request to initialize a new project"""

    project_path: str


class ConfigStatusResponse(SerializableModel):
    """Response for config status check"""

    has_email: bool
    has_api_key: bool
    has_privacy_consent: bool
    has_telemetry_level: bool


class HealthCheckResponse(SerializableModel):
    version: str
    free_disk_gb: float
    min_free_disk_gb: float
    free_disk_gb_warn_limit: float
    sculptor_container_ram_used_gb: float
    docker_ram_limit_gb: float | None


class EmailConfigRequest(RequestModel):
    """Request to save user email configuration"""

    user_email: EmailStr
    full_name: str | None = None
    did_opt_in_to_marketing: bool = False


class PrivacyConfigRequest(RequestModel):
    """Request to save privacy/telemetry settings"""

    telemetry_level: int  # 2-4
    is_repo_backup_enabled: bool = False


class UpdateUserConfigRequest(RequestModel):
    user_config: UserConfig


class DependenciesStatus(SerializableModel):
    """Status of required dependencies"""

    docker_installed: bool
    docker_running: bool
    mutagen_installed: bool
    git_installed: bool


# Generic system dependency models for unified frontend rendering


class SystemRequirementStatus(UpperCaseStrEnum):
    """Status of a system requirement check."""

    PASS = auto()
    FAIL = auto()
    UNKNOWN = auto()


class SystemRequirement(SerializableModel):
    """A single system requirement with validation result.

    This model represents a requirement that can be rendered generically
    by the frontend, such as Docker version >= 27, memory >= 8GB, etc.
    """

    # Unique identifier for this requirement (e.g., "docker_version", "memory_limit")
    requirement_id: str

    # Human-readable description of what's required (e.g., ">=27.0.0", "Docker VMM", "Disabled")
    requirement_description: str

    # Validation status
    status: SystemRequirementStatus

    # The actual detected value (as a human-readable string), if available
    actual_value: str | None = None


class SystemDependencyInfo(SerializableModel):
    """Information about a system dependency (Docker, Git, rsync, etc.).

    This provides a unified structure for frontend to render different
    dependency checks in a consistent way.
    """

    # Name of the dependency (e.g., "Docker", "Git", "rsync")
    name: str

    # Overall status (if all requirements pass, this is PASS; if any fail, this is FAIL)
    overall_status: SystemRequirementStatus

    # List of specific requirements for this dependency
    requirements: list[SystemRequirement]

    # Any error that occurred while checking this dependency
    error: str | None = None


TaskUpdateTypes = Message | CompletedTransaction | dict[str, Any]
UserUpdateSourceTypes = LocalRepoInfo | CompletedTransaction | SculptorSettings
StreamingUpdateSourceTypes = TaskMessageContainer | TaskUpdateTypes | UserUpdateSourceTypes
