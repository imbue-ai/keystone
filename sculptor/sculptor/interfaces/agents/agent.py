"""
An Agent simply *is* a list of `Message`s.

The meaning of each of the message is defined below.
"""

from __future__ import annotations

import abc
import datetime
from enum import StrEnum
from typing import Annotated
from typing import Any
from typing import Callable
from typing import Mapping

from pydantic import AnyUrl
from pydantic import Field
from pydantic import Tag

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.ids import AssistantMessageID
from imbue_core.imbue_cli.action import ActionOutputUnion
from imbue_core.progress_tracking.progress_models import RootProgress
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.pydantic_serialization import build_discriminator
from imbue_core.sculptor.state.chat_state import ContentBlockTypes
from imbue_core.sculptor.state.claude_state import ParsedAssistantResponse
from imbue_core.sculptor.state.claude_state import ParsedCompactionSummaryResponse
from imbue_core.sculptor.state.claude_state import ParsedEndResponse
from imbue_core.sculptor.state.claude_state import ParsedInitResponse
from imbue_core.sculptor.state.claude_state import ParsedToolResultResponse
from imbue_core.sculptor.state.messages import AgentMessageSource
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.state.messages import PersistentAgentMessage
from imbue_core.sculptor.state.messages import PersistentMessage
from imbue_core.sculptor.state.messages import PersistentUserMessage
from imbue_core.sculptor.state.messages import ResponseBlockAgentMessage
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_utils import never_log
from imbue_core.sculptor.telemetry_utils import with_consent
from imbue_core.sculptor.telemetry_utils import without_consent
from imbue_core.secrets_utils import Secret
from imbue_core.serialization import SerializedException
from imbue_core.time_utils import get_current_time
from sculptor.interfaces.agents.artifacts import FileAgentArtifact
from sculptor.interfaces.agents.checks import Check
from sculptor.interfaces.agents.checks import CheckFinishedReason
from sculptor.interfaces.agents.tasks import RunID
from sculptor.interfaces.agents.tasks import TaskState
from sculptor.interfaces.environments.base import ImageTypes
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.environments.modal_environment import ModalEnvironment

ParsedAgentResponseType = (
    ParsedInitResponse
    | ParsedAssistantResponse
    | ParsedToolResultResponse
    | ParsedEndResponse
    | ParsedCompactionSummaryResponse
)


class Agent(MutableModel, abc.ABC):
    @abc.abstractmethod
    def pop_messages(self) -> list[MessageTypes]: ...

    @abc.abstractmethod
    def push_message(self, message: Message) -> None: ...

    @abc.abstractmethod
    def terminate(self, force_kill_seconds: float = 5.0) -> None: ...

    @abc.abstractmethod
    def poll(self) -> int | None: ...

    @abc.abstractmethod
    def wait(self, timeout: float) -> int:
        """
        Wait for the agent to finish running and return the exit code.

        Raises:
            AgentCrashed: If some part of the agent code failed with an unexpected exception.
            WaitTimeoutAgentError: If the agent did not finish within the specified timeout.
        """

    @abc.abstractmethod
    def start(
        self,
        secrets: Mapping[str, str | Secret],
        get_credentials: Callable[[], Credentials],
    ) -> None: ...


EnvironmentTypes = Annotated[
    Annotated[DockerEnvironment, Tag("DockerEnvironment")]
    | Annotated[LocalEnvironment, Tag("LocalEnvironment")]
    | Annotated[ModalEnvironment, Tag("ModalEnvironment")],
    build_discriminator(),
]


class EphemeralMessage(Message):
    @property
    def is_ephemeral(self) -> bool:
        return True


class EphemeralUserMessage(EphemeralMessage, PosthogEventPayload):
    """
    One of two base classes for messages sent from the user.
    Ephemeral user messages are not saved to the database.
    Ephemeral user messages are sent immediately to the agent and are not queued in the task runner.
    """

    # Override inherited fields with consent annotations
    # TODO (moishe): if other classes that derive from Message also start getting logged,
    # change the base Message class to derive from PosthogEventPayload. For now, doing
    # that is overkill and requires lots of annotations of irrelevant classes.
    #
    # TODO (mjr): We should really have `PersistentHoggableMessage` and `EphemeralHoggableMessage` or something
    object_type: str = without_consent(description="Type discriminator for user messages")
    message_id: AgentMessageID = without_consent(
        default_factory=AgentMessageID,
        description="Unique identifier for the user message",
    )
    source: AgentMessageSource = without_consent(default=AgentMessageSource.USER)
    approximate_creation_time: datetime.datetime = without_consent(
        default_factory=get_current_time,
        description="Approximate UTC timestamp when user message was created",
    )


UserMessage = EphemeralUserMessage | PersistentUserMessage


class CompactTaskUserMessage(PersistentUserMessage):
    object_type: str = without_consent(default="CompactTaskUserMessage")


class CommandInputUserMessage(PersistentUserMessage):
    object_type: str = without_consent(default="CommandInputUserMessage")
    text: str = with_consent(ConsentLevel.LLM_LOGS, description="User input text content")
    is_included_in_context: bool = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Whether this command should be included in conversation context"
    )
    is_checkpoint: bool = without_consent(default=False, description="Whether this command represents a checkpoint")
    run_with_sudo_privileges: bool = with_consent(ConsentLevel.PRODUCT_ANALYTICS)
    is_automated_command: bool = without_consent(
        default=False,
        description="Whether this command is an automated command executed by sculptor instead of the user",
    )


class MessageFeedbackUserMessage(PersistentUserMessage):
    object_type: str = without_consent(default="MessageFeedbackUserMessage")
    feedback_message_id: AgentMessageID = without_consent(description="ID of the message being given feedback on")
    feedback_type: str = without_consent(
        description="Type of feedback (e.g., 'positive', 'negative')",
    )
    comment: str | None = without_consent(default=None, description="Optional comment from user about the feedback")
    issue_type: str | None = without_consent(default=None, description="Optional categorization of the issue")


class SetUserConfigurationDataUserMessage(EphemeralUserMessage):
    object_type: str = without_consent(default="SetUserConfigurationDataUserMessage")
    credentials: Credentials = never_log(default=None)


class StopAgentUserMessage(EphemeralUserMessage):
    object_type: str = without_consent(default="StopAgentUserMessage")


class InterruptProcessUserMessage(EphemeralUserMessage):
    object_type: str = without_consent(default="InterruptProcessUserMessage")


class GitCommitAndPushUserMessage(EphemeralUserMessage):
    object_type: str = without_consent(default="GitCommitAndPushUserMessage")
    commit_message: str = with_consent(ConsentLevel.LLM_LOGS, description="Commit message for the git commit")
    is_pushing: bool = without_consent(default=False)


class GitPullUserMessage(EphemeralUserMessage):
    object_type: str = without_consent(default="GitPullUserMessage")


class RemoveQueuedMessageUserMessage(EphemeralUserMessage):
    object_type: str = without_consent(default="RemoveQueuedMessageUserMessage")
    target_message_id: AgentMessageID = without_consent(description="ID of the message to be removed from the queue")


class CheckControlUserMessage(EphemeralUserMessage, abc.ABC):
    check_name: str = with_consent(ConsentLevel.PRODUCT_ANALYTICS, description="Which check is being affected")
    user_message_id: AgentMessageID = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Which user message this is for"
    )


class StopCheckUserMessage(CheckControlUserMessage):
    object_type: str = without_consent(default="StopCheckUserMessage")
    run_id: RunID = with_consent(ConsentLevel.PRODUCT_ANALYTICS, description="Which run is being affected")


class RestartCheckUserMessage(CheckControlUserMessage):
    object_type: str = without_consent(default="RestartCheckUserMessage")


PersistentUserMessageUnion = (
    Annotated[ChatInputUserMessage, Tag("ChatInputUserMessage")]
    | Annotated[CommandInputUserMessage, Tag("CommandInputUserMessage")]
    | Annotated[CompactTaskUserMessage, Tag("CompactTaskUserMessage")]
    | Annotated[MessageFeedbackUserMessage, Tag("MessageFeedbackUserMessage")]
)
EphemeralUserMessageUnion = (
    Annotated[InterruptProcessUserMessage, Tag("InterruptProcessUserMessage")]
    | Annotated[RemoveQueuedMessageUserMessage, Tag("RemoveQueuedMessageUserMessage")]
    | Annotated[GitCommitAndPushUserMessage, Tag("GitCommitAndPushUserMessage")]
    | Annotated[GitPullUserMessage, Tag("GitPullUserMessage")]
    | Annotated[StopCheckUserMessage, Tag("StopCheckUserMessage")]
    | Annotated[RestartCheckUserMessage, Tag("RestartCheckUserMessage")]
    | Annotated[StopAgentUserMessage, Tag("StopAgentUserMessage")]
    | Annotated[SetUserConfigurationDataUserMessage, Tag("SetUserConfigurationDataUserMessage")]
)
UserMessageUnion = PersistentUserMessageUnion | EphemeralUserMessageUnion


class PersistentRunnerMessage(PersistentMessage):
    """Base class for messages sent from the runner."""

    source: AgentMessageSource = AgentMessageSource.RUNNER


class EphemeralRunnerMessage(EphemeralMessage):
    """Base class for messages sent from the runner."""

    source: AgentMessageSource = AgentMessageSource.RUNNER


RunnerMessage = PersistentRunnerMessage | EphemeralRunnerMessage


class EnvironmentCreatedRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "EnvironmentCreatedRunnerMessage"
    environment: EnvironmentTypes


class EnvironmentStoppedRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "EnvironmentStoppedRunnerMessage"


class EnvironmentRestartedRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "EnvironmentRestartedRunnerMessage"
    error: SerializedException | None
    message: str


class KilledAgentRunnerMessage(PersistentRunnerMessage):
    object_type: str = "KilledAgentRunnerMessage"
    full_output_url: AnyUrl | None


class ErrorMessage(SerializableModel):
    pass
    # TODO: remove the `error` field from the subclasses and enable it here.
    # this will require a schema migration
    # error: SerializedException


class AgentCrashedRunnerMessage(PersistentRunnerMessage, ErrorMessage):
    """
    Note that (like EnvironmentCrashedRunnerMessage and UnexpectedErrorRunnerMessage),
    this can happen before *or after* the agent has finished processing a given message.
    """

    object_type: str = "AgentCrashedRunnerMessage"
    exit_code: int | None
    full_output_url: AnyUrl | None
    error: SerializedException


class EnvironmentCrashedRunnerMessage(PersistentRunnerMessage, ErrorMessage):
    object_type: str = "EnvironmentCrashedRunnerMessage"
    error: SerializedException
    full_output_url: AnyUrl | None


class UnexpectedErrorRunnerMessage(PersistentRunnerMessage, ErrorMessage):
    object_type: str = "UnexpectedErrorRunnerMessage"
    error: SerializedException
    full_output_url: AnyUrl | None


class TaskStatusRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "TaskStatusRunnerMessage"
    outcome: TaskState


class StartedAgentSnapshotRunnerMessage(PersistentRunnerMessage):
    object_type: str = "StartedAgentSnapshotRunnerMessage"
    for_user_message_id: AgentMessageID


class AgentSnapshotRunnerMessage(PersistentRunnerMessage):
    object_type: str = "AgentSnapshotRunnerMessage"
    image: ImageTypes | None
    for_user_message_id: AgentMessageID | None
    is_settled: bool = True
    error: SerializedException | None = None


class AgentSnapshotFailureRunnerMessage(PersistentRunnerMessage):
    object_type: str = "AgentSnapshotFailureRunnerMessage"
    for_user_message_id: AgentMessageID | None
    is_settled: bool = True
    failure_reason: SerializedException | None


class ResumeAgentResponseRunnerMessage(PersistentRunnerMessage):
    object_type: str = "ResumeAgentResponseRunnerMessage"
    for_user_message_id: AgentMessageID
    error: SerializedException | None = None
    model_name: LLMModel = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, default=None, description="Selected LLM model for the chat request"
    )


class SculptorSystemEphemeralMessage(EphemeralMessage, PosthogEventPayload, abc.ABC):
    # TODO (mjr): We should really have `PersistentHoggableMessage` and `EphemeralHoggableMessage` or something
    object_type: str = without_consent(description="Type discriminator for sculptor system messages")
    message_id: AgentMessageID = without_consent(
        default_factory=AgentMessageID,
        description="Unique identifier for the sculptor system message",
    )
    source: AgentMessageSource = without_consent(default=AgentMessageSource.SCULPTOR_SYSTEM)
    approximate_creation_time: datetime.datetime = without_consent(
        default_factory=get_current_time,
        description="Approximate UTC timestamp when sculptor system message was created",
    )


class SculptorSystemPersistentMessage(PersistentMessage, PosthogEventPayload, abc.ABC):
    object_type: str = without_consent(description="Type discriminator for sculptor system messages")
    message_id: AgentMessageID = without_consent(
        default_factory=AgentMessageID,
        description="Unique identifier for the sculptor system persistent message",
    )
    source: AgentMessageSource = without_consent(default=AgentMessageSource.SCULPTOR_SYSTEM)
    approximate_creation_time: datetime.datetime = without_consent(
        default_factory=get_current_time,
        description="Approximate UTC timestamp when sculptor system message was created",
    )


class ForkAgentSystemMessage(SculptorSystemPersistentMessage):
    object_type: str = without_consent(default="ForkAgentSystemMessage")
    parent_task_id: TaskID = without_consent(description="The task ID of the parent task")
    child_task_id: TaskID = without_consent(description="The task ID of the child task")
    fork_point_chat_message_id: AgentMessageID = without_consent(description="The fork point chat message ID")


class LocalSyncNotice(SerializableModel, abc.ABC):
    source_tag: str
    reason: str

    def describe(self) -> str:
        subtype = self.__class__.__name__
        return f"{subtype} from {self.source_tag}: {self.reason}"

    @property
    def priority_for_ordering(self) -> int:
        raise NotImplementedError


class LocalSyncNoticeOfWarning(LocalSyncNotice):
    object_type: str = without_consent(default="LocalSyncNoticeOfWarning")

    @property
    def priority_for_ordering(self) -> int:
        return 1


class LocalSyncNoticeOfPause(LocalSyncNotice):
    object_type: str = without_consent(default="LocalSyncNoticeOfPause")

    @property
    def priority_for_ordering(self) -> int:
        return 0


LocalSyncNonPausingNoticeUnion = Annotated[LocalSyncNoticeOfWarning, Tag("LocalSyncNoticeOfWarning")]
LocalSyncNoticeUnion = LocalSyncNonPausingNoticeUnion | LocalSyncNoticeOfPause


class LocalSyncMessage(SculptorSystemEphemeralMessage, abc.ABC):
    pass


class LocalSyncSetupStartedMessage(LocalSyncMessage):
    object_type: str = without_consent(default="LocalSyncSetupStartedMessage")
    sync_branch: str = without_consent(description="branch being synced to")
    original_branch: str = without_consent(description="original branch before sync")


class LocalSyncSetupStep(StrEnum):
    # DISABLING_PRIOR_SYNC = "DISABLING_PRIOR_SYNC"
    VALIDATE_GIT_STATE_SAFETY = "VALIDATE_GIT_STATE_SAFETY"
    MIRROR_AGENT_INTO_LOCAL_REPO = "MIRROR_AGENT_INTO_LOCAL_REPO"
    BEGIN_TWO_WAY_CONTROLLED_SYNC = "BEGIN_TWO_WAY_CONTROLLED_SYNC"


class LocalSyncTeardownStep(StrEnum):
    STOP_FILE_SYNC = "STOP_FILE_SYNC"
    RESTORE_LOCAL_FILES = "RESTORE_LOCAL_FILES"
    RESTORE_ORIGINAL_BRANCH = "RESTORE_ORIGINAL_BRANCH"


class LocalSyncSetupProgressMessage(LocalSyncMessage):
    next_step: LocalSyncSetupStep = without_consent(description="next step in setup process")
    sync_branch: str = without_consent(description="branch being synced to")
    original_branch: str = without_consent(description="original branch before sync")
    object_type: str = without_consent(default="LocalSyncSetupProgressMessage")


class LocalSyncSetupAndEnabledMessage(LocalSyncMessage):
    object_type: str = without_consent(default="LocalSyncSetupAndEnabledMessage")


class LocalSyncUpdateMessage(LocalSyncMessage, abc.ABC):
    event_description: str = with_consent(
        level=ConsentLevel.PRODUCT_ANALYTICS,
        description="description of the event (ie summary of files that triggered sync)",
    )
    nonpause_notices: tuple[LocalSyncNonPausingNoticeUnion, ...] = with_consent(
        default=tuple(),
        level=ConsentLevel.PRODUCT_ANALYTICS,
        description="non-pausing notices, ie large file ignored warnings (currently unimplemented)",
    )

    @property
    def all_notices(self) -> tuple[LocalSyncNoticeUnion, ...]:
        return self.nonpause_notices


class LocalSyncUpdatePendingMessage(LocalSyncUpdateMessage):
    object_type: str = without_consent(default="LocalSyncUpdatePendingMessage")


class LocalSyncUpdateCompletedMessage(LocalSyncUpdateMessage):
    object_type: str = without_consent(default="LocalSyncUpdateCompletedMessage")

    # whether this is the first batch completion after a pause
    is_resumption: bool = without_consent(default=False)


class LocalSyncUpdatePausedMessage(LocalSyncUpdateMessage):
    """Local Sync update failed and is paused instead of completed"""

    pause_notices: tuple[LocalSyncNoticeOfPause, ...] = with_consent(
        default=tuple(),
        level=ConsentLevel.PRODUCT_ANALYTICS,
        description="notices that caused a pause state",
    )

    object_type: str = without_consent(default="LocalSyncUpdatePausedMessage")

    @property
    def all_notices(self) -> tuple[LocalSyncNoticeUnion, ...]:
        return self.pause_notices + self.nonpause_notices

    def model_post_init(self, __context: Any) -> None:
        assert len(self.pause_notices) > 0, "should not construct pause without pause issue"
        return super().model_post_init(__context)


class LocalSyncTeardownStartedMessage(LocalSyncMessage):
    object_type: str = without_consent(default="LocalSyncTeardownStartedMessage")


class LocalSyncTeardownProgressMessage(LocalSyncMessage):
    next_step: LocalSyncTeardownStep = without_consent(description="next step in teardown process")
    sync_branch: str = without_consent(description="branch that was synced")
    original_branch: str = without_consent(description="original branch to restore")
    object_type: str = without_consent(default="LocalSyncTeardownProgressMessage")


class LocalSyncDisabledMessage(LocalSyncMessage):
    object_type: str = without_consent(default="LocalSyncDisabledMessage")


LocalSyncUpdateMessageUnion = (
    Annotated[LocalSyncUpdatePendingMessage, Tag("LocalSyncUpdatePendingMessage")]
    | Annotated[LocalSyncUpdateCompletedMessage, Tag("LocalSyncUpdateCompletedMessage")]
    | Annotated[LocalSyncUpdatePausedMessage, Tag("LocalSyncUpdatePausedMessage")]
)
LocalSyncMessageUnion = (
    Annotated[LocalSyncSetupStartedMessage, Tag("LocalSyncSetupStartedMessage")]
    | Annotated[LocalSyncSetupProgressMessage, Tag("LocalSyncSetupProgressMessage")]
    | Annotated[LocalSyncSetupAndEnabledMessage, Tag("LocalSyncSetupAndEnabledMessage")]
    | LocalSyncUpdateMessageUnion
    | Annotated[LocalSyncTeardownStartedMessage, Tag("LocalSyncTeardownStartedMessage")]
    | Annotated[LocalSyncTeardownProgressMessage, Tag("LocalSyncTeardownProgressMessage")]
    | Annotated[LocalSyncDisabledMessage, Tag("LocalSyncDisabledMessage")]
)


class ManualSyncMessage(SculptorSystemEphemeralMessage, abc.ABC):
    pass


class ManualSyncMergeIntoUserAttemptedMessage(ManualSyncMessage):
    object_type: str = without_consent(default="ManualSyncMergeIntoUserAttemptedMessage")
    reached_operation_label: str | None = without_consent()
    reached_operation_failure_label: str | None = without_consent()
    reached_decision_label: str | None = without_consent()
    selection_by_decision_label: dict[str, str] | None = without_consent()
    target_local_branch: str = without_consent()
    local_branch: str = without_consent()


class ManualSyncMergeIntoAgentNoticeLabel(StrEnum):
    AGENT_UNCOMMITTED_CHANGES = "AGENT_UNCOMMITTED_CHANGES"
    LOCAL_UNCOMMITTED_CHANGES = "LOCAL_UNCOMMITTED_CHANGES"
    LOCAL_BRANCH_NOT_FOUND = "LOCAL_BRANCH_NOT_FOUND"
    PUSH_TO_AGENT_SUCCEEDED = "PUSH_TO_AGENT_SUCCEEDED"
    PUSH_TO_AGENT_ERROR = "PUSH_TO_AGENT_ERROR"
    MERGED_INTO_AGENT_IN_CONFLICT = "MERGED_INTO_AGENT_IN_CONFLICT"
    MERGE_INTO_AGENT_ERROR = "MERGE_INTO_AGENT_ERROR"
    # This is a point in the state graph we aren't sure can be reached: no error, no merge result, but no conflict either
    MERGE_INTO_AGENT_INCOMPLETE_ODD_EDGECASE = "MERGE_INTO_AGENT_INCOMPLETE_ODD_EDGECASE"
    NO_MERGE_NEEDED = "NO_MERGE_NEEDED"
    MERGE_COMPLETED_CLEANLY = "MERGE_COMPLETED_CLEANLY"


class ManualSyncMergeIntoAgentAttemptedMessage(ManualSyncMessage):
    object_type: str = without_consent(default="ManualSyncMergeIntoAgentAttemptedMessage")

    is_attempt_unambiguously_successful: bool = without_consent()
    is_merge_in_progress: bool = without_consent()
    labels: list[ManualSyncMergeIntoAgentNoticeLabel] = without_consent()
    source_local_branch: str = without_consent()
    local_branch: str = without_consent()


ManualSyncMessageUnion = (
    Annotated[ManualSyncMergeIntoUserAttemptedMessage, Tag("ManualSyncMergeIntoUserAttemptedMessage")]
    | Annotated[ManualSyncMergeIntoAgentAttemptedMessage, Tag("ManualSyncMergeIntoAgentAttemptedMessage")]
)
PersistentSystemMessageUnion = Annotated[ForkAgentSystemMessage, Tag("ForkAgentSystemMessage")]
SystemMessageUnion = LocalSyncMessageUnion | ManualSyncMessageUnion | PersistentSystemMessageUnion


class WarningMessage(Message):
    error: SerializedException | None
    message: str


class WarningRunnerMessage(EphemeralRunnerMessage, WarningMessage):
    object_type: str = "WarningRunnerMessage"


class CheckLaunchedRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "CheckLaunchedRunnerMessage"
    user_message_id: AgentMessageID
    check: Check
    run_id: RunID
    # this can be None for local checks when no snapshot is taken
    snapshot: ImageTypes | None


class CheckFinishedRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "CheckFinishedRunnerMessage"
    user_message_id: AgentMessageID
    check: Check
    run_id: RunID
    exit_code: int | None
    finished_reason: CheckFinishedReason
    # if non-empty, this check wasn't even able to be properly loaded, and this is the reason why
    archival_reason: str


class CheckOutputRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "CheckOutputRunnerMessage"
    user_message_id: AgentMessageID
    check_name: str
    run_id: RunID
    output_entries: tuple[ActionOutputUnion, ...]


class ChecksDefinedRunnerMessage(EphemeralRunnerMessage):
    object_type: str = "ChecksDefinedRunnerMessage"
    user_message_id: AgentMessageID
    check_by_name: dict[str, Check]


class ProgressUpdateRunnerMessage(EphemeralRunnerMessage):
    """
    Represents point-in-time progress information from long-running operations
    (e.g. file downloads and subprocess invocations).
    """

    object_type: str = "ProgressUpdateRunnerMessage"
    progress: RootProgress


PersistentRunnerMessageUnion = (
    Annotated[KilledAgentRunnerMessage, Tag("KilledAgentRunnerMessage")]
    | Annotated[AgentCrashedRunnerMessage, Tag("AgentCrashedRunnerMessage")]
    | Annotated[EnvironmentCrashedRunnerMessage, Tag("EnvironmentCrashedRunnerMessage")]
    | Annotated[UnexpectedErrorRunnerMessage, Tag("UnexpectedErrorRunnerMessage")]
    | Annotated[AgentSnapshotRunnerMessage, Tag("AgentSnapshotRunnerMessage")]
    | Annotated[StartedAgentSnapshotRunnerMessage, Tag("StartedAgentSnapshotRunnerMessage")]
    | Annotated[AgentSnapshotFailureRunnerMessage, Tag("AgentSnapshotFailureRunnerMessage")]
    | Annotated[ResumeAgentResponseRunnerMessage, Tag("ResumeAgentResponseRunnerMessage")]
    | Annotated[ProgressUpdateRunnerMessage, Tag("ProgressUpdateRunnerMessage")]
)
EphemeralRunnerMessageUnion = (
    Annotated[WarningRunnerMessage, Tag("WarningRunnerMessage")]
    | Annotated[TaskStatusRunnerMessage, Tag("TaskStatusRunnerMessage")]
    | Annotated[CheckLaunchedRunnerMessage, Tag("CheckLaunchedRunnerMessage")]
    | Annotated[CheckFinishedRunnerMessage, Tag("CheckFinishedRunnerMessage")]
    | Annotated[CheckOutputRunnerMessage, Tag("CheckOutputRunnerMessage")]
    | Annotated[ChecksDefinedRunnerMessage, Tag("ChecksDefinedRunnerMessage")]
    | Annotated[EnvironmentStoppedRunnerMessage, Tag("EnvironmentStoppedRunnerMessage")]
    | Annotated[EnvironmentCreatedRunnerMessage, Tag("EnvironmentCreatedRunnerMessage")]
    | Annotated[EnvironmentRestartedRunnerMessage, Tag("EnvironmentRestartedRunnerMessage")]
)
RunnerMessageUnion = PersistentRunnerMessageUnion | EphemeralRunnerMessageUnion


class EphemeralAgentMessage(EphemeralMessage):
    """Base class for messages sent from the agent."""

    source: AgentMessageSource = AgentMessageSource.AGENT


AgentMessage = PersistentAgentMessage | EphemeralAgentMessage


class ContextSummaryMessage(PersistentAgentMessage):
    object_type: str = "ContextSummaryMessage"
    content: str


class PartialResponseBlockAgentMessage(EphemeralAgentMessage):
    """Ephemeral message with accumulated streaming content.

    Contains complete accumulated content so far (not just delta).
    Used for real-time UI updates during streaming.
    """

    object_type: str = "PartialResponseBlockAgentMessage"
    content: tuple[ContentBlockTypes, ...] = ()
    assistant_message_id: AssistantMessageID
    # The message_id that will be used for the first ResponseBlockAgentMessage of this turn.
    # Used to ensure ChatMessage.id is stable from the first partial and matches a persisted message.
    first_response_message_id: AgentMessageID


class StreamingMessageCompleteAgentMessage(EphemeralAgentMessage):
    """Ephemeral marker indicating streaming for one response block is complete.

    Emitted on message_stop from Claude Code. Not persisted to DB - only used
    for live message_conversion to reset its streaming state.
    """

    object_type: str = "StreamingMessageCompleteAgentMessage"


class UpdatedArtifactAgentMessage(EphemeralAgentMessage):
    object_type: str = "UpdatedArtifactAgentMessage"
    artifact: FileAgentArtifact


class RequestStartedAgentMessage(PersistentAgentMessage):
    object_type: str = "RequestStartedAgentMessage"
    request_id: AgentMessageID


class RemoveQueuedMessageAgentMessage(PersistentAgentMessage):
    object_type: str = "RemoveQueuedMessageAgentMessage"
    removed_message_id: AgentMessageID


class RequestCompleteAgentMessage(abc.ABC):
    request_id: AgentMessageID
    error: SerializedException | None


class PersistentRequestCompleteAgentMessage(PersistentAgentMessage, RequestCompleteAgentMessage, abc.ABC): ...


class EphemeralRequestCompleteAgentMessage(EphemeralAgentMessage, RequestCompleteAgentMessage):
    object_type: str = "EphemeralRequestCompleteAgentMessage"
    request_id: AgentMessageID
    error: SerializedException | None


# TODO: make pyre understand inheritance in pydantic so it understands that request_id isn't uninitialized
class RequestSkippedAgentMessage(PersistentRequestCompleteAgentMessage):  # pyre-fixme[13]
    object_type: str = "RequestSkippedAgentMessage"
    error: None = None


# TODO: make pyre understand inheritance in pydantic so it understands that request_id isn't uninitialized
class RequestSuccessAgentMessage(PersistentRequestCompleteAgentMessage):  # pyre-fixme[13]
    object_type: str = "RequestSuccessAgentMessage"
    error: None = None


# TODO: make pyre understand inheritance in pydantic so it understands that request_id isn't uninitialized
class RequestFailureAgentMessage(PersistentRequestCompleteAgentMessage, ErrorMessage):  # pyre-fixme[13]
    object_type: str = "RequestFailureAgentMessage"
    error: SerializedException


# TODO: make pyre understand inheritance in pydantic so it understands that request_id isn't uninitialized
class RequestStoppedAgentMessage(PersistentRequestCompleteAgentMessage):  # pyre-fixme[13]
    object_type: str = "RequestStoppedAgentMessage"
    error: SerializedException


class UserCommandFailureAgentMessage(PersistentAgentMessage, ErrorMessage):
    object_type: str = "UserCommandFailureAgentMessage"
    error: SerializedException


class ServerReadyAgentMessage(EphemeralAgentMessage):
    object_type: str = "ServerReadyAgentMessage"
    url: AnyUrl
    name: str


ErrorMessageUnion = Annotated[
    Annotated[RequestFailureAgentMessage, Tag("RequestFailureAgentMessage")]
    | Annotated[EnvironmentCrashedRunnerMessage, Tag("EnvironmentCrashedRunnerMessage")]
    | Annotated[UnexpectedErrorRunnerMessage, Tag("UnexpectedErrorRunnerMessage")]
    | Annotated[AgentCrashedRunnerMessage, Tag("AgentCrashedRunnerMessage")]
    | Annotated[UserCommandFailureAgentMessage, Tag("UserCommandFailureAgentMessage")],
    build_discriminator(),
]


class MCPStateUpdateAgentMessage(EphemeralAgentMessage):
    object_type: str = "MCPStateUpdateAgentMessage"
    mcp_servers: dict[str, MCPServerInfo]


class StreamingStderrAgentMessage(EphemeralAgentMessage):
    object_type: str = "StreamingStderrAgentMessage"
    stderr_line: str
    metadata: dict[str, Any] | None = None


class WarningAgentMessage(PersistentAgentMessage, WarningMessage):
    object_type: str = "WarningAgentMessage"


PersistentAgentMessageUnion = (
    # TODO: why is this in PersistentAgentMessageUnion?
    Annotated[EphemeralRequestCompleteAgentMessage, Tag("EphemeralRequestCompleteAgentMessage")]
    | Annotated[RequestSuccessAgentMessage, Tag("RequestSuccessAgentMessage")]
    | Annotated[RequestFailureAgentMessage, Tag("RequestFailureAgentMessage")]
    | Annotated[UserCommandFailureAgentMessage, Tag("UserCommandFailureAgentMessage")]
    | Annotated[ResponseBlockAgentMessage, Tag("ResponseBlockAgentMessage")]
    | Annotated[WarningAgentMessage, Tag("WarningAgentMessage")]
    | Annotated[RequestStartedAgentMessage, Tag("RequestStartedAgentMessage")]
    | Annotated[RequestSkippedAgentMessage, Tag("RequestSkippedAgentMessage")]
    | Annotated[RequestStoppedAgentMessage, Tag("RequestStoppedAgentMessage")]
    | Annotated[ContextSummaryMessage, Tag("ContextSummaryMessage")]
    | Annotated[RemoveQueuedMessageAgentMessage, Tag("RemoveQueuedMessageAgentMessage")]
)
EphemeralAgentMessageUnion = (
    Annotated[PartialResponseBlockAgentMessage, Tag("PartialResponseBlockAgentMessage")]
    | Annotated[ServerReadyAgentMessage, Tag("ServerReadyAgentMessage")]
    | Annotated[StreamingStderrAgentMessage, Tag("StreamingStderrAgentMessage")]
    | Annotated[MCPStateUpdateAgentMessage, Tag("MCPStateUpdateAgentMessage")]
    | Annotated[UpdatedArtifactAgentMessage, Tag("UpdatedArtifactAgentMessage")]
)
AgentMessageUnion = PersistentAgentMessageUnion | EphemeralAgentMessageUnion
# this is necessary because pydantic won't let us use PersistentMessageTypes, which already has a discriminator, to make MessageTypes
PersistentMessageTypesUnannotated = (
    PersistentAgentMessageUnion
    | PersistentRunnerMessageUnion
    | PersistentUserMessageUnion
    | PersistentSystemMessageUnion
)
PersistentMessageTypes = Annotated[PersistentMessageTypesUnannotated, build_discriminator()]

EphemeralMessageTypes = EphemeralAgentMessageUnion | EphemeralRunnerMessageUnion | EphemeralUserMessageUnion

# TODO: my changes here added some types, like PersistentMessageTypes and ManualSyncMergeIntoAgentAttemptedMessage! is this ok?
MessageTypes = Annotated[
    PersistentMessageTypesUnannotated | EphemeralMessageTypes | SystemMessageUnion,
    build_discriminator(),
]


class TaskLifecycleAction(StrEnum):
    DELETED = "DELETED"
    ARCHIVED = "ARCHIVED"
    UNARCHIVED = "UNARCHIVED"


class MCPServerType(StrEnum):
    """Type of MCP server"""

    IMBUE_CLI = "imbue_cli"  # Servers provided by imbue-cli
    EXTERNAL = "external"  # External/third-party MCP servers


class MCPServerInfo(SerializableModel):
    """Information about an MCP server including its status and available tools"""

    status: str = Field(..., description="Connection status of the MCP server")
    server_type: MCPServerType = Field(..., description="Type of MCP server")
    tools: list[str] = Field(default_factory=list, description="List of tool names available from this server")


class AgentConfig(SerializableModel):
    object_type: str


class DefaultAgentConfig(AgentConfig):
    """
    By convention, we suggest that all agents create tmux panes and a ttyd server to allow easy inspection of the agent.
    """

    tmux_session_name: str | None = None
    tmux_scrollback_path: str | None = None
    ttyd_port: int | None = None


# TODO (Andy): Make this not a default agent
class HelloAgentConfig(DefaultAgentConfig):
    object_type: str = "HelloAgentConfig"
    command: str = "echo"  # Default command to run


class ClaudeCodeSDKAgentConfig(DefaultAgentConfig):
    object_type: str = "ClaudeCodeSDKAgentConfig"


class CodexAgentConfig(DefaultAgentConfig):
    object_type: str = "CodexAgentConfig"


AgentConfigTypes = Annotated[
    Annotated[HelloAgentConfig, Tag("HelloAgentConfig")]
    | Annotated[ClaudeCodeSDKAgentConfig, Tag("ClaudeCodeSDKAgentConfig")]
    | Annotated[CodexAgentConfig, Tag("CodexAgentConfig")],
    build_discriminator(),
]
