import html
import json
import re
import shlex
from pathlib import Path
from queue import Empty
from queue import Queue
from typing import Any
from typing import Mapping
from typing import assert_never
from typing import cast

from bs4 import BeautifulSoup
from loguru import logger

from imbue_core.agents.agent_api.data_types import AgentToolName
from imbue_core.async_monkey_patches import log_exception
from imbue_core.processes.local_process import RunningProcess
from imbue_core.sculptor.state.chat_state import DiffToolContent
from imbue_core.sculptor.state.chat_state import GenericToolContent
from imbue_core.sculptor.state.chat_state import ImbueCLIToolContent
from imbue_core.sculptor.state.chat_state import SimpleToolContent
from imbue_core.sculptor.state.chat_state import TextBlock
from imbue_core.sculptor.state.chat_state import ToolInput
from imbue_core.sculptor.state.chat_state import ToolResultBlock
from imbue_core.sculptor.state.chat_state import ToolUseBlock
from imbue_core.sculptor.state.chat_state import ToolUseID
from imbue_core.sculptor.state.claude_state import ContentBlockStopEvent
from imbue_core.sculptor.state.claude_state import MessageStartEvent
from imbue_core.sculptor.state.claude_state import MessageStopEvent
from imbue_core.sculptor.state.claude_state import ParsedCompactionSummaryResponse
from imbue_core.sculptor.state.claude_state import ParsedEndResponse
from imbue_core.sculptor.state.claude_state import ParsedInitResponse
from imbue_core.sculptor.state.claude_state import ParsedStreamEvent
from imbue_core.sculptor.state.claude_state import ParsedStreamEventTypes
from imbue_core.sculptor.state.claude_state import ParsedToolResultResponseSimple
from imbue_core.sculptor.state.claude_state import ParsedUserResponse
from imbue_core.sculptor.state.claude_state import ParsedUserResponseTypeSimple
from imbue_core.sculptor.state.claude_state import TextBlockStartEvent
from imbue_core.sculptor.state.claude_state import TextDeltaEvent
from imbue_core.sculptor.state.claude_state import ToolBlockStartEvent
from imbue_core.sculptor.state.claude_state import ToolInputDeltaEvent
from imbue_core.sculptor.state.claude_state import is_tool_name_in_servers
from imbue_core.sculptor.state.claude_state import parse_claude_code_json_lines_simple
from imbue_core.sculptor.state.mcp_constants import IMBUE_CLI_INTERNAL_MCP_SERVER_NAME
from imbue_core.sculptor.state.mcp_constants import IMBUE_CLI_USER_MCP_SERVER_NAME
from imbue_core.secrets_utils import Secret
from imbue_core.serialization import SerializedException
from sculptor.agents.default.claude_code_sdk.diff_tracker import DiffTracker
from sculptor.agents.default.errors import CommandFailedError
from sculptor.interfaces.agents.agent import AgentMessageID
from sculptor.interfaces.agents.agent import ChatInputUserMessage
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.agents.agent import MCPServerInfo
from sculptor.interfaces.agents.agent import MCPServerType
from sculptor.interfaces.agents.agent import Message
from sculptor.interfaces.agents.agent import ParsedAgentResponseType
from sculptor.interfaces.agents.agent import ParsedAssistantResponse
from sculptor.interfaces.agents.agent import ParsedToolResultResponse
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.agent import TaskID
from sculptor.interfaces.agents.agent import UserCommandFailureAgentMessage
from sculptor.interfaces.agents.errors import IllegalOperationError
from sculptor.interfaces.environments.base import Environment
from sculptor.primitives.constants import USER_FACING_LOG_TYPE

# Union type for pattern matching


def get_claude_command(
    instructions_file: Path,
    system_prompt: str,
    session_id: str | None,
    model_name: str | None,
    enable_streaming: bool = False,
) -> list[str]:
    claude_command = (
        # Important not to use /imbue/nix_bin/claude here, since it won't have the right certificates set up and claude will stall.
        f"IS_SANDBOX=1 claude -p --dangerously-skip-permissions --output-format=stream-json --verbose < {shlex.quote(str(instructions_file))}"
    )

    # Enable streaming for lower time-to-first-token
    if enable_streaming:
        claude_command += " --include-partial-messages"

    # If a session ID is provided, then we resume the existing conversation
    if session_id:
        claude_command += f" --resume {shlex.quote(session_id)}"

    if system_prompt:
        claude_command += f" --append-system-prompt {shlex.quote(system_prompt)}"

    if model_name:
        claude_command += f" --model {shlex.quote(model_name)}"

    return ["bash", "-c", claude_command]


def _run_user_requested_command(
    message: CommandInputUserMessage,
    environment: Environment,
    task_id: TaskID,
    output_message_queue: Queue[Message],
    secrets: Mapping[str, str | Secret],
) -> tuple[int, RunningProcess]:
    """run the command that the user requested"""

    logger.info("Running user command: {}", message.text)
    with logger.contextualize(log_type=USER_FACING_LOG_TYPE, task_id=task_id):
        logger.debug("Running command: " + message.text)
    command_process = environment.run_process_in_background(
        ["bash", "-c", message.text],
        secrets=secrets or {},
        run_with_sudo_privileges=message.run_with_sudo_privileges,
    )
    queue = command_process.get_queue()
    # FIXME: this is awkward... the user could try to run a command that takes a REALLY long time
    #  if that happens, it only makes sense for us to warn them
    #  also, they need a way to interrupt this command (like they can interrupt normal messages)
    with logger.contextualize(log_type=USER_FACING_LOG_TYPE, task_id=task_id):
        while not command_process.is_finished() or not queue.empty():
            try:
                line, is_stdout = queue.get(timeout=0.1)
            except Empty:
                continue
            logger.debug(line)
    command_exit_code = command_process.wait()
    if command_exit_code != 0:
        try:
            raise CommandFailedError(
                f"Command failed with exit code {command_exit_code}\nstdout=\n{command_process.read_stdout()}\nstderr=\n{command_process.read_stderr()}"
            )
        except CommandFailedError as e:
            output_message_queue.put(
                UserCommandFailureAgentMessage(message_id=AgentMessageID(), error=SerializedException.build(e))
            )
    return command_exit_code, command_process


def get_user_instructions(
    # TODO: why these? should it be an established union type?
    message: CommandInputUserMessage | ChatInputUserMessage | ResumeAgentResponseRunnerMessage,
    environment: Environment,
    output_message_queue: Queue[Message],
    task_id: TaskID,
    secrets: Mapping[str, str | Secret],
    file_paths: tuple[str, ...],
) -> str | None:
    if isinstance(message, CommandInputUserMessage):
        command_exit_code, command_process = _run_user_requested_command(
            message,
            environment,
            task_id,
            output_message_queue,
            secrets,
        )

        # if they don't want the LLM to react to this, we're all done, return
        if not message.is_included_in_context:
            return

        # otherwise tell the LLM about the output of this command that we ran on behalf of the user
        user_instructions = f"I ran this command:\n{message.text}\n\nand it exited with code {command_exit_code} and I got this stdout:\n```> {command_process.read_stdout()}```\n\nand this on stderr:\n```{command_process.read_stderr()}```\n\nPlease simply respond with just 'Command finished' to acknowledge this."
    elif isinstance(message, ChatInputUserMessage):
        user_instructions = _strip_and_unescape_html(message.text)
        # TODO: there might be a better way to give claude files via the sdk or another way in the future
        if file_paths:
            file_paths_str = "\n- ".join(file_paths)
            file_instructions = f"""<system-instructions>
The user has attached these files. Read them before proceeding.
{file_paths_str}
</system-instructions>

"""
            user_instructions = file_instructions + user_instructions
    elif isinstance(message, ResumeAgentResponseRunnerMessage):
        user_instructions = """<system-reminder>\nYour previous response was interrupted. Please continue from where you left off. DO NOT respond to this message, just keep continuing with your previous reply as if you had not been stopped part-way through.\n</system-reminder>"""
    else:
        raise IllegalOperationError(f"Unexpected message type: {type(message)}")
    return user_instructions


def _strip_and_unescape_html(text: str) -> str:
    """
    Strip HTML tags (introduced by the frontend editor) and unescape HTML entities (provided by the user).

    """
    stripped = BeautifulSoup(text, "html.parser").get_text()
    return html.unescape(stripped)


def parse_claude_code_json_lines(
    line: str,
    tool_use_map: dict[str, tuple[str, ToolInput]] | None = None,
    diff_tracker: DiffTracker | None = None,
    is_compacting: bool = False,
) -> ParsedAgentResponseType | ParsedStreamEventTypes | None:
    """Parse a JSON line from Claude Code SDK.

    Returns a ParsedAgentMessage subtype, ParsedStreamEvent subtype, or None for unknown message types.
    Includes full parsing of tool results, including DiffToolContent.

    Raises
        json.JSONDecodeError: If the line is not valid JSON.
        Other exceptions such as AssertionError
    """
    # First check for stream_event type (from --include-partial-messages)
    data = json.loads(line)
    if isinstance(data, dict) and data.get("type") == "stream_event":
        event = data.get("event", {})
        event_type = event.get("type", "")

        if event_type == "message_start":
            message_id = event.get("message", {}).get("id", "")
            return MessageStartEvent(message_id=message_id)

        elif event_type == "message_stop":
            return MessageStopEvent()

        elif event_type == "content_block_start":
            content_block = event.get("content_block", {})
            raw_block_type = content_block.get("type", "")
            if raw_block_type == "text":
                return TextBlockStartEvent(
                    index=event.get("index", 0),
                )
            elif raw_block_type == "tool_use":
                return ToolBlockStartEvent(
                    index=event.get("index", 0),
                    tool_id=content_block.get("id"),
                    tool_name=content_block.get("name"),
                )

        elif event_type == "content_block_delta":
            delta = event.get("delta", {})
            delta_type = delta.get("type", "")
            index = event.get("index", 0)

            if delta_type == "input_json_delta":
                return ToolInputDeltaEvent(
                    index=index,
                    partial_json=delta.get("partial_json", ""),
                )
            elif delta_type == "text_delta":
                return TextDeltaEvent(
                    index=index,
                    text=delta.get("text", ""),
                )

        elif event_type == "content_block_stop":
            return ContentBlockStopEvent(index=event.get("index", 0))

        # skip all other streaming events
        else:
            return None

    # Standard parsing for non-stream events
    message_type_and_results = parse_claude_code_json_lines_simple(line, tool_use_map)
    if message_type_and_results is None:
        return None
    message_type, results_with_simple_tool_calls = message_type_and_results

    if message_type == "user":
        if is_compacting:
            # Claude code gives two messages on each compaction call. We specifically want to parse the contents of the
            # message containing the summary. Since it has no special identifiable markers, we instead parse for the
            # local command message and skip it.
            assert results_with_simple_tool_calls is not None
            # these are the only types with content_blocks. it probably can only be one of these.
            assert isinstance(results_with_simple_tool_calls, (ParsedAssistantResponse | ParsedUserResponseTypeSimple))
            summary_block = results_with_simple_tool_calls.content_blocks[0]
            assert isinstance(summary_block, TextBlock)
            if "<local-command-stdout>" in summary_block.text:
                return None
            return ParsedCompactionSummaryResponse(content=summary_block)
        # Skip text-only user messages
        if isinstance(results_with_simple_tool_calls, ParsedUserResponse):
            return None

        return _load_content_for_tool_result_message(
            cast(ParsedToolResultResponseSimple, results_with_simple_tool_calls), diff_tracker
        )
    else:
        return cast(ParsedAgentResponseType, results_with_simple_tool_calls)


def cancel_pending_claude_tool_calls(environment: Environment, session_id: str) -> None:
    """This function is expected to be called any time we interrupt the claude
    code process, and will manually mark our tool calls as cancelled.

    This is necessary due to a bug in Claude code:
      https://github.com/anthropics/claude-code/issues/473

    DO NOT CALL THIS while Claude Code is processing, or a demon will fly out of
    your nose.
    """
    claude_session_file_path = _get_claude_session_file_path(environment, session_id)
    if not environment.exists(str(claude_session_file_path)):
        logger.info(
            "Session id {} is not valid because the file {} does not exist", session_id, claude_session_file_path
        )
        return

    file_contents = environment.read_file(str(claude_session_file_path))
    assert isinstance(file_contents, str)

    cancelled_tool_calls = _isolate_cancelled_tool_calls(file_contents)

    if cancelled_tool_calls:
        logger.info("Uncompleted Tool Calls detected: {}. Surgically removing these lines.", cancelled_tool_calls)

        cancelled_tool_re = re.compile("|".join(cancelled_tool_calls))

        filtered_lines = []
        for line in file_contents.strip().split("\n"):
            if not cancelled_tool_re.search(line):
                filtered_lines.append(line + "\n")

        # update the parent uuid for each line so that the messages are contiguous
        completed_lines = []
        parent_uuid = None
        for line in filtered_lines:
            line_stripped = line.strip()

            if line_stripped == "":
                continue
            try:
                data = json.loads(line_stripped)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed line")
                continue

            if not isinstance(data, dict):
                completed_lines.append(line)
                continue
            if "uuid" not in data:
                # this may occur for lines such as "InvalidAPIKey"
                completed_lines.append(line)
                continue
            data["parentUuid"] = parent_uuid
            parent_uuid = data["uuid"]
            completed_lines.append(json.dumps(data))

        patched_content = "\n".join(completed_lines) + "\n"

        environment.write_file(
            str(claude_session_file_path),
            patched_content,
        )


def _isolate_cancelled_tool_calls(file_contents: str) -> set[ToolUseID]:
    """Search the given file contents for any tool calls that have not been completed."""
    lines = file_contents.split("\n")
    messages: list[ParsedAgentResponseType | None] = []
    for line in lines:
        if not line:
            continue
        try:
            parsed_message = parse_claude_code_json_lines(line)
            if isinstance(parsed_message, ParsedStreamEvent):
                continue
            messages.append(parsed_message)
        except json.JSONDecodeError:
            logger.info("Skipping malformed history line {!r}", line)
        except Exception as e:
            logger.info("Could not successfully parse user line: {!r}, {}", line, e)

    # Use two sets to calculate set difference, to make us robust to processing tools out of order.
    started_tool_use_ids: set[ToolUseID] = set()
    completed_tool_use_ids: set[ToolUseID] = set()

    for message in messages:
        match message:
            case ParsedAssistantResponse():
                for content in message.content_blocks:
                    if isinstance(content, ToolUseBlock):
                        started_tool_use_ids.add(content.id)
            case ParsedToolResultResponse():
                for content in message.content_blocks:
                    if isinstance(content, ToolResultBlock):
                        completed_tool_use_ids.add(content.tool_use_id)
            case ParsedInitResponse() | ParsedEndResponse() | ParsedCompactionSummaryResponse() | None:
                # we want to ignore these, since they don't have relevance for the tool use
                continue
            case _ as unreachable:
                assert_never(unreachable)

    logger.info("Started {} tool use ids: {}", len(started_tool_use_ids), started_tool_use_ids)
    logger.info("Completed {} tool use ids: {}", len(completed_tool_use_ids), completed_tool_use_ids)

    return started_tool_use_ids - completed_tool_use_ids


def parse_mcp_tools_by_server(tools: list[str], mcp_servers: dict[str, str]) -> dict[str, MCPServerInfo]:
    """Parse MCP tools and group them by server.

    MCP tools follow the pattern: mcp__<server_name>__<tool_name>
    """
    server_tools: dict[str, list[str]] = {name: [] for name in mcp_servers.keys()}

    # Group tools by server
    for tool in tools:
        if tool.startswith("mcp__"):
            # Extract server name from tool
            parts = tool.split("__", 2)
            if len(parts) >= 3:
                server_name = parts[1]
                tool_name = parts[2]

                if server_name in server_tools:
                    server_tools[server_name].append(tool_name)
                else:
                    # This shouldn't happen if mcp_servers is complete, but log it
                    logger.warning("Found MCP tool '{}' for unknown server '{}'", tool, server_name)

    # Determine server types based on known imbue-cli server names
    imbue_cli_servers = {IMBUE_CLI_INTERNAL_MCP_SERVER_NAME, IMBUE_CLI_USER_MCP_SERVER_NAME}

    # Create MCPServerInfo objects
    result = {}
    for name, status in mcp_servers.items():
        server_type = MCPServerType.IMBUE_CLI if name in imbue_cli_servers else MCPServerType.EXTERNAL
        result[name] = MCPServerInfo(status=status, server_type=server_type, tools=server_tools.get(name, []))

    return result


def is_session_id_valid(session_id: str, environment: Environment, is_session_running: bool) -> bool:
    """Check if the session id is valid and can be resumed.

    Session ids are valid if they are present in the .claude/projects/-code/ directory.
    And the file contains at least one message that contains the session id.

    This is used to determine if we can resume a session after an interruption.
    """
    claude_session_file_path = _get_claude_session_file_path(environment, session_id)
    if not environment.exists(str(claude_session_file_path)):
        logger.info(
            "Session id {} is not valid because the file {} does not exist", session_id, claude_session_file_path
        )
        return False
    file_contents = environment.read_file(str(claude_session_file_path))
    for line in file_contents.strip().splitlines():
        try:
            maybe_message = json.loads(line)
            if (
                isinstance(maybe_message, dict)
                and "sessionId" in maybe_message
                and maybe_message["sessionId"] == session_id
            ):
                return True
        except json.JSONDecodeError:
            if is_session_running:
                logger.debug(
                    "Skipping malformed history line {} - this may happen if the agent is still working", line
                )
            else:
                logger.debug("Found malformed history line {} - this should not happen", line)
                return False
    return False


def _create_tool_content(
    tool_name: str,
    tool_input: ToolInput,
    tool_content: Any,
    diff_tracker: DiffTracker | None,
) -> GenericToolContent | DiffToolContent:
    """Create appropriate tool content based on tool type."""
    if tool_name in [AgentToolName.WRITE, AgentToolName.EDIT, AgentToolName.MULTI_EDIT] and diff_tracker:
        diff = diff_tracker.compute_diff_for_tool(tool_name, tool_input)
        if diff:
            file_path = tool_input.get("file_path", "")
            return DiffToolContent(diff=diff, file_path=file_path)

    return GenericToolContent(text=str(tool_content))


def _load_content_for_tool_result_message_no_error_checking(
    simple_tool_result: ParsedToolResultResponseSimple | None,
    diff_tracker: DiffTracker | None,
) -> ParsedToolResultResponse | None:
    """Handle user/tool result message type, including parsing tool content."""

    if simple_tool_result is None:
        return None

    # _handle_tool_result_message only returns one block
    (simple_block,) = simple_tool_result.content_blocks

    if is_tool_name_in_servers(simple_block.tool_name):
        tool_content_ = simple_block.content
        assert isinstance(tool_content_, ImbueCLIToolContent)
        tool_content = tool_content_  # for the type checker
    else:
        assert isinstance(simple_block.content, SimpleToolContent)
        tool_content = _create_tool_content(
            simple_block.tool_name, simple_block.content.tool_input, simple_block.content.tool_content, diff_tracker
        )

    return ParsedToolResultResponse(
        content_blocks=[
            ToolResultBlock(
                tool_use_id=simple_block.tool_use_id,
                tool_name=simple_block.tool_name,
                invocation_string=simple_block.invocation_string,
                content=tool_content,
                is_error=simple_block.is_error,
            )
        ]
    )


def _load_content_for_tool_result_message(
    simple_tool_result: ParsedToolResultResponseSimple | None,
    diff_tracker: DiffTracker | None,
) -> ParsedToolResultResponse | None:
    """Load content for tool result message, with error checking. If parsing fails, but the JSON is valid, we return None.

    Raises:
        json.JSONDecodeError: If the line is not valid JSON.
    """
    try:
        return _load_content_for_tool_result_message_no_error_checking(simple_tool_result, diff_tracker)
    except Exception as e:
        if isinstance(e, json.JSONDecodeError):
            raise e
        log_exception(e, "Error loading content for tool result message")
        return None


def _get_claude_session_file_path_no_check(root_path: Path, session_id: str) -> Path:
    return root_path / ".claude" / "projects" / "-code" / f"{session_id}.jsonl"


def _get_claude_session_file_path(environment: Environment, session_id: str) -> Path:
    # TODO: ideally we shouldn't hardcode "-code" but i'm not too sure how claude code generates
    # these folders from the paths (the original path is in get_workspace_path and is /code in the docker case)
    # in this case, we can at least fail loudly if the workspace path is not what we expect
    assert environment.get_workspace_path() == Path("/code")
    return _get_claude_session_file_path_no_check(environment.get_container_user_home_directory(), session_id)
