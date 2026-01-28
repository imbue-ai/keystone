import pytest
from pydantic import AnyUrl

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.ids import AssistantMessageID
from imbue_core.progress_tracking.progress_models import BranchNameAndTaskTitleProgress
from imbue_core.progress_tracking.progress_models import MultiOperationProgress
from imbue_core.progress_tracking.progress_models import OperationState
from imbue_core.progress_tracking.progress_models import ProgressID
from imbue_core.progress_tracking.progress_models import RootProgress
from imbue_core.sculptor.state.chat_state import TextBlock
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import ResponseBlockAgentMessage
from imbue_core.serialization import SerializedException
from imbue_core.time_utils import get_current_time
from sculptor.interfaces.agents.agent import AgentCrashedRunnerMessage
from sculptor.interfaces.agents.agent import AgentMessageSource
from sculptor.interfaces.agents.agent import AgentSnapshotFailureRunnerMessage
from sculptor.interfaces.agents.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.agents.agent import Check
from sculptor.interfaces.agents.agent import CheckFinishedReason
from sculptor.interfaces.agents.agent import CheckFinishedRunnerMessage
from sculptor.interfaces.agents.agent import CheckLaunchedRunnerMessage
from sculptor.interfaces.agents.agent import CheckOutputRunnerMessage
from sculptor.interfaces.agents.agent import ChecksDefinedRunnerMessage
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.agents.agent import CompactTaskUserMessage
from sculptor.interfaces.agents.agent import ContextSummaryMessage
from sculptor.interfaces.agents.agent import EnvironmentCrashedRunnerMessage
from sculptor.interfaces.agents.agent import EnvironmentCreatedRunnerMessage
from sculptor.interfaces.agents.agent import EnvironmentRestartedRunnerMessage
from sculptor.interfaces.agents.agent import EnvironmentStoppedRunnerMessage
from sculptor.interfaces.agents.agent import EphemeralRequestCompleteAgentMessage
from sculptor.interfaces.agents.agent import FileAgentArtifact
from sculptor.interfaces.agents.agent import ForkAgentSystemMessage
from sculptor.interfaces.agents.agent import GitCommitAndPushUserMessage
from sculptor.interfaces.agents.agent import GitPullUserMessage
from sculptor.interfaces.agents.agent import InterruptProcessUserMessage
from sculptor.interfaces.agents.agent import KilledAgentRunnerMessage
from sculptor.interfaces.agents.agent import LocalSyncDisabledMessage
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
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
from sculptor.interfaces.agents.agent import MCPServerType
from sculptor.interfaces.agents.agent import MCPStateUpdateAgentMessage
from sculptor.interfaces.agents.agent import ManualSyncMergeIntoAgentAttemptedMessage
from sculptor.interfaces.agents.agent import ManualSyncMergeIntoAgentNoticeLabel
from sculptor.interfaces.agents.agent import ManualSyncMergeIntoUserAttemptedMessage
from sculptor.interfaces.agents.agent import MessageFeedbackUserMessage
from sculptor.interfaces.agents.agent import MessageTypes
from sculptor.interfaces.agents.agent import PartialResponseBlockAgentMessage
from sculptor.interfaces.agents.agent import ProgressUpdateRunnerMessage
from sculptor.interfaces.agents.agent import RemoveQueuedMessageAgentMessage
from sculptor.interfaces.agents.agent import RemoveQueuedMessageUserMessage
from sculptor.interfaces.agents.agent import RequestFailureAgentMessage
from sculptor.interfaces.agents.agent import RequestSkippedAgentMessage
from sculptor.interfaces.agents.agent import RequestStartedAgentMessage
from sculptor.interfaces.agents.agent import RequestStoppedAgentMessage
from sculptor.interfaces.agents.agent import RequestSuccessAgentMessage
from sculptor.interfaces.agents.agent import RestartCheckUserMessage
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.agent import RunID
from sculptor.interfaces.agents.agent import RunnerMessageUnion
from sculptor.interfaces.agents.agent import ServerReadyAgentMessage
from sculptor.interfaces.agents.agent import SetUserConfigurationDataUserMessage
from sculptor.interfaces.agents.agent import StartedAgentSnapshotRunnerMessage
from sculptor.interfaces.agents.agent import StopAgentUserMessage
from sculptor.interfaces.agents.agent import StopCheckUserMessage
from sculptor.interfaces.agents.agent import StreamingStderrAgentMessage
from sculptor.interfaces.agents.agent import SystemMessageUnion
from sculptor.interfaces.agents.agent import TaskState
from sculptor.interfaces.agents.agent import TaskStatusRunnerMessage
from sculptor.interfaces.agents.agent import UnexpectedErrorRunnerMessage
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.agent import UserCommandFailureAgentMessage
from sculptor.interfaces.agents.agent import UserMessageUnion
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.agents.agent import WarningRunnerMessage
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.interfaces.environments.base import ModalImage
from sculptor.primitives.ids import LocalEnvironmentID
from sculptor.primitives.ids import ModalImageObjectID
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.utils.type_utils import extract_leaf_types


def _create_serialized_exception(msg: str) -> SerializedException:
    """Helper to create a SerializedException with a proper traceback."""
    try:
        raise Exception(msg)
    except Exception as e:
        return SerializedException.build(e)


@pytest.fixture
def example_messages_of_every_type() -> dict[type, MessageTypes]:
    """Create example instances of every message type in MessageTypes union."""
    messages: list[MessageTypes] = [
        # Persistent Agent Messages
        RequestSuccessAgentMessage(  # pyre-fixme[28]: pyre doesn't understand pydantic
            request_id=AgentMessageID(),
            error=None,
        ),
        RequestFailureAgentMessage(  # pyre-fixme[28]: pyre doesn't understand pydantic
            request_id=AgentMessageID(),
            error=_create_serialized_exception("test_failure"),
        ),
        UserCommandFailureAgentMessage(error=_create_serialized_exception("command_failure")),
        ResponseBlockAgentMessage(  # pyre-fixme[28]: pyre doesn't understand pydantic
            request_id=AgentMessageID(),
            role="assistant",
            assistant_message_id=AssistantMessageID("test_assistant_msg_id"),
            content=(TextBlock(text="test response"),),
        ),
        WarningAgentMessage(error=_create_serialized_exception("warning_test"), message="test warning"),
        RequestStartedAgentMessage(request_id=AgentMessageID()),
        RequestSkippedAgentMessage(request_id=AgentMessageID()),  # pyre-fixme[28]: pyre doesn't understand pydantic
        RequestStoppedAgentMessage(  # pyre-fixme[28]: pyre doesn't understand pydantic
            request_id=AgentMessageID(),
            error=_create_serialized_exception("stopped"),
        ),
        ContextSummaryMessage(content="test summary"),
        RemoveQueuedMessageAgentMessage(removed_message_id=AgentMessageID()),
        # Ephemeral Agent Messages
        EphemeralRequestCompleteAgentMessage(request_id=AgentMessageID(), error=None),
        PartialResponseBlockAgentMessage(
            content=(TextBlock(text="test response"),),
            assistant_message_id=AssistantMessageID("test_assistant_msg_id"),
            first_response_message_id=AgentMessageID(),
        ),
        ServerReadyAgentMessage(url=AnyUrl("http://localhost:8080"), name="test_server"),
        StreamingStderrAgentMessage(stderr_line="error line", metadata=None),
        MCPStateUpdateAgentMessage(
            mcp_servers={"test": MCPServerInfo(status="connected", server_type=MCPServerType.IMBUE_CLI, tools=[])}
        ),
        UpdatedArtifactAgentMessage(artifact=FileAgentArtifact(name="test.txt", url=AnyUrl("file:///tmp/test.txt"))),
        # Persistent Runner Messages
        KilledAgentRunnerMessage(full_output_url=None),
        AgentCrashedRunnerMessage(exit_code=1, full_output_url=None, error=_create_serialized_exception("crash")),
        EnvironmentCrashedRunnerMessage(error=_create_serialized_exception("env_crash"), full_output_url=None),
        UnexpectedErrorRunnerMessage(error=_create_serialized_exception("unexpected"), full_output_url=None),
        AgentSnapshotRunnerMessage(
            image=ModalImage(
                image_id=ModalImageObjectID("test_image_id"), project_id=ProjectID(), app_name="test_app"
            ),
            for_user_message_id=None,
            is_settled=True,
        ),
        AgentSnapshotFailureRunnerMessage(
            failure_reason=None,
            for_user_message_id=None,
            is_settled=True,
        ),
        StartedAgentSnapshotRunnerMessage(
            for_user_message_id=AgentMessageID(),
        ),
        ResumeAgentResponseRunnerMessage(for_user_message_id=AgentMessageID()),
        # Ephemeral Runner Messages
        WarningRunnerMessage(error=None, message="runner warning"),
        TaskStatusRunnerMessage(outcome=TaskState.RUNNING),
        CheckLaunchedRunnerMessage(
            user_message_id=AgentMessageID(),
            check=Check(command="echo test", name="test_check"),
            run_id=RunID(),
            snapshot=None,
        ),
        CheckFinishedRunnerMessage(
            user_message_id=AgentMessageID(),
            check=Check(command="echo test", name="test_check"),
            run_id=RunID(),
            exit_code=0,
            finished_reason=CheckFinishedReason.FINISHED,
            archival_reason="",
        ),
        CheckOutputRunnerMessage(
            user_message_id=AgentMessageID(), check_name="test_check", run_id=RunID(), output_entries=tuple()
        ),
        ChecksDefinedRunnerMessage(
            user_message_id=AgentMessageID(), check_by_name={"test": Check(command="echo test", name="test")}
        ),
        EnvironmentStoppedRunnerMessage(),
        EnvironmentCreatedRunnerMessage(
            environment=LocalEnvironment(
                environment_id=LocalEnvironmentID("test_environment_id"),
                project_id=ProjectID(),
                concurrency_group=ConcurrencyGroup(name="test_group"),
                config=LocalEnvironmentConfig(),
            )
        ),
        EnvironmentRestartedRunnerMessage(error=None, message="restarted"),
        # Persistent User Messages
        ChatInputUserMessage(text="test input"),
        CommandInputUserMessage(text="test command", is_included_in_context=True, run_with_sudo_privileges=False),
        CompactTaskUserMessage(),
        MessageFeedbackUserMessage(feedback_message_id=AgentMessageID(), feedback_type="positive"),
        # Ephemeral User Messages
        InterruptProcessUserMessage(),
        RemoveQueuedMessageUserMessage(target_message_id=AgentMessageID()),
        GitCommitAndPushUserMessage(commit_message="test commit", is_pushing=False),
        GitPullUserMessage(),
        StopCheckUserMessage(check_name="test", user_message_id=AgentMessageID(), run_id=RunID()),
        RestartCheckUserMessage(check_name="test", user_message_id=AgentMessageID()),
        StopAgentUserMessage(),
        SetUserConfigurationDataUserMessage(credentials=Credentials()),
        # Persistent System Messages
        ForkAgentSystemMessage(
            parent_task_id=TaskID(), child_task_id=TaskID(), fork_point_chat_message_id=AgentMessageID()
        ),
        # Local Sync Messages (Ephemeral System)
        LocalSyncSetupStartedMessage(),
        LocalSyncSetupProgressMessage(next_step=LocalSyncSetupStep.VALIDATE_GIT_STATE_SAFETY),
        LocalSyncSetupAndEnabledMessage(),
        LocalSyncUpdatePendingMessage(event_description="test pending"),
        LocalSyncUpdateCompletedMessage(event_description="test completed", is_resumption=False),
        LocalSyncUpdatePausedMessage(
            event_description="test paused",
            pause_notices=(LocalSyncNoticeOfPause(source_tag="test", reason="test reason"),),
        ),
        LocalSyncTeardownStartedMessage(),
        LocalSyncTeardownProgressMessage(
            next_step=LocalSyncTeardownStep.STOP_FILE_SYNC,
            sync_branch="foo",
            original_branch="bar",
        ),
        LocalSyncDisabledMessage(),
        # Manual Sync Messages (Ephemeral System)
        ManualSyncMergeIntoUserAttemptedMessage(
            reached_operation_label=None,
            reached_operation_failure_label=None,
            reached_decision_label=None,
            selection_by_decision_label=None,
            target_local_branch="main",
            local_branch="feature",
        ),
        ManualSyncMergeIntoAgentAttemptedMessage(
            is_attempt_unambiguously_successful=True,
            is_merge_in_progress=False,
            labels=[ManualSyncMergeIntoAgentNoticeLabel.MERGE_COMPLETED_CLEANLY],
            source_local_branch="main",
            local_branch="feature",
        ),
        ProgressUpdateRunnerMessage(
            progress=RootProgress(
                snapshot_uncommitted_changes=MultiOperationProgress(
                    state=OperationState.NOT_STARTED,
                    progress_id=ProgressID(),
                    latest_update_time=get_current_time(),
                    operations=[],
                ),
                branch_name_and_task_title_generation=BranchNameAndTaskTitleProgress(
                    state=OperationState.NOT_STARTED,
                    progress_id=ProgressID(),
                    latest_update_time=get_current_time(),
                    operations=[],
                ),
                image_build=MultiOperationProgress(
                    state=OperationState.NOT_STARTED,
                    progress_id=ProgressID(),
                    latest_update_time=get_current_time(),
                    operations=[],
                ),
                container_setup=MultiOperationProgress(
                    state=OperationState.NOT_STARTED,
                    progress_id=ProgressID(),
                    latest_update_time=get_current_time(),
                    operations=[],
                ),
                agent_branch_checkout=MultiOperationProgress(
                    state=OperationState.NOT_STARTED,
                    progress_id=ProgressID(),
                    latest_update_time=get_current_time(),
                    operations=[],
                ),
            ),
        ),
    ]
    return {type(msg): msg for msg in messages}


def test_example_contains_every_type(example_messages_of_every_type: dict[type, MessageTypes]) -> None:
    all_message_types = extract_leaf_types(MessageTypes)
    for message_type in all_message_types:
        assert message_type in example_messages_of_every_type, (
            f"Message type {message_type} not found in example messages"
        )


def test_all_user_message_types_are_in_union(example_messages_of_every_type: dict[type, MessageTypes]) -> None:
    all_user_message_types = extract_leaf_types(UserMessageUnion)
    for message_type, message_example in example_messages_of_every_type.items():
        if message_example.source is AgentMessageSource.USER:
            assert any(isinstance(message_example, message_type) for message_type in all_user_message_types), (
                f"Message type {message_type} has source user but is not included in UserMessageUnion"
            )


def test_all_system_message_types_are_in_union(example_messages_of_every_type: dict[type, MessageTypes]) -> None:
    all_system_message_types = extract_leaf_types(SystemMessageUnion)
    for message_type, message_example in example_messages_of_every_type.items():
        if message_example.source is AgentMessageSource.SCULPTOR_SYSTEM:
            assert any(isinstance(message_example, message_type) for message_type in all_system_message_types), (
                f"Message type {message_type} has source system but is not included in SystemMessageUnion"
            )


def test_all_runner_message_types_are_in_union(example_messages_of_every_type: dict[type, MessageTypes]) -> None:
    all_runner_message_types = extract_leaf_types(RunnerMessageUnion)
    for message_type, message_example in example_messages_of_every_type.items():
        if message_example.source is AgentMessageSource.RUNNER:
            assert any(isinstance(message_example, message_type) for message_type in all_runner_message_types), (
                f"Message type {message_type} has source runner but is not included in RunnerMessageUnion"
            )


def test_all_user_union_types_have_source_user(example_messages_of_every_type: dict[type, MessageTypes]) -> None:
    all_user_message_types = extract_leaf_types(UserMessageUnion)
    for message_type in all_user_message_types:
        example_message = example_messages_of_every_type[message_type]
        assert example_message.source is AgentMessageSource.USER, (
            f"Message type {message_type} has source {example_message.source} but should have source user"
        )


def test_all_runner_union_types_have_source_runner(example_messages_of_every_type: dict[type, MessageTypes]) -> None:
    all_runner_message_types = extract_leaf_types(RunnerMessageUnion)
    for message_type in all_runner_message_types:
        example_message = example_messages_of_every_type[message_type]
        assert example_message.source is AgentMessageSource.RUNNER, (
            f"Message type {message_type} has source {example_message.source} but should have source runner"
        )


def test_all_system_union_types_have_source_system(example_messages_of_every_type: dict[type, MessageTypes]) -> None:
    all_system_message_types = extract_leaf_types(SystemMessageUnion)
    for message_type in all_system_message_types:
        example_message = example_messages_of_every_type[message_type]
        assert example_message.source is AgentMessageSource.SCULPTOR_SYSTEM, (
            f"Message type {message_type} has source {example_message.source} but should have source system"
        )
