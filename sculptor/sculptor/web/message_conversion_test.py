from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.ids import AssistantMessageID
from imbue_core.ids import ToolUseID
from imbue_core.itertools import only
from imbue_core.sculptor.state.chat_state import ChatMessage
from imbue_core.sculptor.state.chat_state import ChatMessageRole
from imbue_core.sculptor.state.chat_state import GenericToolContent
from imbue_core.sculptor.state.chat_state import TextBlock
from imbue_core.sculptor.state.chat_state import ToolResultBlock
from imbue_core.sculptor.state.chat_state import ToolUseBlock
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.serialization import SerializedException
from sculptor.interfaces.agents.agent import PartialResponseBlockAgentMessage
from sculptor.interfaces.agents.agent import RequestStartedAgentMessage
from sculptor.interfaces.agents.agent import RequestSuccessAgentMessage
from sculptor.interfaces.agents.agent import ResponseBlockAgentMessage
from sculptor.web.message_conversion import convert_agent_messages_to_task_update


def _make_serialized_exception(message: str = "boom") -> SerializedException:
    try:
        raise RuntimeError(message)
    except RuntimeError as exc:
        return SerializedException.build(exc, exc.__traceback__)


def test_convert_agent_messages_to_task_update_promotes_user_and_assistant_messages() -> None:
    task_id = TaskID()
    completed_by_id: dict[AgentMessageID, ChatMessage] = {}

    user_message = ChatInputUserMessage(
        text="Hello!",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )
    user_follow_up_message = ChatInputUserMessage(
        text="Goodbye!",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )

    state = convert_agent_messages_to_task_update(
        [user_message, user_follow_up_message],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=None,
    )

    assert state.chat_messages == ()
    assert len(state.queued_chat_messages) == 2
    queued_user, queued_user_2 = state.queued_chat_messages
    assert queued_user.role == ChatMessageRole.USER
    assert queued_user_2.role == ChatMessageRole.USER

    assistant_chat_message_id = AgentMessageID()
    assistant_message_id = AssistantMessageID("assistant-1")

    request_started = RequestStartedAgentMessage(request_id=user_message.message_id)
    response_block = ResponseBlockAgentMessage(
        role="assistant",
        assistant_message_id=assistant_message_id,
        message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're absolutely right!"),),
    )

    state = convert_agent_messages_to_task_update(
        [request_started, response_block],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    assert len(state.chat_messages) == 1
    promoted_user = state.chat_messages[0]
    assert promoted_user.id == user_message.message_id
    assert state.in_progress_user_message_id == user_message.message_id
    assert state.in_progress_chat_message is not None
    assert [block.text for block in state.in_progress_chat_message.content if isinstance(block, TextBlock)] == [
        "You're absolutely right!"
    ]
    assert len(state.queued_chat_messages) == 1
    assert state.queued_chat_messages[0].id == user_follow_up_message.message_id

    follow_up_response = ResponseBlockAgentMessage(
        role="assistant",
        assistant_message_id=assistant_message_id,
        message_id=assistant_chat_message_id,
        content=(TextBlock(text="Let me explain..."),),
    )

    state = convert_agent_messages_to_task_update(
        [follow_up_response],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    assert state.in_progress_chat_message is not None
    assert [block.text for block in state.in_progress_chat_message.content if isinstance(block, TextBlock)] == [
        "You're absolutely right!",
        "Let me explain...",
    ]

    # pyre-ignore[28]: pyre doesn't understand pydantic
    request_success = RequestSuccessAgentMessage(request_id=user_message.message_id)

    state = convert_agent_messages_to_task_update(
        [request_success],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    assert len(state.chat_messages) == 1
    assistant_reply = state.chat_messages[0]
    assert assistant_reply.role == ChatMessageRole.ASSISTANT
    assert [block.text for block in assistant_reply.content if isinstance(block, TextBlock)] == [
        "You're absolutely right!",
        "Let me explain...",
    ]
    assert len(state.queued_chat_messages) == 1
    assert state.queued_chat_messages[0].id == user_follow_up_message.message_id
    assert state.in_progress_chat_message is None
    assert state.in_progress_user_message_id is None
    assert completed_by_id[user_message.message_id].role == ChatMessageRole.USER
    assert completed_by_id[assistant_reply.id].role == ChatMessageRole.ASSISTANT


def test_convert_agent_messages_to_task_update_replaces_tool_use_with_result() -> None:
    task_id = TaskID()
    completed_by_id: dict[AgentMessageID, ChatMessage] = {}

    tool_use_id = ToolUseID("tool-use-1")
    assistant_message_id = AssistantMessageID("assistant-tool-message")
    assistant_chat_message_id = AgentMessageID()

    tool_use = ResponseBlockAgentMessage(
        role="assistant",
        assistant_message_id=assistant_message_id,
        message_id=assistant_chat_message_id,
        content=(ToolUseBlock(id=tool_use_id, name="tool", input={"command": "ls"}),),
    )

    state = convert_agent_messages_to_task_update(
        [tool_use],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=None,
    )

    assert state.chat_messages == ()
    assert state.in_progress_chat_message is not None
    content_blocks = state.in_progress_chat_message.content
    assert len(content_blocks) == 1
    assert isinstance(content_blocks[0], ToolUseBlock)

    tool_result = ResponseBlockAgentMessage(
        role="assistant",
        assistant_message_id=assistant_message_id,
        message_id=assistant_chat_message_id,
        content=(
            ToolResultBlock(
                tool_use_id=tool_use_id,
                tool_name="tool",
                invocation_string="tool('ls')",
                content=GenericToolContent(text="done"),
            ),
        ),
    )

    state = convert_agent_messages_to_task_update(
        [tool_result],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    assert state.chat_messages == ()
    assert state.in_progress_chat_message is not None
    content_blocks = state.in_progress_chat_message.content
    assert len(content_blocks) == 1
    tool_result_block = content_blocks[0]
    assert isinstance(tool_result_block, ToolResultBlock)
    tool_content = tool_result_block.content
    assert isinstance(tool_content, GenericToolContent)
    assert tool_content.text == "done"


def test_convert_agent_messages_to_task_update_handles_partial_response_blocks() -> None:
    task_id = TaskID()
    completed_by_id: dict[AgentMessageID, ChatMessage] = {}

    user_message = ChatInputUserMessage(
        text="Hello!",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )
    state = convert_agent_messages_to_task_update(
        [user_message],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=None,
    )

    assert state.chat_messages == ()
    assert len(state.queued_chat_messages) == 1

    assistant_chat_message_id = AgentMessageID()
    assistant_message_id = AssistantMessageID("assistant-1")

    request_started = RequestStartedAgentMessage(request_id=user_message.message_id)
    partial_response_block_1 = PartialResponseBlockAgentMessage(
        assistant_message_id=assistant_message_id,
        message_id=AgentMessageID(),
        first_response_message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're"),),
    )
    partial_response_block_2 = PartialResponseBlockAgentMessage(
        assistant_message_id=assistant_message_id,
        message_id=AgentMessageID(),
        first_response_message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're absolutely"),),
    )
    partial_response_block_3 = PartialResponseBlockAgentMessage(
        assistant_message_id=assistant_message_id,
        message_id=AgentMessageID(),
        first_response_message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're absolutely right!"),),
    )
    response_block = ResponseBlockAgentMessage(
        role="assistant",
        assistant_message_id=assistant_message_id,
        message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're absolutely right!"),),
    )

    state = convert_agent_messages_to_task_update(
        [
            request_started,
            partial_response_block_1,
            partial_response_block_2,
            partial_response_block_3,
            response_block,
        ],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    assert len(state.chat_messages) == 1
    promoted_user = state.chat_messages[0]
    assert promoted_user.id == user_message.message_id
    assert state.in_progress_user_message_id == user_message.message_id
    assert state.in_progress_chat_message is not None
    assert (
        only([block.text for block in state.in_progress_chat_message.content if isinstance(block, TextBlock)])
        == "You're absolutely right!"
    )

    # pyre-ignore[28]: pyre doesn't understand pydantic
    request_success = RequestSuccessAgentMessage(request_id=user_message.message_id)

    state = convert_agent_messages_to_task_update(
        [request_success],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    assert len(state.chat_messages) == 1
    assistant_reply = state.chat_messages[0]
    assert assistant_reply.role == ChatMessageRole.ASSISTANT
    assert (
        only([block.text for block in assistant_reply.content if isinstance(block, TextBlock)])
        == "You're absolutely right!"
    )


def test_convert_agent_messages_to_task_update_provides_stable_chat_message_id() -> None:
    task_id = TaskID()
    completed_by_id: dict[AgentMessageID, ChatMessage] = {}

    user_message = ChatInputUserMessage(
        text="Hello!",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )
    state = convert_agent_messages_to_task_update(
        [user_message],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=None,
    )

    assert state.chat_messages == ()
    assert len(state.queued_chat_messages) == 1

    # This is the persistent ID that will be used for the ChatMessage and the first ResponseBlockAgentMessage
    assistant_chat_message_id = AgentMessageID()
    assistant_message_id = AssistantMessageID("assistant-1")

    request_started = RequestStartedAgentMessage(request_id=user_message.message_id)
    partial_response_block_1 = PartialResponseBlockAgentMessage(
        assistant_message_id=assistant_message_id,
        message_id=AgentMessageID(),  # Ephemeral, unique per partial
        first_response_message_id=assistant_chat_message_id,  # Persistent, same for all partials
        content=(TextBlock(text="You're"),),
    )

    state = convert_agent_messages_to_task_update(
        [request_started, partial_response_block_1],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    in_progress_msg = state.in_progress_chat_message
    assert in_progress_msg is not None
    assert in_progress_msg.content == (TextBlock(text="You're"),)
    initial_id = in_progress_msg.id
    # The initial ID should be the persistent first_response_message_id
    assert initial_id == assistant_chat_message_id

    partial_response_block_2 = PartialResponseBlockAgentMessage(
        assistant_message_id=assistant_message_id,
        message_id=AgentMessageID(),
        first_response_message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're absolutely"),),
    )
    state = convert_agent_messages_to_task_update(
        [partial_response_block_2],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )
    in_progress_msg = state.in_progress_chat_message
    assert in_progress_msg is not None
    assert in_progress_msg.content == (TextBlock(text="You're absolutely"),)
    assert in_progress_msg.id == initial_id

    partial_response_block_3 = PartialResponseBlockAgentMessage(
        assistant_message_id=assistant_message_id,
        message_id=AgentMessageID(),
        first_response_message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're absolutely right!"),),
    )

    state = convert_agent_messages_to_task_update(
        [partial_response_block_3],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )
    in_progress_msg = state.in_progress_chat_message
    assert in_progress_msg is not None
    assert in_progress_msg.content == (TextBlock(text="You're absolutely right!"),)
    assert in_progress_msg.id == initial_id

    response_block = ResponseBlockAgentMessage(
        role="assistant",
        assistant_message_id=assistant_message_id,
        message_id=assistant_chat_message_id,
        content=(TextBlock(text="You're absolutely right!"),),
    )
    state = convert_agent_messages_to_task_update(
        [response_block],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )
    in_progress_msg = state.in_progress_chat_message
    assert in_progress_msg is not None
    assert in_progress_msg.content == (TextBlock(text="You're absolutely right!"),)
    assert in_progress_msg.id == initial_id

    # pyre-ignore[28]: pyre doesn't understand pydantic
    request_success = RequestSuccessAgentMessage(request_id=user_message.message_id)

    state = convert_agent_messages_to_task_update(
        [request_success],
        task_id=task_id,
        completed_message_by_id=completed_by_id,
        current_state=state,
    )

    assert len(state.chat_messages) == 1
    assistant_reply = state.chat_messages[0]
    assert assistant_reply.role == ChatMessageRole.ASSISTANT
    assert (
        only([block.text for block in assistant_reply.content if isinstance(block, TextBlock)])
        == "You're absolutely right!"
    )
    assert assistant_reply.id == initial_id
