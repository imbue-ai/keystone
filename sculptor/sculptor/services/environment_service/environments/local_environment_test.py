import shutil
import tempfile
import time
from pathlib import Path
from typing import Generator
from uuid import uuid4

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.primitives.ids import LocalEnvironmentID
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.providers.local.constants import LOCAL_SANDBOX_DIR
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.testing.local_git_repo import LocalGitRepo


@pytest.fixture
def local_environment(test_root_concurrency_group: ConcurrencyGroup) -> Generator[LocalEnvironment, None, None]:
    sandbox_dir = LOCAL_SANDBOX_DIR / str(uuid4().hex)
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    try:
        environment_config = LocalEnvironmentConfig()
        local_env = LocalEnvironment(
            config=environment_config,
            environment_id=LocalEnvironmentID(str(sandbox_dir)),
            project_id=ProjectID(),
            concurrency_group=test_root_concurrency_group,
        )
        local_env.to_host_path(local_env.get_workspace_path()).mkdir(parents=True, exist_ok=True)
        yield local_env
    finally:
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir, ignore_errors=True)


def test_processes_are_closed_on_exit(local_environment: LocalEnvironment):
    proc = local_environment.run_process_in_background(["sleep", "60"], {})
    assert len(local_environment._processes) == 1
    # you MUST do this right now -- give it a few seconds to start
    # otherwise the test is flaky because the process might not have started before we call close below
    time.sleep(5.0)
    assert proc.poll() is None
    local_environment.close()
    assert proc.poll() is not None


def make_test_repo(
    repo_path: Path,
    user_name: str = "Test User",
    user_email: str = "test@example.com",
    initial_file: str = "test.txt",
    initial_content: str = "content",
    initial_commit_msg: str = "Initial commit",
) -> LocalGitRepo:
    """Helper to create a test repository."""
    repo_path.mkdir(parents=True, exist_ok=True)
    repo = LocalGitRepo(repo_path)
    repo.write_file(initial_file, initial_content)
    repo.configure_git(git_user_name=user_name, git_user_email=user_email)
    return repo


def add_commit_to_repo(repo_path: Path, filename: str, content: str, commit_msg: str) -> None:
    """Helper to add a commit to an existing repository."""
    repo = LocalGitRepo(repo_path)
    repo.write_file(filename, content)
    repo.run_git(["add", filename])
    repo.run_git(["commit", "-m", commit_msg])


class TestPushIntoEnvironmentRepo:
    """Tests for push_into_environment_repo method."""

    def test_push_branch_to_environment(
        self, local_environment: LocalEnvironment, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test pushing a branch from user repo to environment repo."""
        with tempfile.TemporaryDirectory() as temp_dir:
            user_repo_path = Path(temp_dir) / "user_repo"
            env_repo_path = local_environment.get_sandbox_path() / "code"

            # Create user repo with a branch
            user_repo_helper = make_test_repo(user_repo_path)
            user_repo_helper.run_git(["checkout", "-b", "test-branch"])
            add_commit_to_repo(user_repo_path, "test_file.txt", "test content", "Test commit")

            # Clone to environment location
            user_repo_helper.clone_repo(env_repo_path)

            # Initialize environment repo wrapper
            user_repo = LocalWritableGitRepo(repo_path=user_repo_path, concurrency_group=test_root_concurrency_group)

            # Push to environment
            local_environment.push_into_environment_repo(
                user_repo=user_repo, src_branch_name="test-branch", dst_branch_name="test-branch"
            )

            # Verify the branch exists in environment repo
            env_repo_helper = LocalGitRepo(env_repo_path)
            branches = env_repo_helper.run_git(["branch", "-a"])
            assert "test-branch" in branches

    def test_push_to_different_branch_name(
        self, local_environment: LocalEnvironment, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test pushing a branch to a different branch name in environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            user_repo_path = Path(temp_dir) / "user_repo"
            env_repo_path = local_environment.get_sandbox_path() / "code"

            # Create user repo with a branch
            user_repo_helper = make_test_repo(user_repo_path)
            user_repo_helper.run_git(["checkout", "-b", "source-branch"])
            add_commit_to_repo(user_repo_path, "test_file.txt", "test content", "Test commit")

            # Clone to environment location
            user_repo_helper.clone_repo(env_repo_path)

            # Initialize environment repo wrapper
            user_repo = LocalWritableGitRepo(repo_path=user_repo_path, concurrency_group=test_root_concurrency_group)

            # Push to different branch name
            local_environment.push_into_environment_repo(
                user_repo=user_repo, src_branch_name="source-branch", dst_branch_name="dest-branch"
            )

            # Verify the destination branch exists in environment repo
            env_repo_helper = LocalGitRepo(env_repo_path)
            branches = env_repo_helper.run_git(["branch", "-a"])
            assert "dest-branch" in branches

    def test_push_updates_existing_branch(
        self, local_environment: LocalEnvironment, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that pushing updates an existing branch in environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            user_repo_path = Path(temp_dir) / "user_repo"
            env_repo_path = local_environment.get_sandbox_path() / "code"

            # Create user repo with a branch
            user_repo_helper = make_test_repo(user_repo_path)
            user_repo_helper.run_git(["checkout", "-b", "update-branch"])
            add_commit_to_repo(user_repo_path, "file1.txt", "content 1", "First commit")

            # Clone to environment location
            user_repo_helper.clone_repo(env_repo_path)

            # Initialize environment repo wrapper
            user_repo = LocalWritableGitRepo(repo_path=user_repo_path, concurrency_group=test_root_concurrency_group)

            # Push initial version
            local_environment.push_into_environment_repo(
                user_repo=user_repo, src_branch_name="update-branch", dst_branch_name="update-branch"
            )

            # Get initial commit hash in environment
            env_repo_helper = LocalGitRepo(env_repo_path)
            env_repo_helper.run_git(["checkout", "update-branch"])
            initial_commit = env_repo_helper.run_git(["rev-parse", "HEAD"])

            # Checkout a different branch in environment to allow push
            env_repo_helper.run_git(["checkout", "-b", "other-branch"])

            # Add another commit to user repo
            add_commit_to_repo(user_repo_path, "file2.txt", "content 2", "Second commit")

            # Push again (this tests non-fast-forward since LocalEnvironment doesn't use force)
            # Note: This should succeed because it's a fast-forward
            local_environment.push_into_environment_repo(
                user_repo=user_repo, src_branch_name="update-branch", dst_branch_name="update-branch"
            )

            # Verify the branch was updated
            updated_commit = env_repo_helper.run_git(["rev-parse", "update-branch"])
            user_commit = user_repo_helper.run_git(["rev-parse", "update-branch"])
            assert updated_commit == user_commit
            assert updated_commit != initial_commit
