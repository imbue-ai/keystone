import datetime
from abc import ABC
from enum import StrEnum
from enum import auto
from typing import Annotated
from typing import Any
from typing import Generic
from typing import TypeVar
from typing import assert_never

from pydantic import AnyUrl
from pydantic import Field
from pydantic import PrivateAttr
from pydantic import Tag
from pydantic import computed_field

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.itertools import only
from imbue_core.progress_tracking.progress_models import RootProgress
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.pydantic_serialization import build_discriminator
from imbue_core.sculptor.state.chat_state import ChatMessage
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.sculptor.state.messages import Message
from imbue_core.upper_case_str_enum import UpperCaseStrEnum
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import BaseTaskState
from sculptor.database.models import CacheReposInputsV1
from sculptor.database.models import CacheReposTaskStateV1
from sculptor.database.models import CleanupImagesInputsV1
from sculptor.database.models import CleanupImagesTaskStateV1
from sculptor.database.models import MustBeShutDownTaskInputsV1
from sculptor.database.models import Notification
from sculptor.database.models import Project
from sculptor.database.models import SendEmailTaskInputsV1
from sculptor.database.models import SendEmailTaskStateV1
from sculptor.database.models import Task
from sculptor.database.models import TaskID
from sculptor.database.models import TaskInputs
from sculptor.database.models import UserSettings
from sculptor.interfaces.agents.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.agents.agent import CheckFinishedRunnerMessage
from sculptor.interfaces.agents.agent import CheckLaunchedRunnerMessage
from sculptor.interfaces.agents.agent import CheckOutputRunnerMessage
from sculptor.interfaces.agents.agent import ChecksDefinedRunnerMessage
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.agents.agent import CompactTaskUserMessage
from sculptor.interfaces.agents.agent import EnvironmentCreatedRunnerMessage
from sculptor.interfaces.agents.agent import ForkAgentSystemMessage
from sculptor.interfaces.agents.agent import LocalSyncDisabledMessage
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion
from sculptor.interfaces.agents.agent import LocalSyncSetupAndEnabledMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupProgressMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupStartedMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupStep
from sculptor.interfaces.agents.agent import LocalSyncTeardownProgressMessage
from sculptor.interfaces.agents.agent import LocalSyncTeardownStartedMessage
from sculptor.interfaces.agents.agent import LocalSyncTeardownStep
from sculptor.interfaces.agents.agent import LocalSyncUpdateCompletedMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdatePausedMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdatePendingMessage
from sculptor.interfaces.agents.agent import MCPServerInfo
from sculptor.interfaces.agents.agent import MCPStateUpdateAgentMessage
from sculptor.interfaces.agents.agent import PersistentRequestCompleteAgentMessage
from sculptor.interfaces.agents.agent import RequestStartedAgentMessage
from sculptor.interfaces.agents.agent import RequestSuccessAgentMessage
from sculptor.interfaces.agents.agent import ServerReadyAgentMessage
from sculptor.interfaces.agents.agent import TaskStatusRunnerMessage
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.interfaces.agents.tasks import TaskState
from sculptor.primitives.ids import RequestID
from sculptor.services.git_repo_service.git_repos import GitRepoStatus
from sculptor.services.git_repo_service.ref_namespace_stasher import SculptorStashSingleton
from sculptor.utils.functional import first


class TaskInterface(StrEnum):
    TERMINAL = "TERMINAL"
    API = "API"


class TaskStatus(StrEnum):
    BUILDING = "BUILDING"  # Docker container is being built
    RUNNING = "RUNNING"  # Claude code process is actively running
    READY = "READY"  # Process completed successfully, waiting for input
    ERROR = "ERROR"  # Process encountered an error (stderr output)


class LocalSyncStatus(UpperCaseStrEnum):
    INACTIVE = auto()
    STARTING = auto()
    ACTIVE = auto()
    ACTIVE_SYNCING = auto()
    PAUSED = auto()
    STOPPING = auto()


class LocalSyncState(SerializableModel):
    status: LocalSyncStatus
    last_updated: datetime.datetime | None
    notices: tuple[LocalSyncNoticeUnion, ...] = Field(default_factory=tuple)
    is_resumption: bool = False
    setup_step: LocalSyncSetupStep | None = None
    teardown_step: LocalSyncTeardownStep | None = None
    sync_branch: str | None = None
    original_branch: str | None = None


TaskInputType = TypeVar("TaskInputType", bound=TaskInputs)
TaskStateType = TypeVar("TaskStateType", bound=BaseTaskState)


class LimitedBaseTaskView(SerializableModel, Generic[TaskInputType, TaskStateType], ABC):
    """
    This class represents a view of the state of any task that is being executed.

    It is limited in that an implementor shouldn't necessarily _need_ messages

    Note that this class is mutable!  The messages are continually updated over time.
    """

    # the actual task object, wrapped in a list which we effectively use as a mutable reference
    _task_container: list[Task] = PrivateAttr(default_factory=list)

    @property
    def task(self) -> Task:
        return only(self._task_container)

    def update_task(self, task: Task) -> None:
        """Update the underlying task object with fresh data"""
        self._task_container[0] = task

    @property
    def task_input(self) -> TaskInputType:
        # pyre-fixme[7]: self.task.input_data is a union type, but the return value is a type variable, which could be a fixed variant. Maybe make the Task type generic in its input_data type.
        return self.task.input_data

    @property
    def task_state(self) -> TaskStateType | None:
        # pyre-fixme[7]: self.task.current_state is a union type, but the return value is a type variable, which could be a fixed variant. Maybe make the Task type generic in its current_state type.
        return self.task.current_state

    @computed_field
    @property
    def id(self) -> TaskID:
        return self.task.object_id

    @computed_field
    @property
    def project_id(self) -> ProjectID:
        return self.task.project_id

    @computed_field
    @property
    def created_at(self) -> datetime.datetime:
        return self.task.created_at

    @computed_field
    @property
    def task_status(self) -> TaskState:
        return self.task.outcome

    def _maybe_get_status_from_outcome(self) -> TaskStatus | None:
        """
        NOTE: This is almost always None because outcome is never set while task is running.
        I Extracted it when I thought we were caching task status on state somehow.
        """
        if self.task.outcome == TaskState.FAILED:
            return TaskStatus.ERROR
        if self.task.outcome == TaskState.QUEUED:
            return TaskStatus.BUILDING

        # FIXME: fix this status
        # this is a little weird, but sure, I guess that's the right state...
        if self.task.outcome in (TaskState.SUCCEEDED, TaskState.CANCELLED, TaskState.ARCHIVED, TaskState.DELETED):
            return TaskStatus.READY

        # otherwise, the task is running.
        assert self.task.outcome == TaskState.RUNNING, f"Unexpected task outcome: {self.task.outcome}"
        # if there's no image, we're still building
        if self.task_state is None:
            return TaskStatus.BUILDING
        return None


class TaskView(LimitedBaseTaskView[TaskInputType, TaskStateType], Generic[TaskInputType, TaskStateType], ABC):
    """
    This class represents a view of the state of any task that is being executed.

    The messages serialized and sent separately, but are logically part of the task's state.

    Note that this class is mutable!  The messages are continually updated over time.
    """

    object_type: str

    # our reference to settings (controls some serialized fields)
    _settings_container: list[SculptorSettings] = PrivateAttr(default_factory=list)

    # messages that were sent to or from the task.
    # this attribute is private because it enables easy serialization to the front end.
    _messages: list[Message] = PrivateAttr(default_factory=list)

    @property
    def settings(self) -> SculptorSettings:
        return only(self._settings_container)

    @computed_field
    @property
    def is_compacting(self) -> bool:
        compact_message = None
        for message in reversed(self._messages):
            if isinstance(message, CompactTaskUserMessage):
                compact_message = message.message_id
                break
        if compact_message is None:
            return False
        for message in reversed(self._messages):
            if isinstance(message, RequestSuccessAgentMessage):
                if message.request_id == compact_message:
                    return False
            if isinstance(message, RequestStartedAgentMessage):
                if message.request_id == compact_message:
                    return True
        return False

    @computed_field
    @property
    def artifact_names(self) -> list[str]:
        artifact_messages = [x for x in self._messages if isinstance(x, UpdatedArtifactAgentMessage)]
        return list({x.artifact.name for x in artifact_messages})

    @computed_field
    @property
    def updated_at(self) -> datetime.datetime:
        if len(self._messages) == 0:
            return self.created_at
        return self._messages[-1].approximate_creation_time

    def add_message(self, message: Message) -> None:
        """During each update, we add the new messages"""
        self._messages.append(message)

    @computed_field
    @property
    def sync(self) -> LocalSyncState:
        for message in reversed(self._messages):
            match message:
                case LocalSyncTeardownProgressMessage():
                    return LocalSyncState(
                        status=LocalSyncStatus.STOPPING,
                        last_updated=message.approximate_creation_time,
                        teardown_step=message.next_step,
                        sync_branch=message.sync_branch,
                        original_branch=message.original_branch,
                    )
                case LocalSyncTeardownStartedMessage():
                    return LocalSyncState(
                        status=LocalSyncStatus.STOPPING,
                        last_updated=message.approximate_creation_time,
                    )
                case LocalSyncUpdatePendingMessage():
                    return LocalSyncState(
                        status=LocalSyncStatus.ACTIVE_SYNCING,
                        last_updated=message.approximate_creation_time,
                    )
                case LocalSyncUpdateCompletedMessage():
                    # updates should always imply active, and active can have non-blocking issues.
                    return LocalSyncState(
                        status=LocalSyncStatus.ACTIVE,
                        notices=message.all_notices,
                        last_updated=message.approximate_creation_time,
                        is_resumption=message.is_resumption,
                    )
                case LocalSyncSetupAndEnabledMessage():
                    # we only started and initialized just now
                    return LocalSyncState(
                        status=LocalSyncStatus.ACTIVE, last_updated=message.approximate_creation_time
                    )
                case LocalSyncSetupProgressMessage():
                    return LocalSyncState(
                        status=LocalSyncStatus.STARTING,
                        last_updated=message.approximate_creation_time,
                        setup_step=message.next_step,
                        sync_branch=message.sync_branch,
                        original_branch=message.original_branch,
                    )
                case LocalSyncSetupStartedMessage():
                    return LocalSyncState(
                        status=LocalSyncStatus.STARTING,
                        last_updated=message.approximate_creation_time,
                        sync_branch=message.sync_branch,
                        original_branch=message.original_branch,
                    )
                case LocalSyncUpdatePausedMessage():
                    return LocalSyncState(
                        status=LocalSyncStatus.PAUSED,
                        notices=message.all_notices,
                        last_updated=message.approximate_creation_time,
                    )
                case LocalSyncDisabledMessage():
                    return LocalSyncState(
                        status=LocalSyncStatus.INACTIVE, last_updated=message.approximate_creation_time
                    )
                case _:
                    continue

        return LocalSyncState(status=LocalSyncStatus.INACTIVE, last_updated=None)

    @computed_field
    @property
    def sync_started_at(self) -> datetime.datetime | None:
        for message in reversed(self._messages):
            if isinstance(message, LocalSyncSetupAndEnabledMessage):
                return message.approximate_creation_time
            elif isinstance(message, LocalSyncDisabledMessage):
                return None
        return None


class CodingAgentTaskView(TaskView[AgentTaskInputsV1, AgentTaskStateV1]):
    """
    messages are the primary way of interacting with an agent.

    this class is simply a way of deriving the current state of the agent based on the message log.

    because agents are run as idempotent tasks, consumers MUST be able to handle duplicate messages.
    this is particularly tricky because you cannot deduplicate on message_id here --
    the ids may be different between two different runs
    (and that cannot be fixed because different things may have happened)
    consumers *may* process messages in a "task aware" manner, eg,
    by paying attention to the task start and stop messages in order to properly discard outdated messages.
    """

    object_type: str = "CodingAgentTaskView"

    # TODO(post swap): replace with goal or updated_goal
    @computed_field
    @property
    def initial_prompt(self) -> str:
        return self.goal

    @computed_field
    @property
    def title_or_something_like_it(self) -> str:
        return self.title or self.initial_prompt

    @computed_field
    @property
    def interface(self) -> TaskInterface:
        return TaskInterface.API

    @computed_field
    @property
    def system_prompt(self) -> str | None:
        input_data = self.task.input_data
        assert isinstance(input_data, AgentTaskInputsV1)
        return input_data.system_prompt

    @computed_field
    @property
    def parent_id(self) -> TaskID | None:
        return self.task.parent_task_id

    @computed_field
    @property
    def model(self) -> LLMModel:
        last_input_message = first(
            x for x in reversed(self._messages) if isinstance(x, ChatInputUserMessage) and x.model_name is not None
        )
        # NOTE: this is hacky, but it is due to a quirk in the task subscription system. Talk to Guinness for more details.
        # goal should *rarely* be None, but it will be None for a single frame when the task is first created.
        if last_input_message is None:
            return LLMModel.CLAUDE_4_SONNET
        return last_input_message.model_name

    @computed_field
    @property
    def is_smooth_streaming_supported(self) -> bool:
        return self.model in (LLMModel.CLAUDE_4_SONNET, LLMModel.CLAUDE_4_OPUS, LLMModel.CLAUDE_4_HAIKU)

    @computed_field
    @property
    def is_archived(self) -> bool:
        return self.task.is_archived or self.task.is_archiving

    @computed_field
    @property
    def is_deleted(self) -> bool:
        return self.task.is_deleted or self.task.is_deleting

    @computed_field
    @property
    def source_branch(self) -> str:
        return self.task_input.initial_branch

    @computed_field
    @property
    def branch_name(self) -> str | None:
        task_state = self.task_state
        if task_state is None:
            return None
        assert isinstance(task_state, AgentTaskStateV1)
        return task_state.branch_name

    @computed_field
    @property
    def title(self) -> str | None:
        task_state = self.task_state
        if task_state is None:
            return None
        assert isinstance(task_state, AgentTaskStateV1)
        return task_state.title

    # TODO(post swap): split into task_status and agent_status, separate the BUILDING state out of TaskStatus
    @computed_field
    @property
    def status(self) -> TaskStatus:
        task_from_outcome = self._maybe_get_status_from_outcome()
        if task_from_outcome is not None:
            return task_from_outcome

        # if we have started running but don't have an environment created message, we're still building.
        environment_created_message = None
        for message in self._messages:
            if isinstance(message, EnvironmentCreatedRunnerMessage):
                environment_created_message = message
            elif isinstance(message, TaskStatusRunnerMessage) and message.outcome == TaskState.RUNNING:
                environment_created_message = None
        if environment_created_message is None:
            return TaskStatus.BUILDING

        # if we're blocked on user input, return READY.
        chat_input_messages = [
            x
            for x in self._messages
            if isinstance(x, ChatInputUserMessage)
            or isinstance(x, CommandInputUserMessage)
            or isinstance(x, CompactTaskUserMessage)
        ]
        request_finished_messages = set(
            [x.request_id for x in self._messages if isinstance(x, PersistentRequestCompleteAgentMessage)]
        )
        is_ready = all(input_message.message_id in request_finished_messages for input_message in chat_input_messages)
        if is_ready:
            return TaskStatus.READY
        # otherwise I guess we're running.
        return TaskStatus.RUNNING

    @computed_field
    @property
    def number_of_snapshots(self) -> int:
        return sum(isinstance(x, AgentSnapshotRunnerMessage) and x.image is not None for x in self._messages)

    @computed_field
    @property
    def server_url_by_name(self) -> dict[str, AnyUrl]:
        server_url_by_name = {}
        for message in self._messages:
            if isinstance(message, ServerReadyAgentMessage):
                server_url_by_name[message.name] = message.url
        return server_url_by_name

    # TODO: it's not clear that we want to bother doing much of this logic at all on the server.
    #  (mostly because it's really inefficient, and it also can't be as easily altered by plugins.)
    @computed_field
    @property
    def goal(self) -> str:
        # Find the last fork message (if any) by searching in reverse
        last_fork_index = None
        for i in range(len(self._messages) - 1, -1, -1):
            if isinstance(self._messages[i], ForkAgentSystemMessage):
                last_fork_index = i
                break

        # If there's a fork message, get the first ChatInputUserMessage after it
        if last_fork_index is not None:
            goal = first(
                x.text
                for i, x in enumerate(self._messages)
                if i > last_fork_index and isinstance(x, ChatInputUserMessage)
            )
        else:
            # Otherwise, just get the first ChatInputUserMessage
            goal = first(x.text for x in self._messages if isinstance(x, ChatInputUserMessage))

        # NOTE: this is hacky, but it is due to a quirk in the task subscription system. Talk to Guinness for more details.
        # goal should *rarely* be None, but it will be None for a single frame when the task is first created.
        if goal is None:
            return ""
        return goal

    @computed_field
    @property
    def is_dev(self) -> bool:
        return self.settings.DEV_MODE

    @computed_field
    @property
    def mcp_servers(self) -> dict[str, MCPServerInfo]:
        last_message = first(x for x in reversed(self._messages) if isinstance(x, MCPStateUpdateAgentMessage))
        if last_message is None:
            return {}
        # for the type checker
        assert isinstance(last_message, MCPStateUpdateAgentMessage)
        return last_message.mcp_servers


class SyncedTaskView(LimitedBaseTaskView[AgentTaskInputsV1, AgentTaskStateV1]):
    """Limited interface necessary for sync components in the frontend"""

    _sync: LocalSyncState = PrivateAttr()
    _sync_started_at: datetime.datetime = PrivateAttr()

    @computed_field
    @property
    def sync(self) -> LocalSyncState:
        return self._sync

    @computed_field
    @property
    def sync_started_at(self) -> datetime.datetime | None:
        return self._sync_started_at

    # Ultimately had to copy a bunch anyways because of type genericism
    @computed_field
    @property
    def is_archived(self) -> bool:
        return self.task.is_archived

    @computed_field
    @property
    def is_deleted(self) -> bool:
        return self.task.is_deleted or self.task.is_deleting

    @computed_field
    @property
    def source_branch(self) -> str:
        task_input = self.task_input
        assert isinstance(task_input, AgentTaskInputsV1)
        return task_input.initial_branch

    @computed_field
    @property
    def branch_name(self) -> str | None:
        task_state = self.task_state
        if task_state is None:
            return None
        assert isinstance(task_state, AgentTaskStateV1)
        return task_state.branch_name

    @computed_field
    @property
    def title(self) -> str | None:
        task_state = self.task_state
        if task_state is None:
            return None
        assert isinstance(task_state, AgentTaskStateV1)
        return task_state.title

    @computed_field
    @property
    def title_or_something_like_it(self) -> str:
        return self.title or str(self.task.object_id)

    @computed_field
    @property
    def status(self) -> TaskStatus | None:
        return self._maybe_get_status_from_outcome()

    @classmethod
    def build(cls, task: Task, sync: LocalSyncState, sync_started_at: datetime.datetime) -> "SyncedTaskView":
        view = cls()
        view._sync = sync
        view._sync_started_at = sync_started_at
        view._task_container = [task]
        return view


class GlobalLocalSyncInfo(SerializableModel):
    """Container for global sync state information across projects."""

    synced_task: SyncedTaskView | None
    stash_singleton: SculptorStashSingleton | None

    def model_post_init(self, __context: Any) -> None:
        if (
            self.synced_task is not None
            and self.stash_singleton is not None
            and self.synced_task.project_id != self.stash_singleton.owning_project_id
        ):
            message = (
                "LOCAL_SYNC: very surprising error! sculptor stash and synced task have different project IDs:",
                f"(synced {self.synced_task.project_id} != stashed {self.stash_singleton.owning_project_id}).",
                "Please contact support",
            )
            raise AssertionError(" ".join(message))
        return super().model_post_init(__context)


class SendEmailTaskView(TaskView[SendEmailTaskInputsV1, SendEmailTaskStateV1]):
    object_type: str = "SendEmailTaskView"


class CleanupImagesTaskView(TaskView[CleanupImagesInputsV1, CleanupImagesTaskStateV1]):
    object_type: str = "CleanupImagesTaskView"


class CacheReposTaskView(TaskView[CacheReposInputsV1, CacheReposTaskStateV1]):
    object_type: str = "CacheReposTaskView"


TaskViewTypes = Annotated[
    Annotated[CodingAgentTaskView, Tag("CodingAgentTaskView")]
    | Annotated[SendEmailTaskView, Tag("SendEmailTaskView")]
    | Annotated[CleanupImagesTaskView, Tag("CleanupImagesTaskView")]
    | Annotated[CacheReposTaskView, Tag("CacheReposTaskView")],
    build_discriminator(),
]


class InsertedChatMessage(SerializableModel):
    message: ChatMessage
    after_message_id: AgentMessageID


class TaskUpdate(SerializableModel):
    """Represents an incremental update to task state sent to the frontend via SSE/WebSocket.

    Initial Connection:
    - Sends complete current state (all completed messages, current in-progress message, etc.)
    - Provides frontend with full context to render the UI

    Subsequent Updates:
    - Only sends deltas (new messages, changed state, etc.)
    - Frontend merges updates with existing state

    Field Update Patterns:
    - chat_messages: Only new completed messages are sent; frontend appends to existing list
    - in_progress_chat_message: Sent in full each time it changes; frontend replaces previous value
    - queued_chat_messages: Full list sent each time; frontend replaces entire queue
    - updated_artifacts: Lists artifacts that changed; frontend fetches updated content
    - finished_request_ids: IDs of completed requests for frontend to acknowledge
    - logs: New log lines only; frontend appends to existing logs
    - inserted_chat_messages: For when we want to insert the message after a specific message, not just append

    The frontend is responsible for:
    - Maintaining cumulative state by merging updates
    - Replacing vs appending based on field semantics
    - Fetching artifact data when notified of updates
    """

    task_id: TaskID
    chat_messages: tuple[ChatMessage, ...]
    updated_artifacts: tuple[ArtifactType, ...]
    in_progress_chat_message: ChatMessage | None
    queued_chat_messages: tuple[ChatMessage, ...]
    finished_request_ids: tuple[RequestID, ...]
    logs: tuple[str, ...]
    in_progress_user_message_id: AgentMessageID | None
    check_update_messages: tuple[
        ChecksDefinedRunnerMessage | CheckLaunchedRunnerMessage | CheckFinishedRunnerMessage, ...
    ]
    new_check_output_messages: tuple[CheckOutputRunnerMessage, ...]
    inserted_messages: tuple[InsertedChatMessage, ...] = ()
    feedback_by_message_id: dict[str, str] = {}
    # Track streaming state across updates - index where streaming content starts in in_progress_chat_message
    streaming_start_index: int
    is_streaming_active: bool = False
    progress: RootProgress | None = None


# NOTE: not currently related to sculptor/sculptor/web/data_types.py RepoInfo,
# which contains more concrete data like Path as well as "recent branches."
# May want to consolidate in the future.
class LocalRepoInfo(SerializableModel):
    status: GitRepoStatus
    current_branch: str
    project_id: ProjectID


class UserUpdate(SerializableModel):
    user_settings: UserSettings | None = None
    projects: tuple[Project, ...] = ()
    settings: SculptorSettings | None = None
    notifications: tuple[Notification, ...] = ()


def create_initial_task_view(task: Task, settings: SculptorSettings) -> TaskViewTypes:
    # For some reason, matching on task.input_data directly makes Pyre fail the exhaustiveness check
    input_data = task.input_data
    task_view_class: type[TaskViewTypes]
    match input_data:
        case AgentTaskInputsV1():
            task_view_class = CodingAgentTaskView
        case SendEmailTaskInputsV1():
            task_view_class = SendEmailTaskView
        case CleanupImagesInputsV1():
            task_view_class = CleanupImagesTaskView
        case CacheReposInputsV1():
            task_view_class = CacheReposTaskView
        case MustBeShutDownTaskInputsV1():
            assert False, "MustBeShutDownTaskInputsV1 should only occur in testing"
        case _ as unreachable:
            assert_never(unreachable)
    instance = task_view_class()
    instance._task_container.append(task)
    instance._settings_container.append(settings)
    return instance
