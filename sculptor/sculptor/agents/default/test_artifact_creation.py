from pathlib import Path
from queue import Queue
from typing import Final

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.agents.default.artifact_creation import _check_and_warn_on_nested_git_repos
from sculptor.agents.default.artifact_creation import _create_diff_artifact
from sculptor.interfaces.agents.agent import Message
from sculptor.interfaces.agents.agent import TaskID
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.agents.artifacts import DiffArtifact
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.primitives.ids import LocalEnvironmentID
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.tasks.handlers.run_agent.git import run_git_command_in_environment

_FILE_CONTENTS: Final[str] = """def foo() -> None:
    pass"""
_NEW_FILE_CONTENTS: Final[str] = """def foo() -> None:
    print('this is new!')"""
_ANOTHER_FILE_CONTENTS: Final[str] = """def bar() -> int:
    return 42"""
_FILE_PATH: Final[str] = "main.py"
_ANOTHER_FILE_PATH: Final[str] = "another.py"


def _create_local_environment_from_local_path(
    path: Path, environment_config: LocalEnvironmentConfig, test_root_concurrency_group: ConcurrencyGroup
) -> LocalEnvironment:
    environment = LocalEnvironment(
        environment_id=LocalEnvironmentID(str(path)),
        config=environment_config,
        project_id=ProjectID(),
        concurrency_group=test_root_concurrency_group,
    )
    return environment


def _setup_repo_in_environment_with_initial_files_commit(environment: LocalEnvironment) -> str:
    """Setup a git repo with an initial commit."""
    environment.write_file(str(environment.get_workspace_path() / _FILE_PATH), _FILE_CONTENTS)
    run_git_command_in_environment(environment=environment, command=["git", "init"])
    run_git_command_in_environment(environment=environment, command=["git", "add", "."])
    run_git_command_in_environment(environment=environment, command=["git", "commit", "-m", "initial commit"])
    _, commit_hash, _ = run_git_command_in_environment(environment=environment, command=["git", "rev-parse", "HEAD"])
    return commit_hash.strip()


def _create_source_branch(environment: LocalEnvironment, branch_name: str = "main") -> None:
    """Create and checkout a source branch."""
    # Check if branch already exists, only create if it doesn't
    exit_code, stdout, _ = run_git_command_in_environment(
        environment=environment, command=["git", "branch", "--list", branch_name]
    )
    if not stdout.strip():
        # Branch doesn't exist, create it
        run_git_command_in_environment(environment=environment, command=["git", "branch", branch_name])


def _create_feature_branch(environment: LocalEnvironment, branch_name: str = "feature") -> None:
    """Create and checkout a feature branch."""
    run_git_command_in_environment(environment=environment, command=["git", "checkout", "-b", branch_name])


@pytest.fixture
def environment_config() -> LocalEnvironmentConfig:
    return LocalEnvironmentConfig()


@pytest.fixture
def environment_and_initial_repo_commit_hash(
    tmp_path: Path, environment_config: LocalEnvironmentConfig, test_root_concurrency_group: ConcurrencyGroup
) -> tuple[LocalEnvironment, str]:
    environment = _create_local_environment_from_local_path(tmp_path, environment_config, test_root_concurrency_group)
    initial_repo_commit_hash = _setup_repo_in_environment_with_initial_files_commit(environment=environment)
    return environment, initial_repo_commit_hash


def test_create_diff_artifact_with_no_changes(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact returns empty diffs when there are no changes."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    assert diff_artifact.committed_diff == ""
    assert diff_artifact.uncommitted_diff == ""
    assert diff_artifact.complete_diff == ""


def test_create_diff_artifact_with_committed_changes(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact correctly captures committed changes."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    # Make a change and commit it
    file_path = str(environment.get_workspace_path() / _FILE_PATH)
    environment.write_file(file_path, _NEW_FILE_CONTENTS)
    run_git_command_in_environment(environment=environment, command=["git", "add", "."])
    run_git_command_in_environment(environment=environment, command=["git", "commit", "-m", "update main.py"])
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    # Committed diff should show the change
    assert "def foo() -> None:" in diff_artifact.committed_diff
    assert "print('this is new!')" in diff_artifact.committed_diff
    assert "-    pass" in diff_artifact.committed_diff
    # Uncommitted diff should be empty
    assert diff_artifact.uncommitted_diff == ""
    # Complete diff should match committed diff
    assert diff_artifact.complete_diff == diff_artifact.committed_diff


def test_create_diff_artifact_with_uncommitted_changes(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact correctly captures uncommitted changes."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    # Make a change but don't commit it
    file_path = str(environment.get_workspace_path() / _FILE_PATH)
    environment.write_file(file_path, _NEW_FILE_CONTENTS)
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    # Committed diff should be empty
    assert diff_artifact.committed_diff == ""
    # Uncommitted diff should show the change
    assert "def foo() -> None:" in diff_artifact.uncommitted_diff
    assert "print('this is new!')" in diff_artifact.uncommitted_diff
    assert "-    pass" in diff_artifact.uncommitted_diff
    # Complete diff should match uncommitted diff
    assert diff_artifact.complete_diff == diff_artifact.uncommitted_diff


def test_create_diff_artifact_with_both_committed_and_uncommitted_changes(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact correctly captures both committed and uncommitted changes."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    # Make a change and commit it
    file_path = str(environment.get_workspace_path() / _FILE_PATH)
    environment.write_file(file_path, _NEW_FILE_CONTENTS)
    run_git_command_in_environment(environment=environment, command=["git", "add", "."])
    run_git_command_in_environment(environment=environment, command=["git", "commit", "-m", "update main.py"])
    # Make another change but don't commit it
    another_file_path = str(environment.get_workspace_path() / _ANOTHER_FILE_PATH)
    environment.write_file(another_file_path, _ANOTHER_FILE_CONTENTS)
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    # Committed diff should show the first change
    assert "def foo() -> None:" in diff_artifact.committed_diff
    assert "print('this is new!')" in diff_artifact.committed_diff
    # Uncommitted diff should show the second change
    assert "def bar() -> int:" in diff_artifact.uncommitted_diff
    assert "return 42" in diff_artifact.uncommitted_diff
    # Complete diff should show both changes
    assert "print('this is new!')" in diff_artifact.complete_diff
    assert "def bar() -> int:" in diff_artifact.complete_diff


def test_create_diff_artifact_with_untracked_file(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact correctly captures untracked files in uncommitted diff."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    # Create a new untracked file
    new_file_path = str(environment.get_workspace_path() / "untracked.py")
    environment.write_file(new_file_path, _ANOTHER_FILE_CONTENTS)
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    # Committed diff should be empty
    assert diff_artifact.committed_diff == ""
    # Uncommitted diff should show the new file
    assert "def bar() -> int:" in diff_artifact.uncommitted_diff
    assert "return 42" in diff_artifact.uncommitted_diff
    assert "+++ b" in diff_artifact.uncommitted_diff
    assert "untracked.py" in diff_artifact.uncommitted_diff
    # Complete diff should match uncommitted diff
    assert "def bar() -> int:" in diff_artifact.complete_diff


def test_create_diff_artifact_with_multiple_commits(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact correctly captures multiple commits."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    # Make first change and commit it
    file_path = str(environment.get_workspace_path() / _FILE_PATH)
    environment.write_file(file_path, _NEW_FILE_CONTENTS)
    run_git_command_in_environment(environment=environment, command=["git", "add", "."])
    run_git_command_in_environment(environment=environment, command=["git", "commit", "-m", "first commit"])
    # Make second change and commit it
    another_file_path = str(environment.get_workspace_path() / _ANOTHER_FILE_PATH)
    environment.write_file(another_file_path, _ANOTHER_FILE_CONTENTS)
    run_git_command_in_environment(environment=environment, command=["git", "add", "."])
    run_git_command_in_environment(environment=environment, command=["git", "commit", "-m", "second commit"])
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    # Committed diff should show both changes
    assert "print('this is new!')" in diff_artifact.committed_diff
    assert "def bar() -> int:" in diff_artifact.committed_diff
    assert "return 42" in diff_artifact.committed_diff
    # Uncommitted diff should be empty
    assert diff_artifact.uncommitted_diff == ""


def test_check_and_warn_on_nested_git_repos_with_no_nested_repos(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _check_and_warn_on_nested_git_repos returns empty when there are no nested repos."""
    environment, _ = environment_and_initial_repo_commit_hash
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    result = _check_and_warn_on_nested_git_repos(environment=environment, task_id=task_id, message_queue=message_queue)
    assert result == ""
    assert message_queue.empty()


def test_check_and_warn_on_nested_git_repos_with_nested_repo(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _check_and_warn_on_nested_git_repos detects and warns about nested repos."""
    environment, _ = environment_and_initial_repo_commit_hash
    # Create a nested git repo
    nested_repo_path = environment.get_workspace_path() / "nested_repo"
    nested_repo_path.mkdir(parents=True, exist_ok=True)
    run_git_command_in_environment(
        environment=environment,
        command=["git", "init", str(nested_repo_path.relative_to(environment.get_workspace_path()))],
    )
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    result = _check_and_warn_on_nested_git_repos(environment=environment, task_id=task_id, message_queue=message_queue)
    # Should detect the nested repo directory
    assert "nested_repo" in result
    # Should have added a warning message to the queue
    assert not message_queue.empty()
    warning_message = message_queue.get()
    assert isinstance(warning_message, WarningAgentMessage)
    assert "nested git repository" in warning_message.message.lower()
    assert "nested_repo" in warning_message.message


def test_check_and_warn_on_nested_git_repos_with_multiple_nested_repos(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _check_and_warn_on_nested_git_repos detects multiple nested repos."""
    environment, _ = environment_and_initial_repo_commit_hash
    # Create multiple nested git repos
    nested_repo_path_1 = environment.get_workspace_path() / "nested_repo_1"
    nested_repo_path_1.mkdir(parents=True, exist_ok=True)
    run_git_command_in_environment(
        environment=environment,
        command=["git", "init", str(nested_repo_path_1.relative_to(environment.get_workspace_path()))],
    )
    nested_repo_path_2 = environment.get_workspace_path() / "nested_repo_2"
    nested_repo_path_2.mkdir(parents=True, exist_ok=True)
    run_git_command_in_environment(
        environment=environment,
        command=["git", "init", str(nested_repo_path_2.relative_to(environment.get_workspace_path()))],
    )
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    result = _check_and_warn_on_nested_git_repos(environment=environment, task_id=task_id, message_queue=message_queue)
    # Should detect both nested repo directories
    assert "nested_repo" in result
    # Should have added a warning message to the queue
    assert not message_queue.empty()
    warning_message = message_queue.get()
    assert isinstance(warning_message, WarningAgentMessage)
    assert "nested git repository" in warning_message.message.lower()


def test_check_and_warn_on_nested_git_repos_without_task_id_and_message_queue(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _check_and_warn_on_nested_git_repos works without task_id and message_queue."""
    environment, _ = environment_and_initial_repo_commit_hash
    # Create a nested git repo
    nested_repo_path = environment.get_workspace_path() / "nested_repo"
    nested_repo_path.mkdir(parents=True, exist_ok=True)
    run_git_command_in_environment(
        environment=environment,
        command=["git", "init", str(nested_repo_path.relative_to(environment.get_workspace_path()))],
    )
    # Should not raise an error when task_id and message_queue are None
    result = _check_and_warn_on_nested_git_repos(environment=environment, task_id=None, message_queue=None)
    # Should still detect the nested repo
    assert "nested_repo" in result


def test_create_diff_artifact_filters_out_nested_git_repos(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact filters out nested git repos from diffs."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    # Create a nested git repo with a file
    nested_repo_path = environment.get_workspace_path() / "nested_repo"
    nested_repo_path.mkdir(parents=True, exist_ok=True)
    run_git_command_in_environment(
        environment=environment,
        command=["git", "init", str(nested_repo_path.relative_to(environment.get_workspace_path()))],
    )
    nested_file = nested_repo_path / "nested_file.py"
    environment.write_file(str(nested_file), "# nested file")
    # Create a regular untracked file
    untracked_file = str(environment.get_workspace_path() / "untracked.py")
    environment.write_file(untracked_file, _ANOTHER_FILE_CONTENTS)
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    # The diff should include the regular untracked file
    assert "untracked.py" in diff_artifact.uncommitted_diff
    # The diff should NOT include the nested repo file
    assert "nested_file.py" not in diff_artifact.uncommitted_diff
    assert "nested_repo" not in diff_artifact.uncommitted_diff
    # Should have received a warning about the nested repo
    assert not message_queue.empty()
    warning_message = message_queue.get()
    assert isinstance(warning_message, WarningAgentMessage)
    assert "nested git repository" in warning_message.message.lower()


def test_check_and_warn_on_nested_git_repos_with_worktree(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _check_and_warn_on_nested_git_repos detects and warns about git worktrees."""
    environment, _ = environment_and_initial_repo_commit_hash
    # Create a git worktree inside the repo
    worktree_path = environment.get_workspace_path() / "worktrees" / "feature-branch"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    run_git_command_in_environment(
        environment=environment,
        command=["git", "worktree", "add", str(worktree_path.relative_to(environment.get_workspace_path())), "HEAD"],
    )
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    result = _check_and_warn_on_nested_git_repos(environment=environment, task_id=task_id, message_queue=message_queue)
    # Should detect the worktree directory
    assert "worktrees/feature-branch" in result or "worktrees" in result
    # Should have added a warning message to the queue
    assert not message_queue.empty()
    warning_message = message_queue.get()
    assert isinstance(warning_message, WarningAgentMessage)
    assert "nested git repository" in warning_message.message.lower()


def test_check_and_warn_on_nested_git_repos_with_multiple_worktrees(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _check_and_warn_on_nested_git_repos detects multiple git worktrees."""
    environment, _ = environment_and_initial_repo_commit_hash
    # Create multiple git worktrees
    worktree_path_1 = environment.get_workspace_path() / "wt1"
    run_git_command_in_environment(
        environment=environment,
        command=["git", "worktree", "add", str(worktree_path_1.relative_to(environment.get_workspace_path())), "HEAD"],
    )
    worktree_path_2 = environment.get_workspace_path() / "wt2"
    run_git_command_in_environment(
        environment=environment,
        command=["git", "worktree", "add", str(worktree_path_2.relative_to(environment.get_workspace_path())), "HEAD"],
    )
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    result = _check_and_warn_on_nested_git_repos(environment=environment, task_id=task_id, message_queue=message_queue)
    # Should detect the worktree directories
    assert "wt1" in result or "wt2" in result
    # Should have added a warning message to the queue
    assert not message_queue.empty()
    warning_message = message_queue.get()
    assert isinstance(warning_message, WarningAgentMessage)
    assert "nested git repository" in warning_message.message.lower()


def test_create_diff_artifact_filters_out_worktrees(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that _create_diff_artifact filters out git worktrees from diffs."""
    environment, _ = environment_and_initial_repo_commit_hash
    _create_source_branch(environment, "main")
    _create_feature_branch(environment, "feature")
    # Create a git worktree with a file
    worktree_path = environment.get_workspace_path() / "worktree_branch"
    run_git_command_in_environment(
        environment=environment,
        command=["git", "worktree", "add", str(worktree_path.relative_to(environment.get_workspace_path())), "HEAD"],
    )
    # Add a file in the worktree
    worktree_file = worktree_path / "worktree_file.py"
    environment.write_file(str(worktree_file), "# worktree file")
    # Create a regular untracked file
    untracked_file = str(environment.get_workspace_path() / "untracked.py")
    environment.write_file(untracked_file, _ANOTHER_FILE_CONTENTS)
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    diff_artifact = _create_diff_artifact(
        source_branch="main", environment=environment, task_id=task_id, message_queue=message_queue
    )
    assert isinstance(diff_artifact, DiffArtifact)
    # The diff should include the regular untracked file
    assert "untracked.py" in diff_artifact.uncommitted_diff
    # The diff should NOT include the worktree file
    assert "worktree_file.py" not in diff_artifact.uncommitted_diff
    assert "worktree_branch/" not in diff_artifact.uncommitted_diff
    # Should have received a warning about the worktree
    assert not message_queue.empty()
    warning_message = message_queue.get()
    assert isinstance(warning_message, WarningAgentMessage)
    assert "nested git repository" in warning_message.message.lower()


def test_worktree_and_nested_repo_together(
    environment_and_initial_repo_commit_hash: tuple[LocalEnvironment, str],
) -> None:
    """Test that both worktrees and nested repos are detected correctly when both exist."""
    environment, _ = environment_and_initial_repo_commit_hash
    # Create a git worktree
    worktree_path = environment.get_workspace_path() / "my_worktree"
    run_git_command_in_environment(
        environment=environment,
        command=["git", "worktree", "add", str(worktree_path.relative_to(environment.get_workspace_path())), "HEAD"],
    )
    # Create a nested git repo
    nested_repo_path = environment.get_workspace_path() / "nested_repo"
    nested_repo_path.mkdir(parents=True, exist_ok=True)
    run_git_command_in_environment(
        environment=environment,
        command=["git", "init", str(nested_repo_path.relative_to(environment.get_workspace_path()))],
    )
    task_id = TaskID()
    message_queue: Queue[Message] = Queue()
    result = _check_and_warn_on_nested_git_repos(environment=environment, task_id=task_id, message_queue=message_queue)
    # Should detect both directories
    assert ("my_worktree" in result and "nested_repo" in result) or (
        "my_worktree" in result or "nested_repo" in result
    )
    # Should have added a warning message to the queue
    assert not message_queue.empty()
    warning_message = message_queue.get()
    assert isinstance(warning_message, WarningAgentMessage)
    assert "nested git repository" in warning_message.message.lower()
