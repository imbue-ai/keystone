"""Converts agent messages to chat messages for the frontend."""

from typing import Sequence

from loguru import logger

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.sculptor.state.chat_state import ChatMessage
from imbue_core.sculptor.state.chat_state import ChatMessageRole
from imbue_core.sculptor.state.chat_state import CommandBlock
from imbue_core.sculptor.state.chat_state import ContentBlockTypes
from imbue_core.sculptor.state.chat_state import ContextSummaryBlock
from imbue_core.sculptor.state.chat_state import ErrorBlock
from imbue_core.sculptor.state.chat_state import FileBlock
from imbue_core.sculptor.state.chat_state import ForkedFromBlock
from imbue_core.sculptor.state.chat_state import ForkedToBlock
from imbue_core.sculptor.state.chat_state import ResumeResponseBlock
from imbue_core.sculptor.state.chat_state import TextBlock
from imbue_core.sculptor.state.chat_state import ToolResultBlock
from imbue_core.sculptor.state.chat_state import ToolUseBlock
from imbue_core.sculptor.state.chat_state import WarningBlock
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.state.messages import ResponseBlockAgentMessage
from imbue_core.serialization import SerializedException
from sculptor.interfaces.agents.agent import AgentCrashedRunnerMessage
from sculptor.interfaces.agents.agent import AgentSnapshotFailureRunnerMessage
from sculptor.interfaces.agents.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.agents.agent import CheckFinishedRunnerMessage
from sculptor.interfaces.agents.agent import CheckLaunchedRunnerMessage
from sculptor.interfaces.agents.agent import CheckOutputRunnerMessage
from sculptor.interfaces.agents.agent import ChecksDefinedRunnerMessage
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.agents.agent import ContextSummaryMessage
from sculptor.interfaces.agents.agent import EnvironmentCrashedRunnerMessage
from sculptor.interfaces.agents.agent import ErrorMessage
from sculptor.interfaces.agents.agent import ErrorMessageUnion
from sculptor.interfaces.agents.agent import ForkAgentSystemMessage
from sculptor.interfaces.agents.agent import MessageFeedbackUserMessage
from sculptor.interfaces.agents.agent import PartialResponseBlockAgentMessage
from sculptor.interfaces.agents.agent import ProgressUpdateRunnerMessage
from sculptor.interfaces.agents.agent import RemoveQueuedMessageAgentMessage
from sculptor.interfaces.agents.agent import RequestFailureAgentMessage
from sculptor.interfaces.agents.agent import RequestStartedAgentMessage
from sculptor.interfaces.agents.agent import RequestSuccessAgentMessage
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.agent import StreamingMessageCompleteAgentMessage
from sculptor.interfaces.agents.agent import UnexpectedErrorRunnerMessage
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.agent import UserCommandFailureAgentMessage
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.agents.agent import WarningMessage
from sculptor.interfaces.agents.agent import WarningRunnerMessage
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.services.data_model_service.api import CompletedTransaction
from sculptor.web.derived import InsertedChatMessage
from sculptor.web.derived import TaskUpdate

# Message type groups
ERROR_MESSAGE_TYPES = (
    EnvironmentCrashedRunnerMessage,
    UnexpectedErrorRunnerMessage,
    AgentCrashedRunnerMessage,
    UserCommandFailureAgentMessage,
)

WARNING_MESSAGE_TYPES = (
    WarningAgentMessage,
    WarningRunnerMessage,
)


def convert_agent_messages_to_task_update(
    new_messages: Sequence[Message | CompletedTransaction | dict],
    task_id: TaskID,
    completed_message_by_id: dict[AgentMessageID, ChatMessage],
    current_state: TaskUpdate | None = None,
) -> TaskUpdate:
    """Convert a batch of agent messages to a TaskUpdate.

    Takes a stream of agent messages and converts them into a TaskUpdate
    with pure UI state that can be displayed in the frontend. Manages the state
    transitions of messages from queued -> completed and builds up assistant messages
    incrementally.
    """

    # TODO: really this should be renamed to "updated_messages"
    completed_chat_messages = []
    queued_chat_messages = list(current_state.queued_chat_messages) if current_state else []
    in_progress_chat_message = current_state.in_progress_chat_message if current_state else None
    current_request_id = current_state.in_progress_user_message_id if current_state else None
    update_artifacts = set()
    finished_request_ids = []
    logs = []
    check_update_messages = []
    new_check_output_messages = []
    inserted_messages = []
    feedback_by_message_id = dict(current_state.feedback_by_message_id) if current_state else {}
    progress = current_state.progress if current_state is not None else None

    # in_progress_chat_message.content[streaming_start_index:] are the content blocks that we will alter
    if current_state:
        streaming_start_index = current_state.streaming_start_index
    elif in_progress_chat_message:
        streaming_start_index = len(in_progress_chat_message.content)
    else:
        streaming_start_index = 0

    # We initialize is_streaming_active to False by default, but we'll turn it on if we see a PartialResponseBlockAgentMessage during processing.
    is_streaming_active = False
    if current_state:
        is_streaming_active = current_state.is_streaming_active

    for msg in new_messages:
        if isinstance(msg, ChatInputUserMessage):
            # Build content blocks from text and files
            content_blocks: list[ContentBlockTypes] = [TextBlock(text=msg.text)]
            for file in msg.files:
                content_blocks.append(FileBlock(source=file))

            # Queue user message until confirmed
            queued_chat_messages.append(
                ChatMessage(
                    id=msg.message_id,
                    role=ChatMessageRole.USER,
                    content=tuple(content_blocks),
                )
            )

        elif isinstance(msg, CommandInputUserMessage):
            queued_chat_messages.append(
                ChatMessage(
                    id=msg.message_id,
                    role=ChatMessageRole.USER,
                    content=(CommandBlock(command=msg.text, is_automated=msg.is_automated_command),),
                )
            )

        elif isinstance(msg, RequestStartedAgentMessage):
            # Promote queued message to completed
            for i, message in enumerate(queued_chat_messages):
                assert isinstance(msg.request_id, AgentMessageID)
                if message.id == msg.request_id:
                    previously_queued_message = queued_chat_messages.pop(i)
                    completed_message_by_id[previously_queued_message.id] = previously_queued_message
                    completed_chat_messages.append(previously_queued_message)
                    current_request_id = msg.request_id
                    break

        elif isinstance(msg, RemoveQueuedMessageAgentMessage):
            # Remove queued message without completing it
            queued_chat_messages = [m for m in queued_chat_messages if m.id != msg.removed_message_id]

        elif isinstance(msg, PartialResponseBlockAgentMessage):
            # First partial in a turn establishes where streaming edits begin
            if not is_streaming_active:
                streaming_start_index = len(in_progress_chat_message.content) if in_progress_chat_message else 0
            is_streaming_active = True
            # Handle streaming partial - replace content from streaming_start_index.
            # Use first_response_message_id for the ChatMessage ID so it's stable AND persistent.
            in_progress_chat_message = _handle_partial_response(
                in_progress_chat_message, msg.content, msg.first_response_message_id, streaming_start_index
            )

        elif isinstance(msg, ResponseBlockAgentMessage):
            if is_streaming_active:
                continue
            # Non-streaming (or historical replay) - append content as usual
            in_progress_chat_message = _handle_response_blocks(in_progress_chat_message, msg.content, msg.message_id)

        elif isinstance(msg, StreamingMessageCompleteAgentMessage):
            streaming_start_index = len(in_progress_chat_message.content) if in_progress_chat_message else 0
            is_streaming_active = False

        elif isinstance(msg, ResumeAgentResponseRunnerMessage):
            # add a block to indicate that we are resuming
            in_progress_chat_message = _handle_response_blocks(
                in_progress_chat_message, (ResumeResponseBlock(),), msg.message_id
            )

        elif isinstance(msg, ContextSummaryMessage):
            in_progress_chat_message = _add_context_summary_to_message(in_progress_chat_message, msg)
            completed_message_by_id[in_progress_chat_message.id] = in_progress_chat_message
            completed_chat_messages.append(in_progress_chat_message)
            in_progress_chat_message = None

        elif isinstance(msg, RequestSuccessAgentMessage):
            # Finalize assistant message when ready
            if current_request_id and msg.request_id == current_request_id and in_progress_chat_message:
                completed_message_by_id[in_progress_chat_message.id] = in_progress_chat_message
                completed_chat_messages.append(in_progress_chat_message)
                in_progress_chat_message = None
                current_request_id = None

        elif isinstance(msg, RequestFailureAgentMessage):
            # Add error block to assistant message
            in_progress_chat_message = _add_error_to_message(in_progress_chat_message, msg)
            # Finalize assistant message when ready
            if current_request_id and msg.request_id == current_request_id and in_progress_chat_message:
                completed_message_by_id[in_progress_chat_message.id] = in_progress_chat_message
                completed_chat_messages.append(in_progress_chat_message)
                in_progress_chat_message = None
                current_request_id = None

        elif isinstance(msg, ForkAgentSystemMessage):
            if msg.parent_task_id == task_id:
                _insert_forked_to_block(inserted_messages, msg)
            # This could be a fork from another task, or a nested fork. Either way, show the "forked from" block.
            else:
                _insert_forked_from_block(inserted_messages, msg)

        elif isinstance(msg, ERROR_MESSAGE_TYPES):
            # Add error block to assistant message
            if in_progress_chat_message is not None:
                in_progress_chat_message = _add_error_to_message(in_progress_chat_message, msg)
            else:
                new_message = _add_error_to_message(in_progress_chat_message, msg)
                completed_message_by_id[new_message.id] = new_message
                completed_chat_messages.append(new_message)

        elif isinstance(msg, WARNING_MESSAGE_TYPES):
            # Add warning block to assistant message
            if in_progress_chat_message is not None:
                in_progress_chat_message = _add_warning_to_message(in_progress_chat_message, msg)
            else:
                new_message = _add_warning_to_message(in_progress_chat_message, msg)
                completed_message_by_id[new_message.id] = new_message
                completed_chat_messages.append(new_message)

        elif isinstance(msg, UpdatedArtifactAgentMessage):
            artifact_type = ArtifactType(msg.artifact.name)
            if artifact_type:
                update_artifacts.add(artifact_type)

        # set the snapshot id when it happens
        elif isinstance(msg, AgentSnapshotRunnerMessage):
            # only need to update if there is a message to update
            if msg.for_user_message_id and msg.for_user_message_id in completed_message_by_id:
                # TODO: we may want to think a little bit harder about how to deal with skipped messages in general
                prev_message = completed_message_by_id[msg.for_user_message_id]
                msg_image = msg.image
                if msg_image is not None:
                    prev_message = prev_message.evolve(prev_message.ref().snapshot_id, msg_image.image_id)
                completed_message_by_id[prev_message.id] = prev_message
                completed_chat_messages.append(prev_message)

        elif isinstance(msg, AgentSnapshotFailureRunnerMessage):
            # only need to update if there is a message to update
            if msg.for_user_message_id and msg.for_user_message_id in completed_message_by_id:
                prev_message = completed_message_by_id[msg.for_user_message_id]
                prev_message = prev_message.evolve(prev_message.ref().did_snapshot_fail, True)
                completed_message_by_id[prev_message.id] = prev_message
                completed_chat_messages.append(prev_message)

        # Handle build log messages
        elif isinstance(msg, dict):
            logs.append(_reformat_log(msg["text"]))

        # Track completed requests
        elif isinstance(msg, CompletedTransaction):
            if msg.request_id:
                finished_request_ids.append(msg.request_id)

        # handle messages for when the check was started, stopped, or defined
        # (and include container status messages, which affect local checks)
        elif isinstance(
            msg,
            (
                CheckLaunchedRunnerMessage,
                CheckFinishedRunnerMessage,
                ChecksDefinedRunnerMessage,
            ),
        ):
            check_update_messages.append(msg)

        elif isinstance(msg, CheckOutputRunnerMessage):
            new_check_output_messages.append(msg)

        elif isinstance(msg, MessageFeedbackUserMessage):
            # Track feedback by message ID, or remove if feedback_type is "none"
            if msg.feedback_type == "none":
                feedback_by_message_id.pop(str(msg.feedback_message_id), None)
            else:
                feedback_by_message_id[str(msg.feedback_message_id)] = msg.feedback_type

        elif isinstance(msg, ProgressUpdateRunnerMessage):
            progress = msg.progress

    # Build final update
    return TaskUpdate(
        task_id=task_id,
        chat_messages=tuple(completed_chat_messages),
        in_progress_chat_message=in_progress_chat_message,
        queued_chat_messages=tuple(queued_chat_messages),
        updated_artifacts=tuple(update_artifacts),
        finished_request_ids=tuple(finished_request_ids),
        logs=tuple(logs),
        in_progress_user_message_id=current_request_id,
        check_update_messages=tuple(check_update_messages),
        new_check_output_messages=tuple(new_check_output_messages),
        inserted_messages=tuple(inserted_messages),
        feedback_by_message_id=feedback_by_message_id,
        streaming_start_index=streaming_start_index,
        is_streaming_active=is_streaming_active,
        progress=progress,
    )


def _create_empty_assistant_message(chat_message_id: AgentMessageID) -> ChatMessage:
    """Create a new empty assistant message."""
    return ChatMessage(
        id=chat_message_id,
        role=ChatMessageRole.ASSISTANT,
        content=(),
    )


def _handle_response_blocks(
    in_progress: ChatMessage | None, blocks: tuple[ContentBlockTypes, ...], agent_message_id: AgentMessageID
) -> ChatMessage:
    """Process response blocks, returns the updated in-progress chat message.

    Handles both text/tool use blocks (append) and tool result blocks
    (replace matching tool use or append if no match).
    """
    if not in_progress:
        in_progress = _create_empty_assistant_message(chat_message_id=agent_message_id)

    content = list(in_progress.content)

    for block in blocks:
        if isinstance(block, (TextBlock, ToolUseBlock)):
            content.append(block)
        elif isinstance(block, ToolResultBlock):
            # Try to replace matching tool use with result
            content, replaced = _replace_tool_use_with_result(content, block)

            # TODO CODEX: Clean this tool use/result logic up
            # assert replaced, "No tool use found for result"
            if not replaced:
                content.append(block)

    return in_progress.model_copy(update={"content": tuple(content)})


def _replace_tool_use_with_result(content: list, result: ToolResultBlock) -> tuple[list, bool]:
    """Try to replace a tool use block with its result.

    Returns (updated_content, was_replaced).
    """
    for i, block in enumerate(content):
        if isinstance(block, ToolUseBlock) and block.id == result.tool_use_id:
            content[i] = result
            return content, True
    return content, False


def _handle_partial_response(
    in_progress: ChatMessage | None,
    content: tuple[ContentBlockTypes, ...],
    message_id: AgentMessageID,
    streaming_start_index: int,
) -> ChatMessage:
    """Handle streaming partial - replace content from streaming_start_index."""
    if not in_progress:
        in_progress = _create_empty_assistant_message(chat_message_id=message_id)

    # Replace content from streaming_start_index onwards
    committed_content = in_progress.content[:streaming_start_index]
    new_content = committed_content + content

    return in_progress.model_copy(update={"content": new_content})


def _add_context_summary_to_message(
    in_progress: ChatMessage | None,
    message: ContextSummaryMessage,
) -> ChatMessage:
    """Add error block to message."""
    # although all elements of `ContextSummaryMessage` are `Message`s, pyre doesn't play nice with pydantic
    assert isinstance(message, Message)

    context_summary_block = ContextSummaryBlock(
        text=message.content,
    )

    return _add_system_block_to_message(in_progress, context_summary_block, chat_message_id=message.message_id)


def _insert_forked_to_block(
    inserted_messages: list[InsertedChatMessage],
    message: ForkAgentSystemMessage,
) -> None:
    """Add forked to block to message."""
    # although all elements of `ForkAgentSystemMessage` are `Message`s, pyre doesn't play nice with pydantic
    assert isinstance(message, Message)
    forked_to_block = ForkedToBlock(forked_to_task_id=message.child_task_id)
    new_message = _create_empty_assistant_message(chat_message_id=message.message_id)
    new_message = new_message.model_copy(update={"content": (forked_to_block,)})
    inserted_messages.append(
        InsertedChatMessage(message=new_message, after_message_id=message.fork_point_chat_message_id)
    )


def _insert_forked_from_block(
    inserted_messages: list[InsertedChatMessage],
    message: ForkAgentSystemMessage,
) -> None:
    """Add forked from block to message."""
    # although all elements of `ForkAgentSystemMessage` are `Message`s, pyre doesn't play nice with pydantic
    assert isinstance(message, Message)
    forked_from_block = ForkedFromBlock(forked_from_task_id=message.parent_task_id)
    new_message = _create_empty_assistant_message(chat_message_id=message.message_id)
    new_message = new_message.model_copy(update={"content": (forked_from_block,)})
    inserted_messages.append(
        InsertedChatMessage(message=new_message, after_message_id=message.fork_point_chat_message_id)
    )


def _add_error_to_message(
    in_progress: ChatMessage | None,
    message: ErrorMessageUnion,
) -> ChatMessage:
    """Add error block to message."""
    # although all elements of `ErrorMessageUnion` are `ErrorMessage`s, pyre doesn't play nice with pydantic, so we do the assert to make it understand message's attributes
    assert isinstance(message, ErrorMessage)
    error = message.error
    chat_message_id = message.message_id
    if not isinstance(error, SerializedException):
        logger.error("Expected SerializedException, got {}", type(message.error))
        return in_progress or _create_empty_assistant_message(chat_message_id=chat_message_id)

    args = message.error.args
    message_text = args[0] if args and isinstance(args[0], str) else f"{message.error}"
    error_block = ErrorBlock(
        message=message_text,
        traceback=message.error.as_formatted_traceback(),
        error_type=message.error.exception,
    )

    return _add_system_block_to_message(in_progress=in_progress, block=error_block, chat_message_id=chat_message_id)


def _add_warning_to_message(in_progress: ChatMessage | None, message: WarningMessage) -> ChatMessage:
    """Add warning block to message."""
    traceback = None
    warning_type = None

    # although WarningMessage is a Message, pyre doesn't play nice with pydantic, so we do the assert to make it understand message's attributes
    assert isinstance(message, Message)
    error = message.error

    if isinstance(error, SerializedException):
        traceback = error.as_formatted_traceback()
        warning_type = error.exception

    warning_block = WarningBlock(
        message=message.message,
        traceback=traceback,
        warning_type=warning_type,
    )

    return _add_system_block_to_message(
        in_progress=in_progress, block=warning_block, chat_message_id=message.message_id
    )


def _add_system_block_to_message(
    in_progress: ChatMessage | None, block: ContentBlockTypes, chat_message_id: AgentMessageID
) -> ChatMessage:
    """Add any system block (error/warning) to message."""
    if not in_progress:
        in_progress = _create_empty_assistant_message(chat_message_id=chat_message_id)

    return in_progress.model_copy(update={"content": in_progress.content + (block,)})


def _reformat_log(log: str) -> str:
    """Reformat log line for display."""
    try:
        timestamp, level, rest = log.split("|", 2)
        _, useful = rest.split("- ", 1)
        return f"{timestamp}|{level}| {useful.strip()}"
    except ValueError:
        # If log format is unexpected, return as-is
        return log
