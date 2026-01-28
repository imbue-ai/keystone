from pathlib import Path
from queue import Queue
from threading import Event
from typing import Generator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches_test import expect_at_least_logged_errors
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.git_snapshot import FullLocalGitRepo
from imbue_core.sculptor.state.messages import Message
from imbue_core.subprocess_utils import ProcessError
from sculptor.agents.default.artifact_creation import _create_diff_artifact
from sculptor.agents.default.claude_code_sdk.diff_tracker import DiffTracker
from sculptor.agents.default.claude_code_sdk.diff_tracker_test import normalize_diff
from sculptor.agents.default.claude_code_sdk.errors import ClaudeAPIError
from sculptor.agents.default.claude_code_sdk.output_processor import ClaudeOutputProcessor
from sculptor.interfaces.agents.errors import AgentClientError
from sculptor.interfaces.agents.errors import AgentTransientError
from sculptor.interfaces.environments.base import LocalImage
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.environments.local_environment import LocalEnvironmentConfig
from sculptor.services.environment_service.providers.local.environment_utils import build_local_environment
from sculptor.services.environment_service.providers.local.image_utils import build_local_image
from sculptor.testing.mock_repo import MockRepoState


@pytest.fixture
def git_repo(tmp_path: Path, test_root_concurrency_group: ConcurrencyGroup) -> Path:
    """Create a git repository with some commits using MockRepoState."""
    repo_path = tmp_path / "test_repo"

    # Define a simple repo state
    initial_state = FullLocalGitRepo(
        git_user_email="test@example.com",
        git_user_name="Test User",
        git_diff=None,
        git_branch="main",
        main_history=(),
    )

    # Create the repo using MockRepoState
    mock_repo = MockRepoState.build_locally(
        state=initial_state, local_dir=repo_path, concurrency_group=test_root_concurrency_group
    )
    return mock_repo.base_path


@pytest.fixture
def local_image(git_repo: Path) -> LocalImage:
    return build_local_image(git_repo, project_id=ProjectID())


@pytest.fixture
def git_local_environment(
    local_image: LocalImage,
    test_root_concurrency_group: ConcurrencyGroup,
) -> Generator[LocalEnvironment, None, None]:
    environment = None
    try:
        environment = build_local_environment(local_image, LocalEnvironmentConfig(), test_root_concurrency_group)
        # Git repo is in workspace directory (default cwd for run_process_in_background)
        # We're implicitly on main.
        # Write test.txt to workspace directory (relative to sandbox root)
        environment.write_file(str(environment.get_workspace_path() / "test.txt"), "Initial content")
        environment.run_process_to_completion(["git", "add", "test.txt"], {})
        environment.run_process_to_completion(["git", "commit", "-m", "Initial commit"], {})
        # Create and checkout a new branch
        environment.run_process_to_completion(["git", "checkout", "-b", "new-branch"], {})
        yield environment
    finally:
        if environment is not None:
            environment.close()


@pytest.fixture
def git_docker_environment(docker_environment: DockerEnvironment) -> Generator[DockerEnvironment, None, None]:
    """Docker environment with git repository initialized, similar to local_environment."""
    # Initialize git repository in workspace
    workspace_path = docker_environment.get_workspace_path()
    docker_environment.run_process_to_completion(["mkdir", "-p", str(workspace_path)], {})

    # Configure git user
    docker_environment.run_process_to_completion(["git", "config", "--global", "user.name", "Test User"], {})
    docker_environment.run_process_to_completion(["git", "config", "--global", "user.email", "test@example.com"], {})

    # Re-Initialize a clean git repo
    docker_environment.run_process_to_completion(["git", "init"], {})
    docker_environment.run_process_to_completion(["git", "reset", "--hard"], {}, cwd=str(workspace_path))
    docker_environment.run_process_to_completion(["git", "clean", "-fd"], {}, cwd=str(workspace_path))

    # Create main branch (modern git uses main instead of master)
    process = docker_environment.run_process_to_completion(
        ["git", "checkout", "-b", "main"], {}, is_checked_after=False
    )
    if process.returncode != 0:
        docker_environment.run_process_to_completion(["git", "checkout", "main"], {})

    # Create initial commit
    docker_environment.write_file(str(workspace_path / "test.txt"), "Initial content")
    docker_environment.run_process_to_completion(["git", "add", "test.txt"], {})
    docker_environment.run_process_to_completion(["git", "commit", "-m", "Initial commit"], {})

    # Create and checkout a new branch
    docker_environment.run_process_to_completion(["git", "checkout", "-b", "new-branch"], {})

    yield docker_environment


@pytest.fixture(params=["local", "docker"])
def git_environment(
    request: pytest.FixtureRequest, git_local_environment: LocalEnvironment, git_docker_environment: DockerEnvironment
) -> LocalEnvironment | DockerEnvironment:
    """Parametrized fixture that provides either local or docker environment with git setup."""
    if request.param == "docker":
        return git_docker_environment
    return git_local_environment


def test_create_diff_artifact_no_changes(git_environment: LocalEnvironment | DockerEnvironment) -> None:
    """Test when there are no changes at all."""
    diff_artifact = _create_diff_artifact(source_branch="main", environment=git_environment)
    assert diff_artifact.committed_diff.strip() == ""
    assert diff_artifact.uncommitted_diff.strip() == ""
    assert diff_artifact.complete_diff.strip() == ""


def test_create_diff_artifact_untracked_files(
    git_environment: LocalEnvironment | DockerEnvironment,
) -> None:
    """Test when there are only untracked files."""
    # Create an untracked file (in workspace directory where git is)
    git_environment.write_file(
        str(git_environment.get_workspace_path() / "untracked.txt"), "This is an untracked file"
    )

    diff_artifact = _create_diff_artifact(source_branch="main", environment=git_environment)

    expected_uncommitted_diff = """diff --git a/untracked.txt b/untracked.txt
new file mode 100644
index 0000000..38c2b40
--- /dev/null
+++ b/untracked.txt
@@ -0,0 +1 @@
+This is an untracked file
\\ No newline at end of file
"""

    assert normalize_diff(diff_artifact.uncommitted_diff).strip() == normalize_diff(expected_uncommitted_diff).strip()
    assert normalize_diff(diff_artifact.complete_diff).strip() == normalize_diff(expected_uncommitted_diff).strip()
    assert diff_artifact.committed_diff.strip() == ""


def test_create_diff_artifact_changes_to_tracked_files(
    git_environment: LocalEnvironment | DockerEnvironment,
) -> None:
    """Test when there are changes to already tracked files."""
    # Modify an existing tracked file
    git_environment.write_file(str(git_environment.get_workspace_path() / "test.txt"), "Modified content")

    diff_artifact = _create_diff_artifact(source_branch="main", environment=git_environment)
    expected_uncommitted_diff = """diff --git a/test.txt b/test.txt
index 960c351..b7e1a1b 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Initial content
\\ No newline at end of file
+Modified content
\\ No newline at end of file
"""

    assert normalize_diff(diff_artifact.uncommitted_diff).strip() == normalize_diff(expected_uncommitted_diff).strip()
    assert normalize_diff(diff_artifact.complete_diff).strip() == normalize_diff(expected_uncommitted_diff).strip()
    assert diff_artifact.committed_diff.strip() == "", f"Committed diff: {diff_artifact.committed_diff}"


def test_create_diff_artifact_file_deletions(git_environment: LocalEnvironment | DockerEnvironment) -> None:
    """Test when files are deleted."""
    # Delete an existing file
    git_environment.run_process_to_completion(["rm", "test.txt"], {})

    diff_artifact = _create_diff_artifact(source_branch="main", environment=git_environment)
    expected_uncommitted_diff = """diff --git a/test.txt b/test.txt
deleted file mode 100644
index 960c351..0000000
--- a/test.txt
+++ /dev/null
@@ -1 +0,0 @@
-Initial content
\\ No newline at end of file
"""

    assert normalize_diff(diff_artifact.uncommitted_diff).strip() == normalize_diff(expected_uncommitted_diff).strip()
    assert normalize_diff(diff_artifact.complete_diff).strip() == normalize_diff(expected_uncommitted_diff).strip()
    assert diff_artifact.committed_diff.strip() == "", f"Committed diff: {diff_artifact.committed_diff}"


def test_create_diff_artifact_only_committed_changes(
    git_environment: LocalEnvironment | DockerEnvironment,
) -> None:
    """Test when there are only committed changes (no uncommitted changes)."""
    # Create a new file and commit it
    git_environment.write_file(str(git_environment.get_workspace_path() / "committed1.txt"), "First committed file")
    git_environment.run_process_to_completion(["git", "add", "committed1.txt"], {})
    git_environment.run_process_to_completion(["git", "commit", "-m", "Add first file"], {})

    # Modify existing file and commit
    git_environment.write_file(str(git_environment.get_workspace_path() / "test.txt"), "Modified and committed")
    git_environment.run_process_to_completion(["git", "add", "test.txt"], {})
    git_environment.run_process_to_completion(["git", "commit", "-m", "Modify test file"], {})

    diff_artifact = _create_diff_artifact(source_branch="main", environment=git_environment)

    expected_committed_diff = """diff --git a/committed1.txt b/committed1.txt
new file mode 100644
index 0000000..31adb58
--- /dev/null
+++ b/committed1.txt
@@ -0,0 +1 @@
+First committed file
\\ No newline at end of file
diff --git a/test.txt b/test.txt
index 960c351..1cb37ac 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Initial content
\\ No newline at end of file
+Modified and committed
\\ No newline at end of file
"""

    assert normalize_diff(diff_artifact.committed_diff).strip() == normalize_diff(expected_committed_diff).strip()
    assert diff_artifact.uncommitted_diff.strip() == ""
    assert normalize_diff(diff_artifact.complete_diff).strip() == normalize_diff(expected_committed_diff).strip()


def test_create_diff_artifact_committed_and_uncommitted_changes(
    git_environment: LocalEnvironment | DockerEnvironment,
) -> None:
    """Test with a mix of committed and uncommitted changes."""
    # First, make some commits
    git_environment.write_file(str(git_environment.get_workspace_path() / "committed.txt"), "Committed content")
    git_environment.run_process_to_completion(["git", "add", "committed.txt"], {})
    git_environment.run_process_to_completion(["git", "commit", "-m", "Add committed file"], {})

    # Modify test.txt and commit
    git_environment.write_file(str(git_environment.get_workspace_path() / "test.txt"), "First modification")
    git_environment.run_process_to_completion(["git", "add", "test.txt"], {})
    git_environment.run_process_to_completion(["git", "commit", "-m", "First modification"], {})

    # Now make uncommitted changes
    git_environment.write_file(
        str(git_environment.get_workspace_path() / "test.txt"), "Second modification (uncommitted)"
    )
    git_environment.write_file(str(git_environment.get_workspace_path() / "untracked.txt"), "Untracked file")
    git_environment.write_file(
        str(git_environment.get_workspace_path() / "staged_uncommitted.txt"), "Staged but not committed"
    )
    git_environment.run_process_to_completion(["git", "add", "staged_uncommitted.txt"], {})

    diff_artifact = _create_diff_artifact(source_branch="main", environment=git_environment)

    expected_committed_diff = """diff --git a/committed.txt b/committed.txt
new file mode 100644
index 0000000..442e274
--- /dev/null
+++ b/committed.txt
@@ -0,0 +1 @@
+Committed content
\\ No newline at end of file
diff --git a/test.txt b/test.txt
index 960c351..934337b 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Initial content
\\ No newline at end of file
+First modification
\\ No newline at end of file
"""

    expected_uncommitted_diff = """diff --git a/staged_uncommitted.txt b/staged_uncommitted.txt
new file mode 100644
index 0000000..7cddae9
--- /dev/null
+++ b/staged_uncommitted.txt
@@ -0,0 +1 @@
+Staged but not committed
\\ No newline at end of file
diff --git a/test.txt b/test.txt
index 934337b..f3233e4 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-First modification
\\ No newline at end of file
+Second modification (uncommitted)
\\ No newline at end of file
diff --git a/untracked.txt b/untracked.txt
new file mode 100644
index 0000000..7934298
--- /dev/null
+++ b/untracked.txt
@@ -0,0 +1 @@
+Untracked file
\\ No newline at end of file
"""

    expected_complete_diff = """ diff --git a/committed.txt b/committed.txt
new file mode 100644
index 0000000..442e274
--- /dev/null
+++ b/committed.txt
@@ -0,0 +1 @@
+Committed content
\\ No newline at end of file
diff --git a/staged_uncommitted.txt b/staged_uncommitted.txt
new file mode 100644
index 0000000..7cddae9
--- /dev/null
+++ b/staged_uncommitted.txt
@@ -0,0 +1 @@
+Staged but not committed
\\ No newline at end of file
diff --git a/test.txt b/test.txt
index 960c351..f3233e4 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Initial content
\\ No newline at end of file
+Second modification (uncommitted)
\\ No newline at end of file
diff --git a/untracked.txt b/untracked.txt
new file mode 100644
index 0000000..7934298
--- /dev/null
+++ b/untracked.txt
@@ -0,0 +1 @@
+Untracked file
\\ No newline at end of file
"""
    assert normalize_diff(diff_artifact.committed_diff).strip() == normalize_diff(expected_committed_diff).strip()
    assert normalize_diff(diff_artifact.uncommitted_diff).strip() == normalize_diff(expected_uncommitted_diff).strip()
    assert normalize_diff(diff_artifact.complete_diff).strip() == normalize_diff(expected_complete_diff).strip()


def test_create_diff_artifact_raises_git_error(
    git_environment: LocalEnvironment | DockerEnvironment,
) -> None:
    with pytest.raises(ProcessError):
        _create_diff_artifact(source_branch="fake-branch", environment=git_environment)


@pytest.mark.flaky
def test_stream_end_successful(docker_environment: DockerEnvironment) -> None:
    claude_json = """
{"type":"system","subtype":"init","cwd":"/user_home/workspace","session_id":"43637bf5-8752-4f95-92e1-450c800b51d2","tools":["Task","Bash","Glob","Grep","LS","exit_plan_mode","Read","Edit","MultiEdit","Write","NotebookRead","NotebookEdit","WebFetch","TodoRead","TodoWrite","WebSearch","mcp__imbue__check","mcp__imbue__verify","ListMcpResourcesTool","ReadMcpResourceTool"],"mcp_servers":[{"name":"imbue","status":"connected"},{"name":"imbue_tools","status":"failed"}],"model":"claude-sonnet-4-20250514","permissionMode":"default","apiKeySource":"ANTHROPIC_API_KEY"}
{"type":"result","subtype":"success","is_error":false,"duration_ms":5939,"duration_api_ms":5723,"num_turns":1,"result":"Hello! I'm here to help you with your software engineering tasks. I can see you're working on a branch called `amyhu/add-claude-exceptions` with some modified files related to error handling in the Claude Code SDK. \\n\\nWhat would you like me to help you with today?","session_id":"773f86df-b066-4b2b-926e-c6e686178cdc","total_cost_usd":0.256635,"usage":{"input_tokens":4,"cache_creation_input_tokens":13392,"cache_read_input_tokens":0,"output_tokens":73,"server_tool_use":{"web_search_requests":0},"service_tier":"standard"}}
"""

    file_path = str(docker_environment.get_root_path() / "test.jsonl")
    docker_environment.write_file(file_path, claude_json)
    command = ["bash", "-c", f'while IFS= read -r line; do echo "$line"; done < {file_path}']
    process = docker_environment.run_process_in_background(command, {})
    output_message_queue: Queue[Message] = Queue()
    ClaudeOutputProcessor.build_and_process_output(
        process=process,
        source_command=" ".join(command),
        output_message_queue=output_message_queue,
        environment=docker_environment,
        diff_tracker=DiffTracker(docker_environment, output_message_queue),
        source_branch="main",
        task_id=TaskID("26charsjnbase32wchr1be10w7"),
        session_id_written_event=Event(),
    )


def test_raise_claude_client_error(docker_environment: DockerEnvironment) -> None:
    """
    This test is to ensure that we raise a client error when claude code returns a non-api error.
    """
    unsuccessful_claude_json = """
{"type":"system","subtype":"init","cwd":"/user_home/workspace","session_id":"b0c847dd-fc8e-48cc-94a7-773adc1f1427","tools":["Task","Bash","Glob","Grep","LS","exit_plan_mode","Read","Edit","MultiEdit","Write","NotebookRead","NotebookEdit","WebFetch","TodoRead","TodoWrite","WebSearch","mcp__imbue__check","mcp__imbue__verify","ListMcpResourcesTool","ReadMcpResourceTool"],"mcp_servers":[{"name":"imbue","status":"connected"},{"name":"imbue_tools","status":"failed"}],"model":"claude-opus-4-20250514","permissionMode":"default","apiKeySource":"ANTHROPIC_API_KEY"}
{"type":"assistant","message":{"id":"c9aa0b40-552d-4f80-a148-33ce5d227360","model":"<synthetic>","role":"assistant","stop_reason":"stop_sequence","stop_sequence":"","type":"message","usage":{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"server_tool_use":{"web_search_requests":0}},"content":[{"type":"text","text":"Repeated server overload with Opus model"}]},"parent_tool_use_id":null,"session_id":"b0c847dd-fc8e-48cc-94a7-773adc1f1427"}
{"type":"result","subtype":"success","is_error":true,"duration_ms":45716,"duration_api_ms":1176,"num_turns":1,"result":"Repeated server overload with Opus model","session_id":"b0c847dd-fc8e-48cc-94a7-773adc1f1427","total_cost_usd":0.0003832,"usage":{"input_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":0,"server_tool_use":{"web_search_requests":0}}}
"""

    file_path = str(docker_environment.get_root_path() / "test.jsonl")
    docker_environment.write_file(file_path, unsuccessful_claude_json)
    command = ["bash", "-c", f'while IFS= read -r line; do echo "$line"; done < {file_path}']
    process = docker_environment.run_process_in_background(command, {})
    output_message_queue: Queue[Message] = Queue()
    with pytest.raises(AgentClientError):
        ClaudeOutputProcessor.build_and_process_output(
            process=process,
            source_command=" ".join(command),
            output_message_queue=output_message_queue,
            environment=docker_environment,
            diff_tracker=DiffTracker(docker_environment, output_message_queue),
            source_branch="main",
            task_id=TaskID("26charsjnbase32wchr1be10w7"),
            session_id_written_event=Event(),
        )


def test_raise_claude_api_error(docker_environment: DockerEnvironment) -> None:
    """
    This test is to ensure that we raise a API error when the Claude API returns a 400 error.

    Note that it does not run in our agentic container.
    """
    unsuccessful_claude_json = """
{"type":"system","subtype":"init","cwd":"/user_home/workspace","session_id":"43637bf5-8752-4f95-92e1-450c800b51d2","tools":["Task","Bash","Glob","Grep","LS","exit_plan_mode","Read","Edit","MultiEdit","Write","NotebookRead","NotebookEdit","WebFetch","TodoRead","TodoWrite","WebSearch","mcp__imbue__check","mcp__imbue__verify","ListMcpResourcesTool","ReadMcpResourceTool"],"mcp_servers":[{"name":"imbue","status":"connected"},{"name":"imbue_tools","status":"failed"}],"model":"claude-sonnet-4-20250514","permissionMode":"default","apiKeySource":"ANTHROPIC_API_KEY"}
{"type":"assistant","message":{"id":"4642f70d-877c-4aa0-8855-a97ec32d8409","model":"<synthetic>","role":"assistant","stop_reason":"stop_sequence","stop_sequence":"","type":"message","usage":{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"server_tool_use":{"web_search_requests":0}},"content":[{"type":"text","text":"API Error: 400 {type':'error','error':{'type':'invalid_request_error','message':'input length and `max_tokens` exceed context limit: 196122 + 21333 > 200000, decrease input length or `max_tokens` and try again'}}"}]},"parent_tool_use_id":null,"session_id":"43637bf5-8752-4f95-92e1-450c800b51d2"}
{"type":"result","subtype":"success","is_error":true,"duration_ms":1282,"duration_api_ms":1383,"num_turns":442,"result":"API Error: 400 {'type':'error','error':{'type':'invalid_request_error','message':'input length and `max_tokens` exceed context limit: 196122 + 21333 > 200000, decrease input length or `max_tokens` and try again'}}","session_id":"43637bf5-8752-4f95-92e1-450c800b51d2","total_cost_usd":0.00028000000000000003,"usage":{"input_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":0,"server_tool_use":{"web_search_requests":0}}}
"""

    file_path = str(docker_environment.get_root_path() / "test.jsonl")
    docker_environment.write_file(file_path, unsuccessful_claude_json)
    command = ["bash", "-c", f'while IFS= read -r line; do echo "$line"; done < {file_path}']
    process = docker_environment.run_process_in_background(command, {})
    output_message_queue: Queue[Message] = Queue()
    with pytest.raises(ClaudeAPIError):
        ClaudeOutputProcessor.build_and_process_output(
            process=process,
            source_command=" ".join(command),
            output_message_queue=output_message_queue,
            environment=docker_environment,
            diff_tracker=DiffTracker(docker_environment, output_message_queue),
            source_branch="main",
            task_id=TaskID("26charsjnbase32wchr1be10w7"),
            session_id_written_event=Event(),
        )


def test_raise_claude_transient_error(docker_environment: DockerEnvironment) -> None:
    """
    This test is to ensure that we raise a transient error when the Claude API returns a 500 internal server error.
    """
    unsuccessful_claude_json = """
{"type":"system","subtype":"init","cwd":"/user_home/workspace","session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7","tools":["Task","Bash","Glob","Grep","LS","exit_plan_mode","Read","Edit","MultiEdit","Write","NotebookRead","NotebookEdit","WebFetch","TodoRead","TodoWrite","WebSearch","mcp__imbue__check","mcp__imbue__verify","ListMcpResourcesTool","ReadMcpResourceTool"],"mcp_servers":[{"name":"imbue","status":"connected"},{"name":"imbue_tools","status":"failed"}],"model":"claude-sonnet-4-20250514","permissionMode":"default","apiKeySource":"ANTHROPIC_API_KEY"}
{"type":"assistant","message":{"id":"msg_012vsi9duHT5ZZZcHuJTx19w","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","content":[{"type":"text","text":"I'll reduce the space between the header and subtitle, and also reduce the space above the header. Let me update the CSS:"}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":4,"cache_creation_input_tokens":2044,"cache_read_input_tokens":62606,"output_tokens":91,"service_tier":"standard"}},"parent_tool_use_id":null,"session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7"}
{"type":"assistant","message":{"id":"msg_012vsi9duHT5ZZZcHuJTx19w","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","content":[{"type":"tool_use","id":"toolu_01M61LCFJcS7EruaiWqP6iE4","name":"Read","input":{"file_path":"/user_home/workspace/assets/homepage.css"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":4,"cache_creation_input_tokens":2044,"cache_read_input_tokens":62606,"output_tokens":91,"service_tier":"standard"}},"parent_tool_use_id":null,"session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7"}
{"type":"assistant","message":{"id":"986a09ec-f89c-4340-a3f5-d772497e00a1","model":"<synthetic>","role":"assistant","stop_reason":"stop_sequence","stop_sequence":"","type":"message","usage":{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"server_tool_use":{"web_search_requests":0}},"content":[{"type":"text","text":"API Error: 500 {'type':'error','error':{'type':'api_error','message':'Internal server error'}}"}]},"parent_tool_use_id":null,"session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7"}
{"type":"result","subtype":"success","is_error":true,"duration_ms":18591,"duration_api_ms":15223,"num_turns":186,"result":"API Error: 500 {'type':'error','error':{'type':'api_error','message':'Internal server error'}}","session_id":"2b3f9218-14c9-4540-b355-7ecc6ec2d3c7","total_cost_usd":0,"usage":{"input_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":0,"server_tool_use":{"web_search_requests":0}}}
"""
    file_path = str(docker_environment.get_root_path() / "test.jsonl")
    docker_environment.write_file(file_path, unsuccessful_claude_json)
    command = ["bash", "-c", f'while IFS= read -r line; do echo "$line"; done < {file_path}']
    process = docker_environment.run_process_in_background(command, {})
    output_message_queue: Queue[Message] = Queue()
    with pytest.raises(AgentTransientError):
        ClaudeOutputProcessor.build_and_process_output(
            process=process,
            source_command=" ".join(command),
            output_message_queue=output_message_queue,
            environment=docker_environment,
            diff_tracker=DiffTracker(docker_environment, output_message_queue),
            source_branch="main",
            task_id=TaskID("26charsjnbase32wchr1be10w7"),
            session_id_written_event=Event(),
        )


def test_claude_no_explode_when_internal_tool_format_is_invalid(docker_environment: DockerEnvironment) -> None:
    errored_invocation_of_internal_tool = """{"parentUuid":"bb2e3a6f-c78f-4d07-bfd7-1b960346ea07","isSidechain":false,"userType":"external","cwd":"/code","sessionId":"22a7c334-d89b-438e-bfba-578816fb68d8","version":"2.0.0","gitBranch":"sculptor/implement-simple-sql-verification","message":{"id":"msg_01611izC2FncRyMRqeAA3gE9","type":"message","role":"assistant","model":"claude-opus-4-1-20250805","content":[{"type":"tool_use","id":"toolu_01Y8pr9UBn3nRA5zKMNJhCxp","name":"mcp__imbue_tools__verify","input":{"goal":"Implement a simple SQL system with basic functionality including CREATE TABLE, INSERT, SELECT, UPDATE, and DELETE operations. The implementation should include a parser, in-memory database storage, and an execution engine.","base_commit":"HEAD"}}],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":7,"cache_creation_input_tokens":328,"cache_read_input_tokens":27383,"cache_creation":{"ephemeral_5m_input_tokens":328,"ephemeral_1h_input_tokens":0},"output_tokens":1,"service_tier":"standard"}},"requestId":"req_011CTfGuGw2AZDr3x4o5yXqC","type":"assistant","uuid":"74591022-ff14-473a-869d-dbd00f53ad47","timestamp":"2025-09-30T21:13:04.371Z"}
{"parentUuid":"74591022-ff14-473a-869d-dbd00f53ad47","isSidechain":false,"userType":"external","cwd":"/code","sessionId":"22a7c334-d89b-438e-bfba-578816fb68d8","version":"2.0.0","gitBranch":"sculptor/implement-simple-sql-verification","type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"<tool_use_error>Error: No such tool available: mcp__imbue_tools__verify</tool_use_error>","is_error":true,"tool_use_id":"toolu_01Y8pr9UBn3nRA5zKMNJhCxp"}]},"uuid":"95fb8be1-5c06-4a59-844f-4c8a6101cddc","timestamp":"2025-09-30T21:13:04.415Z","toolUseResult":"Error: No such tool available: mcp__imbue_tools__verify"}
"""
    file_path = str(docker_environment.get_root_path() / "test.jsonl")
    docker_environment.write_file(file_path, errored_invocation_of_internal_tool)
    command = ["bash", "-c", f'while IFS= read -r line; do echo "$line"; done < {file_path}']
    process = docker_environment.run_process_in_background(command, {})
    output_message_queue: Queue[Message] = Queue()
    with expect_at_least_logged_errors({"Error loading content for tool result message"}):
        ClaudeOutputProcessor.build_and_process_output(
            process=process,
            source_command=" ".join(command),
            output_message_queue=output_message_queue,
            environment=docker_environment,
            diff_tracker=DiffTracker(docker_environment, output_message_queue),
            source_branch="main",
            task_id=TaskID("26charsjnbase32wchr1be10w7"),
            session_id_written_event=Event(),
        )
