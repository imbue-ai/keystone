from queue import Queue
from typing import Annotated
from typing import TypeGuard
from typing import get_args
from typing import get_origin

from loguru import logger

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.sculptor.state.messages import Message
from imbue_core.serialization import SerializedException
from sculptor.agents.default.artifact_creation import get_file_artifact_messages
from sculptor.agents.default.claude_code_sdk.utils import get_warning_message
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import RequestFailureAgentMessage
from sculptor.interfaces.agents.agent import RequestStoppedAgentMessage
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.agent import UserMessageUnion
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.interfaces.environments.base import Environment
from sculptor.tasks.handlers.run_agent.errors import GitCommandFailure
from sculptor.tasks.handlers.run_agent.git import run_git_command_in_environment


def _get_user_message_union_types() -> tuple[type, ...]:
    """Extract all concrete types from UserMessageUnion for isinstance() checks."""

    union_args = get_args(UserMessageUnion)
    actual_types = []

    for arg in union_args:
        # Handle Annotated types (e.g., Annotated[ChatInputUserMessage, Tag("ChatInputUserMessage")])
        if get_origin(arg) is Annotated:
            actual_types.append(get_args(arg)[0])
        else:
            actual_types.append(arg)

    return tuple(actual_types)


def is_user_message(message: Message) -> TypeGuard[UserMessageUnion]:
    return isinstance(message, _get_user_message_union_types())


def on_git_user_message(
    environment: Environment,
    command: list[str],
    source_branch: str,
    output_message_queue: Queue[Message],
    task_id: TaskID,
) -> None:
    try:
        logger.info("Running git command: {}", " ".join(command))
        run_git_command_in_environment(
            environment=environment,
            command=command,
            secrets={},
            cwd=str(environment.get_workspace_path()),
            is_retry_safe=True,
            timeout=30.0,
        )
    except GitCommandFailure as e:
        output_message_queue.put(
            get_warning_message(
                f"Failed to run git command {command} - stderr: {e.stderr}",
                e,
                task_id,
            )
        )
    logger.info("Received git user message, updating artifacts")
    messages_to_send = get_file_artifact_messages(
        artifact_name=ArtifactType.DIFF,
        environment=environment,
        source_branch=source_branch,
        task_id=task_id,
    )
    for artifact_message in messages_to_send:
        output_message_queue.put(artifact_message)


def serialize_agent_wrapper_error(
    e: Exception, message: UserMessageUnion, is_stopping: bool
) -> RequestStoppedAgentMessage | RequestFailureAgentMessage:
    serialized_exception = SerializedException.build(e)
    message_type = RequestStoppedAgentMessage if is_stopping else RequestFailureAgentMessage
    # TODO: make pyre understand inheritance in pydantic so it understands that request_id exists
    return message_type(  # pyre-fixme[28]
        message_id=AgentMessageID(),
        request_id=message.message_id,
        error=serialized_exception,
    )


def stream_token_and_cost_info(
    environment: Environment,
    source_branch: str,
    output_message_queue: Queue[Message],
    task_id: TaskID,
) -> None:
    # we should send token and cost info:
    artifact_messages_to_send: list[UpdatedArtifactAgentMessage | WarningAgentMessage] = []
    artifact_messages_to_send.extend(
        get_file_artifact_messages(
            artifact_name=ArtifactType.USAGE,
            environment=environment,
            source_branch=source_branch,
            task_id=task_id,
        )
    )
    for artifact_message in artifact_messages_to_send:
        if artifact_message is not None:
            output_message_queue.put(artifact_message)

    logger.debug("Stream ended")  # process should be done by now, but we'll wait for it to be sure
