import json
import uuid
from queue import Empty
from queue import Queue

from loguru import logger

from imbue_core.agents.agent_api.data_types import AgentToolName
from imbue_core.ids import ToolUseID
from imbue_core.processes.local_process import RunningProcess
from imbue_core.sculptor.state.chat_state import DiffToolContent
from imbue_core.sculptor.state.chat_state import GenericToolContent
from imbue_core.sculptor.state.chat_state import TextBlock
from imbue_core.sculptor.state.chat_state import ToolResultBlock
from imbue_core.sculptor.state.chat_state import ToolUseBlock
from imbue_core.sculptor.state.claude_state import ParsedAssistantResponse
from imbue_core.sculptor.state.claude_state import ParsedEndResponse
from imbue_core.sculptor.state.claude_state import ParsedInitResponse
from imbue_core.sculptor.state.claude_state import ParsedToolResultResponse
from imbue_core.sculptor.state.claude_state import RE_STRIP_ANSI_ESCAPE
from imbue_core.sculptor.state.messages import AssistantMessageID
from imbue_core.serialization import SerializedException
from sculptor.agents.default.artifact_creation import get_file_artifact_messages
from sculptor.agents.default.artifact_creation import should_send_diff_and_branch_name_artifacts
from sculptor.agents.default.claude_code_sdk.diff_tracker import DiffTracker
from sculptor.agents.default.codex.errors import CodexJsonDecodeError
from sculptor.agents.default.codex.errors import InconsistentSessionError
from sculptor.agents.default.constants import SESSION_ID_STATE_FILE
from sculptor.agents.default.constants import TOKEN_AND_COST_STATE_FILE
from sculptor.agents.default.posthog_utils import emit_posthog_event_for_agent_message
from sculptor.agents.default.utils import stream_token_and_cost_info
from sculptor.database.models import AgentMessageID
from sculptor.interfaces.agents.agent import Message
from sculptor.interfaces.agents.agent import ParsedAgentResponseType
from sculptor.interfaces.agents.agent import ResponseBlockAgentMessage
from sculptor.interfaces.agents.agent import StreamingStderrAgentMessage
from sculptor.interfaces.agents.agent import TaskID
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.interfaces.environments.base import Environment


class CodexOutputProcessor:
    def __init__(
        self,
        process: RunningProcess,
        source_command: str,
        output_message_queue: Queue[Message],
        environment: Environment,
        diff_tracker: DiffTracker | None,
        source_branch: str,
        task_id: TaskID,
    ):
        self.process = process
        self.source_command = source_command
        self.output_message_queue = output_message_queue
        self.environment = environment
        self.diff_tracker = diff_tracker
        self.source_branch = source_branch
        self.task_id = task_id
        self.tool_input_by_id = {}

        self.queue = self.process.get_queue()
        self.current_message_id: AssistantMessageID | None = None
        self.last_assistant_message: ResponseBlockAgentMessage | None = None
        self.found_final_message = False

    def process_output(self) -> bool:
        self.queue = self.process.get_queue()
        self.current_message_id = None
        self.last_assistant_message = None
        self.found_final_message = False
        while not self.process.is_finished() or not self.queue.empty():
            try:
                line, is_stdout = self.queue.get(timeout=0.1)
            except Empty:
                continue
            if not line.strip():
                continue
            if not is_stdout:
                self.output_message_queue.put(
                    StreamingStderrAgentMessage(
                        stderr_line=line.strip(),
                        message_id=AgentMessageID(),
                        metadata={"source_command": self.source_command},
                    )
                )
                continue
            logger.trace("Received line from process: {}", line.strip())
            try:
                result = parse_codex_json_lines(line, self.diff_tracker)
            except json.JSONDecodeError as e:
                # NOTE: sometimes the claude -p will return the following message:
                # "This error originated either by throwing inside of an async function without a catch block,
                # or by rejecting a promise which was not handled with .catch(). The promise rejected with the reason:"
                # this does not seem to be our fault and might be a claude bug.
                # NOTE (update): we have not seen the above bug in like a week so maybe it has gone away
                raise CodexJsonDecodeError(
                    f"JSON decode error from Codex line: {line}\nstdout: {self.process.read_stdout()}\nstderr: {self.process.read_stderr()}",
                ) from e

            if result is None:
                continue

            emit_posthog_event_for_agent_message(self.task_id, result)

            if isinstance(result, ParsedInitResponse):
                self._parse_init_response(result)

            elif isinstance(result, ParsedEndResponse):
                self._parse_stream_end_response(result)

            elif isinstance(result, ParsedAssistantResponse):
                self._parse_assistant_response(result)

            elif isinstance(result, ParsedToolResultResponse):
                self._parse_tool_result_response(result)

        logger.debug("Process stream ended")

        return self.found_final_message

    def _parse_init_response(self, result: ParsedInitResponse) -> None:
        session_id = result.session_id
        session_file_path = self.environment.get_state_path() / SESSION_ID_STATE_FILE
        if self.environment.exists(path=str(session_file_path)):
            current_session_id = self.environment.read_file(path=str(session_file_path)).strip()
            if current_session_id == session_id:
                logger.debug("Written session ID matches found session ID, skipping write")
                return
            else:
                try:
                    raise InconsistentSessionError(
                        f"Mismatching state file session ID and agent session ID: {current_session_id}, {session_id}"
                    )
                except InconsistentSessionError as e:
                    self.output_message_queue.put(
                        WarningAgentMessage(
                            message_id=AgentMessageID(),
                            error=SerializedException.build(e),
                            message="Inconsistent container state detected, updating session.",
                        )
                    )
        self.environment.write_file(path=str(session_file_path), content=session_id)

    def _parse_stream_end_response(self, result: ParsedEndResponse) -> None:
        logger.debug("Stream ended")
        if result.total_tokens is not None:
            update_codex_token_state(
                environment=self.environment,
                source_branch=self.source_branch,
                output_message_queue=self.output_message_queue,
                task_id=self.task_id,
                cumulative_tokens=result.total_tokens,
            )

        # sigh. I saw this take just a tiny bit more than 5 seconds on modal once :(
        self.process.wait(timeout=10.0)
        self.found_final_message = True

    def _parse_assistant_response(self, result: ParsedAssistantResponse) -> None:
        new_message_id = result.message_id
        new_blocks = result.content_blocks

        # Track tool names and file paths from ToolUseBlocks
        for block in new_blocks:
            if isinstance(block, ToolUseBlock):
                self.tool_input_by_id[block.id] = block.input

        logger.debug("Streaming new assistant message {}", new_message_id)
        logger.trace("New blocks: {}", new_blocks)
        self.current_message_id = new_message_id
        self.last_assistant_message = ResponseBlockAgentMessage(
            role="assistant",
            message_id=AgentMessageID(),
            assistant_message_id=AssistantMessageID(new_message_id),
            content=tuple(new_blocks),
        )
        self.output_message_queue.put(self.last_assistant_message)

    def _parse_tool_result_response(self, result: ParsedToolResultResponse) -> None:
        # Add tool results to current assistant message
        new_blocks = list(result.content_blocks)
        logger.debug("Adding tool result to assistant message")
        logger.debug("{} new blocks", len(new_blocks))
        logger.trace("New blocks: {}", new_blocks)
        will_send_diff_and_branch_name_artifacts = False
        plan_artifact_info = None
        suggestions_artifact_info = None
        for block in new_blocks:
            assert isinstance(block, ToolResultBlock)
            tool_info = self.tool_input_by_id.get(block.tool_use_id, None)
            if not will_send_diff_and_branch_name_artifacts:
                tool_name = block.tool_name
                will_send_diff_and_branch_name_artifacts = should_send_diff_and_branch_name_artifacts(
                    tool_name, tool_info
                )
            # TODO CODEX: set up plans and suggestions
            # plan_artifact_info = (tool_input, block) if should_send_plan_artifact(tool_name) else None
            # suggestions_artifact_info = (
            #     (tool_input, block) if should_send_suggestions_artifact(tool_name) else None
            # )

        self.last_assistant_message = ResponseBlockAgentMessage(
            role="assistant",
            message_id=AgentMessageID(),
            assistant_message_id=AssistantMessageID(self.current_message_id),
            content=tuple(new_blocks),
        )
        self.output_message_queue.put(self.last_assistant_message)
        artifact_messages_to_send: list[UpdatedArtifactAgentMessage | WarningAgentMessage] = []

        if will_send_diff_and_branch_name_artifacts:
            logger.info("Contents of message indicate likely git state change, updating artifacts")
            artifact_messages_to_send.extend(
                get_file_artifact_messages(
                    artifact_name=ArtifactType.DIFF,
                    environment=self.environment,
                    source_branch=self.source_branch,
                    task_id=self.task_id,
                )
            )

        if plan_artifact_info:
            tool_input, tool_result = plan_artifact_info
            artifact_messages_to_send.extend(
                get_file_artifact_messages(
                    artifact_name=ArtifactType.PLAN,
                    environment=self.environment,
                    source_branch=self.source_branch,
                    tool_input=tool_input,
                    task_id=self.task_id,
                )
            )

        if suggestions_artifact_info:
            tool_input, tool_result = suggestions_artifact_info
            artifact_messages_to_send.extend(
                get_file_artifact_messages(
                    artifact_name=ArtifactType.SUGGESTIONS,
                    environment=self.environment,
                    source_branch=self.source_branch,
                    tool_input=tool_input,
                    tool_result=tool_result,
                    task_id=self.task_id,
                )
            )

        for artifact_message in artifact_messages_to_send:
            if artifact_message is not None:
                self.output_message_queue.put(artifact_message)


def parse_codex_json_lines(
    line: str,
    diff_tracker: DiffTracker | None = None,
) -> ParsedAgentResponseType | None:
    """Parse a JSON line from Codex.

    Returns a ParsedAgentMessage subtype or None for unknown message types.
    Includes full parsing of tool results, including DiffToolContent.

    Raises
        json.JSONDecodeError: If the line is not valid JSON.
        Other exceptions such as AssertionError
    """
    line = RE_STRIP_ANSI_ESCAPE.sub("", line).strip()
    logger.info("Parsing line: {}", line)

    if line == "":
        return None

    data = json.loads(line)

    item_type = data.get("type")
    if item_type == "thread.started":
        session_id = data.get("thread_id")
        return ParsedInitResponse(session_id=session_id)
    if item_type == "turn.completed":
        usage = data.get("usage")
        input_tokens = usage.get("input_tokens", 0)
        cached_input_tokens = usage.get("cached_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens - cached_input_tokens + output_tokens
        logger.info(
            "Returning stream end response with total tokens of {}",
            total_tokens,
        )
        return ParsedEndResponse(total_tokens=total_tokens)
    item_info = data.get("item")
    if item_info is None:
        return None

    message_type = item_info.get("type")
    if message_type == "agent_message" or message_type == "reasoning":
        message_text = item_info.get("text")
        # TODO CODEX: evaluate why we need this
        random_message_id = uuid.uuid4().hex
        # Manually append a newline on the text block since codex allows for multiple consecutive reasoning blocks
        # Instead of appending a newline between all text blocks, append one here in case it's relevant to streaming
        content_blocks = [TextBlock(text=message_text + "\n\n")]
        return ParsedAssistantResponse(message_id=random_message_id, content_blocks=content_blocks)  # pyre-ignore[6]
    elif message_type == "command_execution":
        random_message_id = uuid.uuid4().hex
        if item_type == "item.started":
            content_blocks = [
                ToolUseBlock(
                    id=item_info.get("id"),
                    name="command",
                    input={"command": item_info.get("command", "")},
                )
            ]
            # Note: content_blocks is a subset of ContentBlockTypes, but pyre isn't smart enough to know that this is ok, hence the pyre ignore
            return ParsedAssistantResponse(
                message_id=AssistantMessageID(random_message_id),
                content_blocks=content_blocks,  # pyre-ignore[6]
            )
        elif item_type == "item.completed":
            content_blocks = [
                ToolResultBlock(
                    tool_use_id=item_info.get("id"),
                    tool_name="command",
                    invocation_string=item_info.get("command"),
                    content=GenericToolContent(text=item_info.get("aggregated_output")),
                )
            ]
            return ParsedToolResultResponse(content_blocks=content_blocks)
    elif message_type == "file_change":
        changes = item_info.get("changes")
        content_blocks = []
        # TODO CODEX: Clean this mess up
        if diff_tracker is not None:
            for change in changes:
                CODEX_CHANGE_TYPE_TO_TOOL_TYPE = {"update": AgentToolName.EDIT.value, "add": AgentToolName.WRITE.value}
                change_type = CODEX_CHANGE_TYPE_TO_TOOL_TYPE.get(change.get("kind"), "")
                path = change.get("path")
                diff = diff_tracker.compute_diff_for_tool(change_type, {"file_path": path})
                if diff:
                    content_block = ToolResultBlock(
                        tool_use_id=ToolUseID(uuid.uuid4().hex),
                        tool_name=change_type,
                        invocation_string="",
                        content=DiffToolContent(diff=diff, file_path=path),
                    )
                    content_blocks.append(content_block)
        return ParsedToolResultResponse(content_blocks=content_blocks)
    return None


def update_codex_token_state(
    environment: Environment,
    source_branch: str,
    output_message_queue: Queue[Message],
    task_id: TaskID,
    cumulative_tokens: int,
) -> None:
    """Update cumulative token count and cost, persisting to state file."""
    token_state = {"tokens": cumulative_tokens, "cost_usd": 0}

    environment.write_file(str(environment.get_state_path() / TOKEN_AND_COST_STATE_FILE), json.dumps(token_state))
    stream_token_and_cost_info(
        environment=environment,
        source_branch=source_branch,
        output_message_queue=output_message_queue,
        task_id=task_id,
    )
