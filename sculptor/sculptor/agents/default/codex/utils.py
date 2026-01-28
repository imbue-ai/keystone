import json
import os
import shlex
import tempfile
from json import JSONDecodeError
from pathlib import Path

from loguru import logger

from sculptor.interfaces.environments.base import Environment
from sculptor.services.config_service.data_types import Credentials


def populate_codex_settings(environment: Environment, credentials: Credentials | None = None) -> None:
    logger.info("Populating codex settings")

    # Get OpenAI API key from credentials if available, otherwise fall back to environment
    openai_api_key = None
    if credentials and credentials.openai:
        openai_api_key = credentials.openai.openai_api_key.unwrap()
        logger.info("Using OpenAI API key from config service")
    else:
        # Fall back to environment variable for backward compatibility
        openai_api_key = os.environ.get("OPENAI_API_KEY")
        if openai_api_key:
            logger.info("Using OpenAI API key from environment variable")

    auth_contents = {"OPENAI_API_KEY": openai_api_key}
    # See https://github.com/openai/codex/issues/3064 on why we need the ignore_default_excludes
    config_contents = f'[projects."{environment.get_workspace_path()}"]\ntrust_level = "trusted"\n\n[shell_environment_policy]\nignore_default_excludes = true\n'

    with tempfile.NamedTemporaryFile() as tmp_file:
        tmp_file.write(json.dumps(auth_contents).encode())
        tmp_file.flush()
        environment.copy_from_local(
            Path(tmp_file.name), str(environment.get_container_user_home_directory() / ".codex" / "auth.json")
        )

    with tempfile.NamedTemporaryFile() as tmp_file:
        tmp_file.write(config_contents.encode())
        tmp_file.flush()
        environment.copy_from_local(
            Path(tmp_file.name), str(environment.get_container_user_home_directory() / ".codex" / "config.toml")
        )


def get_codex_command(
    instructions_file: Path,
    system_prompt: str,
    session_id: str | None,
    model_name: str | None,
) -> list[str]:
    # TODO CODEX: re-enable
    # perhaps use CODEX_TOOLS? unless there are tools there we don't want to permit?
    # allowed_tools = [
    #     *IMBUE_CLI_MCP_TOOL_PREFIXES,
    #     "Agent",
    #     "Bash",
    #     "Edit",
    #     "Glob",
    #     "Grep",
    #     "LS",
    #     "MultiEdit",
    #     "NotebookEdit",
    #     "NotebookRead",
    #     "Read",
    #     "TodoRead",
    #     "TodoWrite",
    #     "WebFetch",
    #     "WebSearch",
    #     "Write",
    # ]
    codex_command = (
        # Allow codex to run with full permissions since otherwise it requires landlock for the sandbox
        f"codex exec --json --sandbox danger-full-access --dangerously-bypass-approvals-and-sandbox"
    )

    # TODO CODEX: re-enable system prompt
    # if system_prompt:
    #     codex_command += f" --append-system-prompt {shlex.quote(system_prompt)}"

    if model_name:
        codex_command += f" --model {shlex.quote(model_name)}"
    # If a session ID is provided, then we resume the existing conversation

    if session_id:
        codex_command += f" resume {shlex.quote(session_id)}"

    codex_command += f" < {shlex.quote(str(instructions_file))}"

    return ["bash", "-c", codex_command]


def get_codex_session_file_path(environment: Environment, session_id: str) -> Path | None:
    sessions_root = environment.get_container_user_home_directory() / ".codex" / "sessions"
    if not environment.exists(str(sessions_root)):
        return None

    process = environment.run_process_to_completion(
        ["find", str(sessions_root), "-type", "f", "-name", f"*{session_id}*.jsonl"], secrets={}
    )
    if process.returncode != 0:
        logger.info("Failed to find session file {}: {}", session_id, process.stderr)
        return None
    session_file = process.stdout
    return Path(session_file.strip())


def cancel_pending_codex_tool_calls(environment: Environment, session_id: str) -> None:
    codex_session_file_path = get_codex_session_file_path(environment, session_id)
    if not environment.exists(str(codex_session_file_path)):
        logger.info(
            "Session id {} is not valid because the file {} does not exist", session_id, codex_session_file_path
        )
        return

    file_contents = environment.read_file(str(codex_session_file_path))
    assert isinstance(file_contents, str)

    tool_use_ids = set()
    tool_result_ids = set()
    for line in file_contents.splitlines():
        try:
            line_contents = json.loads(line)
        except JSONDecodeError:
            logger.info("Failed to decode line {}", line)
            continue
        line_type = line_contents.get("type")
        item = line_contents.get("item")
        if item is None:
            continue
        item_type = item.get("type")
        item_id = item.get("id")
        if item_type == "command_execution":
            if line_type == "item.started":
                tool_use_ids.add(item_id)
            elif line_type == "item.completed":
                tool_result_ids.add(item_id)

    # Get elements not in both sets
    unmatched_ids = tool_use_ids ^ tool_result_ids
    filtered_lines = []
    for line in file_contents.splitlines():
        try:
            line_contents = json.loads(line)
        except JSONDecodeError:
            filtered_lines.append(line)
            continue
        item = line_contents.get("item")
        # Append if it's not an item line or if it's not a pruned line
        if item is None or item.get("id") not in unmatched_ids:
            filtered_lines.append(line)

        patched_content = "\n".join(filtered_lines) + "\n"

        environment.write_file(
            str(codex_session_file_path),
            patched_content,
        )
