import json
import shlex
from pathlib import Path
from queue import Queue
from typing import cast

from loguru import logger
from pydantic import AnyUrl

from imbue_core.agents.agent_api.data_types import AgentToolName
from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import generate_id
from imbue_core.common import is_running_within_a_pytest_tree
from imbue_core.constants import ExceptionPriority
from imbue_core.sculptor.state.chat_state import ImbueCLIToolContent
from imbue_core.sculptor.state.chat_state import ToolInput
from imbue_core.sculptor.state.chat_state import ToolResultBlock
from imbue_core.sculptor.state.claude_state import is_tool_name_in_servers
from sculptor.agents.default.claude_code_sdk.utils import get_state_file_contents
from sculptor.agents.default.claude_code_sdk.utils import get_warning_message
from sculptor.agents.default.constants import DEFAULT_WAIT_TIMEOUT
from sculptor.agents.default.constants import FILE_CHANGE_TOOL_NAMES
from sculptor.agents.default.constants import TOKEN_AND_COST_STATE_FILE
from sculptor.database.models import AgentMessageID
from sculptor.interfaces.agents.agent import Message
from sculptor.interfaces.agents.agent import TaskID
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.interfaces.agents.artifacts import ArtifactUnion
from sculptor.interfaces.agents.artifacts import DiffArtifact
from sculptor.interfaces.agents.artifacts import FileAgentArtifact
from sculptor.interfaces.agents.artifacts import SuggestionsArtifact
from sculptor.interfaces.agents.artifacts import TodoItem
from sculptor.interfaces.agents.artifacts import TodoListArtifact
from sculptor.interfaces.agents.artifacts import TodoPriority
from sculptor.interfaces.agents.artifacts import TodoStatus
from sculptor.interfaces.agents.artifacts import UsageArtifact
from sculptor.interfaces.agents.errors import IllegalOperationError
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.errors import ProviderError
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.tasks.handlers.run_agent.errors import GitCommandFailure
from sculptor.tasks.handlers.run_agent.git import run_git_command_in_environment
from sculptor.utils.timeout import log_runtime_decorator


@log_runtime_decorator()
def get_file_artifact_messages(
    artifact_name: str,
    environment: Environment,
    source_branch: str,
    task_id: TaskID,
    tool_input: ToolInput | None = None,
    tool_result: ToolResultBlock | None = None,
) -> list[UpdatedArtifactAgentMessage | WarningAgentMessage]:
    messages: Queue[UpdatedArtifactAgentMessage | WarningAgentMessage] = Queue()
    try:
        remote_artifact_path = _make_file_artifact(
            artifact_name=artifact_name,
            environment=environment,
            source_branch=source_branch,
            task_id=task_id,
            message_queue=cast(Queue[Message], messages),
            tool_input=tool_input,
            tool_result=tool_result,
        )
    # TODO (PROD-2129): what happens if the environment just dies? This is a temporary hack to make sure we don't crash when it doesn't exist
    except ProviderError as e:
        messages.put(
            get_warning_message(
                f"Failed to fetch file artifact {artifact_name} because the environment is no longer available.",
                e,
                task_id,
            )
        )
    except Exception as e:
        log_exception(
            e,
            "Failed to create file artifact {artifact_name}",
            priority=ExceptionPriority.MEDIUM_PRIORITY,
            artifact_name=artifact_name,
        )
        messages.put(get_warning_message(f"Failed to create file artifact {artifact_name}", e, task_id))
    else:
        file_artifact_message = UpdatedArtifactAgentMessage(
            message_id=AgentMessageID(),
            artifact=FileAgentArtifact(
                name=artifact_name,
                url=AnyUrl(f"file://{remote_artifact_path}"),
            ),
        )
        messages.put(file_artifact_message)
    return list(messages.queue)


def should_send_diff_and_branch_name_artifacts(tool_name: str, tool_input: ToolInput) -> bool:
    logger.info("Should send diff and branch name")
    if tool_name in (FILE_CHANGE_TOOL_NAMES + (AgentToolName.BASH,)):
        return True
    command = tool_input.get("command", "")
    # Check for git commands that change the branch state
    git_branch_commands = [
        "git commit",
        "git reset",
        "git revert",
        "git checkout",
        "git switch",
        "git merge",
        "git rebase",
        "git cherry-pick",
    ]

    return any(cmd in command for cmd in git_branch_commands)


def should_send_plan_artifact(tool_name: str) -> bool:
    return tool_name == AgentToolName.TODO_WRITE


def should_send_suggestions_artifact(tool_name: str) -> bool:
    return is_tool_name_in_servers(tool_name)


XARGS_CONTAINS_NON_ZERO_RETURN_CODE = 123


def _run_diff_accepting_changes(
    environment: Environment,
    cmd_parts: list[str],
    error_msg: str,
    task_id: TaskID | None = None,
    message_queue: Queue[Message] | None = None,
) -> str:
    # Run git commands from the workspace directory where the git repo should be
    returncode, stdout, stderr = run_git_command_in_environment(
        environment,
        cmd_parts,
        {},
        check_output=False,
        timeout=DEFAULT_WAIT_TIMEOUT,
    )
    if returncode > 1 and returncode != XARGS_CONTAINS_NON_ZERO_RETURN_CODE:
        # if returncode is 0, the diff is empty. if returncode is 1, the diff is not empty.
        # if any of the xargs commands return a non-zero return code, the final returncode is 123.
        # if there exists any diff in our xargs command, we will get returncode 123.
        # there is a chance that the git diff command inside xargs fails with a returncode != 0 and != 1.
        # in that case, we will not raise an error even though we should BUT hopefully this is very very low probability
        # to fix this properly, we would need to do some ungodly bash magic to check if the xargs command failed with exit code 1 due to the presence of a diff or for some other reason.
        raise GitCommandFailure(
            f"{error_msg}\nreturncode: {returncode}\nstderr: {stderr[:1000]}\nstdout: {stdout[:1000]}\ncommand: {cmd_parts}",
            stderr=stderr,
            stdout=stdout,
            returncode=returncode,
            command=cmd_parts,
        )
    if stderr.strip() != "" and task_id is not None and message_queue is not None:
        # if stderr is not empty, then an error occurred somewhere in our crazy command, but we still want to return the diff
        message_queue.put(
            get_warning_message(
                f"Received stderr {stderr[:1000]} from git command {cmd_parts}",
                None,
                task_id,
            )
        )
    diff = stdout.strip()
    return diff


def _check_and_warn_on_nested_git_repos(
    environment: Environment,
    task_id: TaskID | None = None,
    message_queue: Queue[Message] | None = None,
) -> str:
    # We don't yet support diffs for nested git repos.
    # These will show up as folders in `git ls-files`, which is why we have the find filtering them out in the commands below in _create_diff_artifact
    # But this would be confusing if we didn't tell the user anything, so we'll use this function to detect them and make a warning when we can't display that diff yet.

    any_directories = [
        "bash",
        "-c",
        "git ls-files --others --exclude-standard -z | xargs -0 -I {} find {} -maxdepth 0 -type d",
    ]

    returncode, stdout, stderr = run_git_command_in_environment(
        environment, any_directories, {}, check_output=False, timeout=DEFAULT_WAIT_TIMEOUT
    )
    if task_id is not None and message_queue is not None:
        if returncode > 1 and returncode != XARGS_CONTAINS_NON_ZERO_RETURN_CODE:
            message_queue.put(
                get_warning_message(
                    f"Received stderr {stderr[:1000]} from git command {any_directories}",
                    None,
                    task_id,
                )
            )
        elif stderr.strip() != "" or stdout.strip() != "":
            stdout_lines = stdout.splitlines()
            stdout_tweaked = stdout_lines[0] if len(stdout_lines) == 1 else f"{stdout_lines}"
            stderr_tweaked = f"\nstderr: {stderr[:1000]}" if stderr != "" else ""
            message_queue.put(
                get_warning_message(
                    f"It appears you have a nested git repository at {stdout_tweaked}. We unfortunately don't support displaying diffs for that yet."
                    + stderr_tweaked,
                    None,
                    task_id,
                )
            )
    return stdout


def _create_diff_artifact(
    source_branch: str,
    environment: Environment,
    task_id: TaskID | None = None,
    message_queue: Queue[Message] | None = None,
) -> DiffArtifact:
    """Create a unified diff artifact with all three diff types."""
    if not is_running_within_a_pytest_tree():
        assert task_id is not None and message_queue is not None, (
            "task_id and message_queue must be provided when not running within a pytest tree"
        )
    merge_base_command = ["git", "merge-base", shlex.quote(source_branch), "HEAD"]
    merge_base_process = environment.run_process_to_completion(merge_base_command, {})
    merge_base = merge_base_process.stdout.strip()

    committed_diff_command = ["git", "--no-pager", "diff", merge_base, "HEAD"]
    get_untracked_files_diff_command_str = "git ls-files --others --exclude-standard -z | xargs -0 -I {} find {} -maxdepth 0 -type f -print0 | xargs -0 -I {} git --no-pager diff --no-index /dev/null {}"
    uncommitted_diff_command = [
        "bash",
        "-c",
        "git --no-pager diff HEAD; " + get_untracked_files_diff_command_str,
    ]
    complete_diff_command = [
        "bash",
        "-c",
        f"git --no-pager diff {shlex.quote(merge_base)}; " + get_untracked_files_diff_command_str,
    ]
    with ObservableThreadPoolExecutor(environment.concurrency_group, max_workers=3) as ex:
        futs = {
            "committed_diff": ex.submit(
                _run_diff_accepting_changes,
                environment,
                committed_diff_command,
                f"Failed to get committed diff from {source_branch} to HEAD",
                task_id,
                message_queue,
            ),
            "uncommitted_diff": ex.submit(
                _run_diff_accepting_changes,
                environment,
                uncommitted_diff_command,
                "Failed to get uncommitted diff",
                task_id,
                message_queue,
            ),
            "complete_diff": ex.submit(
                _run_diff_accepting_changes,
                environment,
                complete_diff_command,
                "Failed to get complete diff",
                task_id,
                message_queue,
            ),
            # not put into DiffArtifact yet, but we want to wait on it.
            "no_nested_git_repos": ex.submit(
                _check_and_warn_on_nested_git_repos,
                environment,
                task_id,
                message_queue,
            ),
        }
        results = {k: f.result() for k, f in futs.items()}

    return DiffArtifact(
        committed_diff=results["committed_diff"],
        uncommitted_diff=results["uncommitted_diff"],
        complete_diff=results["complete_diff"],
    )


def _create_usage_artifact(environment: Environment) -> UsageArtifact:
    """Create a unified usage artifact with both usage types (cost and token)."""
    tokens = 0
    cost_usd = 0
    token_state_content = get_state_file_contents(environment, TOKEN_AND_COST_STATE_FILE)
    if token_state_content:
        try:
            token_state = json.loads(token_state_content)
            cost_usd = token_state.get("cost_usd", 0.0)
            tokens = token_state.get("tokens", 0)
        except json.decoder.JSONDecodeError:
            logger.warning("Failed to parse token state file, resetting to zero")
    return UsageArtifact(
        cost_usd_info=cost_usd,
        token_info=tokens,
    )


def _create_todo_list_artifact(tool_input: ToolInput | None) -> TodoListArtifact:
    """Create a TodoListArtifact from tool input."""
    todos = []
    for todo_data in (tool_input or {}).get("todos", []):
        # Ensure all fields have proper types and defaults
        todo_item = TodoItem(
            id=str(todo_data.get("id", "")),
            content=str(todo_data.get("content", "")),
            status=TodoStatus(todo_data.get("status", TodoStatus.PENDING)),
            priority=TodoPriority(todo_data.get("priority", TodoPriority.MEDIUM)),
        )
        todos.append(todo_item)

    return TodoListArtifact(todos=todos)


def _create_suggestions_artifact(tool_result: ToolResultBlock) -> SuggestionsArtifact:
    """Create a SuggestionsArtifact from tool result."""
    # For suggestions, the content should always be ImbueCLIToolContent
    assert isinstance(tool_result.content, ImbueCLIToolContent)
    return SuggestionsArtifact(content=tool_result.content)


def _make_file_artifact(
    artifact_name: str,
    environment: Environment,
    source_branch: str,
    task_id: TaskID,
    message_queue: Queue[Message],
    tool_input: ToolInput | None = None,
    tool_result: ToolResultBlock | None = None,
) -> Path:
    """Generates artifacts of type artifact_name and saves them into target_file"""
    target_file = environment.get_artifacts_path() / f"{artifact_name}-{generate_id()}"

    artifact: ArtifactUnion
    if artifact_name == ArtifactType.DIFF:
        artifact = _create_diff_artifact(source_branch, environment, task_id, message_queue)
        json_content = artifact.model_dump_json(indent=2)
        environment.write_file(str(target_file), json_content)
    elif artifact_name == ArtifactType.PLAN:
        artifact = _create_todo_list_artifact(tool_input)
        json_content = artifact.model_dump_json(indent=2)
        environment.write_file(str(target_file), json_content)
    elif artifact_name == ArtifactType.SUGGESTIONS:
        assert tool_result is not None
        artifact = _create_suggestions_artifact(tool_result)
        json_content = artifact.model_dump_json(indent=2)
        environment.write_file(str(target_file), json_content)
    elif artifact_name == ArtifactType.USAGE:
        artifact = _create_usage_artifact(environment)
        json_content = artifact.model_dump_json(indent=2)
        environment.write_file(str(target_file), json_content)
    else:
        raise IllegalOperationError(f"Unknown artifact name: {artifact_name}")

    assert environment.exists(str(target_file)), f"Artifact {target_file} does not exist"
    return target_file
