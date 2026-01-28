import json
from queue import Empty
from queue import Queue
from threading import Event
from typing import Any

from loguru import logger

from imbue_core.processes.local_process import RunningProcess
from imbue_core.sculptor.state.chat_state import ContentBlockTypes
from imbue_core.sculptor.state.chat_state import TextBlock
from imbue_core.sculptor.state.chat_state import ToolInput
from imbue_core.sculptor.state.chat_state import ToolResultBlock
from imbue_core.sculptor.state.chat_state import ToolUseBlock
from imbue_core.sculptor.state.claude_state import ContentBlockStopEvent
from imbue_core.sculptor.state.claude_state import MessageStartEvent
from imbue_core.sculptor.state.claude_state import MessageStopEvent
from imbue_core.sculptor.state.claude_state import ParsedAssistantResponse
from imbue_core.sculptor.state.claude_state import ParsedCompactionSummaryResponse
from imbue_core.sculptor.state.claude_state import ParsedEndResponse
from imbue_core.sculptor.state.claude_state import ParsedInitResponse
from imbue_core.sculptor.state.claude_state import ParsedStreamEvent
from imbue_core.sculptor.state.claude_state import ParsedToolResultResponse
from imbue_core.sculptor.state.claude_state import TextBlockStartEvent
from imbue_core.sculptor.state.claude_state import TextDeltaEvent
from imbue_core.sculptor.state.claude_state import ToolBlockStartEvent
from imbue_core.sculptor.state.claude_state import ToolInputDeltaEvent
from imbue_core.sculptor.state.messages import AssistantMessageID
from sculptor.agents.default.artifact_creation import get_file_artifact_messages
from sculptor.agents.default.artifact_creation import should_send_diff_and_branch_name_artifacts
from sculptor.agents.default.artifact_creation import should_send_plan_artifact
from sculptor.agents.default.artifact_creation import should_send_suggestions_artifact
from sculptor.agents.default.claude_code_sdk.compaction_utils import update_token_and_cost_state
from sculptor.agents.default.claude_code_sdk.compaction_utils import update_weighted_tokens_since_last_verifier_check
from sculptor.agents.default.claude_code_sdk.constants import TRANSIENT_ERROR_CODES
from sculptor.agents.default.claude_code_sdk.diff_tracker import DiffTracker
from sculptor.agents.default.claude_code_sdk.errors import ClaudeAPIError
from sculptor.agents.default.claude_code_sdk.errors import ClaudeJsonDecodeError
from sculptor.agents.default.claude_code_sdk.process_manager_utils import parse_claude_code_json_lines
from sculptor.agents.default.claude_code_sdk.process_manager_utils import parse_mcp_tools_by_server
from sculptor.agents.default.constants import SESSION_ID_STATE_FILE
from sculptor.agents.default.posthog_utils import emit_posthog_event_for_agent_message
from sculptor.database.models import AgentMessageID
from sculptor.interfaces.agents.agent import ContextSummaryMessage
from sculptor.interfaces.agents.agent import MCPStateUpdateAgentMessage
from sculptor.interfaces.agents.agent import Message
from sculptor.interfaces.agents.agent import PartialResponseBlockAgentMessage
from sculptor.interfaces.agents.agent import ResponseBlockAgentMessage
from sculptor.interfaces.agents.agent import StreamingMessageCompleteAgentMessage
from sculptor.interfaces.agents.agent import StreamingStderrAgentMessage
from sculptor.interfaces.agents.agent import TaskID
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.interfaces.agents.errors import AgentClientError
from sculptor.interfaces.agents.errors import AgentTransientError
from sculptor.interfaces.environments.base import Environment


class ClaudeOutputProcessor:
    def __init__(
        self,
        process: RunningProcess,
        source_command: str,
        output_message_queue: Queue[Message],
        environment: Environment,
        diff_tracker: DiffTracker | None,
        source_branch: str,
        task_id: TaskID,
        session_id_written_event: Event,
        is_compacting: bool = False,
        streaming_enabled: bool = True,
    ):
        self.process = process
        self.source_command = source_command
        self.output_message_queue = output_message_queue
        self.environment = environment
        self.diff_tracker = diff_tracker
        self.source_branch = source_branch
        self.task_id = task_id
        self.session_id_written_event = session_id_written_event
        self.is_compacting = is_compacting
        self.streaming_enabled = streaming_enabled

        self.queue = self.process.get_queue()
        # The current assistant message ID corresponds to the entire turn, which may contain multiple messages (assistant message + tool results)
        # We happen to set the current_message_id to be the messageID of the first assistant message in the turn
        # We might want to consider distinguishing between turn ID and message ID in the future
        self.current_turn_id: AssistantMessageID | None = None
        self.last_assistant_message: ResponseBlockAgentMessage | None = None
        self.tool_use_map: dict[str, tuple[str, ToolInput]] = {}
        self.found_final_message = False

        self._is_streaming_turn = False
        self._completed_streaming_blocks: list[ContentBlockTypes] = []
        self._text_accumulators: dict[int, str] = {}
        self._tool_accumulators: dict[int, dict[str, Any]] = {}
        # Persistent message ID for the ChatMessage, generated at the first MessageStartEvent.
        # Used in partials and the first ResponseBlockAgentMessage to ensure stable IDs.
        self._first_response_message_id: AgentMessageID | None = None
        self._used_first_response_id: bool = False

    @classmethod
    def build_and_process_output(
        cls,
        process: RunningProcess,
        source_command: str,
        output_message_queue: Queue[Message],
        environment: Environment,
        diff_tracker: DiffTracker | None,
        source_branch: str,
        task_id: TaskID,
        session_id_written_event: Event,
        is_compacting: bool = False,
        streaming_enabled: bool = True,
    ) -> bool:
        processor = cls(
            process,
            source_command,
            output_message_queue,
            environment,
            diff_tracker,
            source_branch,
            task_id,
            session_id_written_event,
            is_compacting,
            streaming_enabled,
        )
        return processor._process_output()

    def _process_output(self) -> bool:
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
                result = parse_claude_code_json_lines(
                    line, self.tool_use_map, self.diff_tracker, is_compacting=self.is_compacting
                )
            except json.JSONDecodeError as e:
                # NOTE: sometimes the claude -p will return the following message:
                # "This error originated either by throwing inside of an async function without a catch block,
                # or by rejecting a promise which was not handled with .catch(). The promise rejected with the reason:"
                # this does not seem to be our fault and might be a claude bug.
                # NOTE (update): we have not seen the above bug in like a week so maybe it has gone away
                raise ClaudeJsonDecodeError(
                    f"JSON decode error from Claude Code SDK line: {line}\nstdout: {self.process.read_stdout()}\nstderr: {self.process.read_stderr()}",
                ) from e

            if result is None:
                continue

            if isinstance(result, ParsedStreamEvent):
                self._handle_stream_event(result)
                # No further processing needed for stream events
                continue

            emit_posthog_event_for_agent_message(self.task_id, result)

            if isinstance(result, ParsedInitResponse):
                self._parse_init_response(result)

            elif isinstance(result, ParsedEndResponse):
                self._parse_stream_end_response(result)

            elif isinstance(result, ParsedAssistantResponse):
                if self._is_streaming_turn:
                    # During streaming, only emit message for DB persistence.
                    # Use the pre-generated ID for the first ResponseBlockAgentMessage,
                    # so it matches the ChatMessage.id set by partials.
                    if not self._used_first_response_id:
                        message_id = self._first_response_message_id
                        assert message_id is not None
                        self._used_first_response_id = True
                    else:
                        message_id = AgentMessageID()
                    logger.trace("Emitting assistant response for persistence")
                    self.output_message_queue.put(
                        ResponseBlockAgentMessage(
                            role="assistant",
                            message_id=message_id,
                            assistant_message_id=AssistantMessageID(result.message_id),
                            content=tuple(result.content_blocks),
                        )
                    )
                else:
                    self._parse_assistant_response(result)

            elif isinstance(result, ParsedToolResultResponse):
                self._parse_tool_result_response(result)

            elif isinstance(result, ParsedCompactionSummaryResponse):
                self._parse_compaction_summary_response(result)

        logger.debug("Process stream ended")

        return self.found_final_message

    def _parse_init_response(self, result: ParsedInitResponse) -> None:
        session_id = result.session_id
        session_file_path = self.environment.get_state_path() / SESSION_ID_STATE_FILE
        self.environment.write_file(str(session_file_path), session_id)
        self.session_id_written_event.set()
        logger.info("Stored session_id: {}", session_id)

        # Parse MCP tools and create enriched server info
        mcp_server_info = parse_mcp_tools_by_server(result.tools, result.mcp_servers)
        self.output_message_queue.put(MCPStateUpdateAgentMessage(mcp_servers=mcp_server_info))

    def _parse_stream_end_response(self, result: ParsedEndResponse) -> None:
        logger.debug("Stream ended")
        if result.session_id and result.total_cost_usd:
            update_token_and_cost_state(
                environment=self.environment,
                source_branch=self.source_branch,
                output_message_queue=self.output_message_queue,
                session_id=result.session_id,
                cost_usd=result.total_cost_usd,
                task_id=self.task_id,
            )

        if result.input_tokens and result.output_tokens:
            update_weighted_tokens_since_last_verifier_check(
                environment=self.environment,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

        # sigh. I saw this take just a tiny bit more than 5 seconds on modal once :(
        self.process.wait(timeout=10.0)

        # if there is an error, raise the appropriate error to be handled in the context manager
        if result.is_error:
            result_message = result.result
            if result_message.startswith("API Error"):
                logger.info("API Error: stdout={}, stderr={}", self.process.read_stdout(), self.process.read_stderr())
                if any(result_message.startswith(f"API Error: {code}") for code in TRANSIENT_ERROR_CODES):
                    raise AgentTransientError(result.result, exit_code=self.process.returncode)
                raise ClaudeAPIError(result.result, exit_code=self.process.returncode)
            else:
                raise AgentClientError(result.result, exit_code=self.process.returncode)

        self.found_final_message = True

    def _parse_assistant_response(self, result: ParsedAssistantResponse) -> None:
        new_message_id = result.message_id
        new_blocks = result.content_blocks

        # Track tool names and file paths from ToolUseBlocks
        for block in new_blocks:
            if isinstance(block, ToolUseBlock):
                self.tool_use_map[block.id] = (block.name, block.input)

        logger.debug("Streaming new assistant message {}", new_message_id)
        logger.trace("New blocks: {}", new_blocks)
        if self.current_turn_id is None:
            self.current_turn_id = new_message_id
        self.last_assistant_message = ResponseBlockAgentMessage(
            role="assistant",
            message_id=AgentMessageID(),
            assistant_message_id=AssistantMessageID(new_message_id),
            content=tuple(new_blocks),
        )
        self.output_message_queue.put(self.last_assistant_message)

    def _parse_tool_result_response(self, result: ParsedToolResultResponse) -> None:
        assert self.current_turn_id is not None
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
            tool_info = self.tool_use_map.get(block.tool_use_id, None)
            if tool_info and not block.is_error:
                tool_name, tool_input = tool_info
                if not will_send_diff_and_branch_name_artifacts:
                    will_send_diff_and_branch_name_artifacts = should_send_diff_and_branch_name_artifacts(
                        tool_name, tool_input
                    )
                plan_artifact_info = (tool_input, block) if should_send_plan_artifact(tool_name) else None
                suggestions_artifact_info = (
                    (tool_input, block) if should_send_suggestions_artifact(tool_name) else None
                )

        self.last_assistant_message = ResponseBlockAgentMessage(
            role="assistant",
            message_id=AgentMessageID(),
            assistant_message_id=AssistantMessageID(self.current_turn_id),
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

    def _parse_compaction_summary_response(self, result: ParsedCompactionSummaryResponse) -> None:
        compaction_summary_message = ContextSummaryMessage(content=result.content.text)
        self.output_message_queue.put(compaction_summary_message)

    # ========== Streaming Methods ==========

    def _handle_stream_event(self, event: ParsedStreamEvent) -> None:
        """Handle streaming event:
        - Process one streaming event at a time.
        - Merge the event with internal state
        - Emit AgentMessages in output queue
            - Send partial text updates as they arrive
            - Send tool input only when complete
        """
        if not self.streaming_enabled:
            return

        if isinstance(event, MessageStartEvent):
            self._is_streaming_turn = True
            self.current_turn_id = AssistantMessageID(event.message_id)
            # Generate the persistent message ID on the FIRST MessageStartEvent of this request.
            # This ID will be used for the ChatMessage and the first ResponseBlockAgentMessage.
            if self._first_response_message_id is None:
                self._first_response_message_id = AgentMessageID()

        elif isinstance(event, TextBlockStartEvent):
            self._text_accumulators[event.index] = ""

        elif isinstance(event, ToolBlockStartEvent):
            self._tool_accumulators[event.index] = {
                "id": event.tool_id,
                "name": event.tool_name,
                "input_json": "",
            }

        elif isinstance(event, TextDeltaEvent):
            if event.index in self._text_accumulators:
                self._text_accumulators[event.index] += event.text
                self._emit_partial_message()

        elif isinstance(event, ToolInputDeltaEvent):
            if event.index in self._tool_accumulators:
                # Buffer tool input, don't emit until complete
                self._tool_accumulators[event.index]["input_json"] += event.partial_json

        elif isinstance(event, ContentBlockStopEvent):
            self._finalize_block_from_accumulator(event.index)

        elif isinstance(event, MessageStopEvent):
            # Turn complete - emit marker to signal end of streaming mode.
            self.output_message_queue.put(StreamingMessageCompleteAgentMessage(message_id=AgentMessageID()))
            self._reset_streaming_state()

    def _finalize_block_from_accumulator(self, index: int) -> None:
        """Finalize a block and optionally emit partial."""
        if index in self._text_accumulators:
            text = self._text_accumulators.pop(index)
            self._add_to_completed_streaming_blocks(index, TextBlock(text=text))
        elif index in self._tool_accumulators:
            tool_data = self._tool_accumulators.pop(index)
            try:
                tool_input = json.loads(tool_data["input_json"]) if tool_data["input_json"] else {}
            except json.JSONDecodeError:
                logger.error("Failed to parse tool input")
                tool_input = {}
            tool_block = ToolUseBlock(id=tool_data["id"], name=tool_data["name"], input=tool_input)
            self._add_to_completed_streaming_blocks(index, tool_block)
            # Track tool for later tool result processing
            self.tool_use_map[tool_data["id"]] = (tool_data["name"], tool_input)
            self._emit_partial_message()

    def _add_to_completed_streaming_blocks(self, index: int, block: ContentBlockTypes) -> None:
        """Add block at index, expanding list if needed."""
        while len(self._completed_streaming_blocks) <= index:
            self._completed_streaming_blocks.append(TextBlock(text=""))
        self._completed_streaming_blocks[index] = block

    def _emit_partial_message(self) -> None:
        """Emit current turn's partial state."""
        content = self._build_current_content()
        assert self.current_turn_id is not None
        assert self._first_response_message_id is not None
        self.output_message_queue.put(
            PartialResponseBlockAgentMessage(
                message_id=AgentMessageID(),
                content=tuple(content),
                # pyre-ignore[6] pyre thinks these could be None even though we assert that they are not None above
                assistant_message_id=self.current_turn_id,
                first_response_message_id=self._first_response_message_id,  # pyre-ignore[6]
            )
        )

    def _build_current_content(self) -> list[ContentBlockTypes]:
        """Render the current view of the message from finalized blocks + in-progress accumulators."""
        content = list(self._completed_streaming_blocks)
        for idx, text in self._text_accumulators.items():
            while len(content) <= idx:
                content.append(TextBlock(text=""))
            content[idx] = TextBlock(text=text)
        return content

    def _reset_streaming_state(self) -> None:
        """Reset streaming state for next turn."""
        self._is_streaming_turn = False
        self._completed_streaming_blocks = []
        self._text_accumulators = {}
        self._tool_accumulators = {}
