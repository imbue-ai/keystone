import os
import socket
import stat
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from pydantic import AnyUrl
from syrupy import SnapshotAssertion

from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.services.git_repo_service.default_implementation import LocalReadOnlyGitRepo
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.default_implementation import get_global_git_config
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.testing.local_git_repo import LocalGitRepo


def wrap_path_in_url(path: Path) -> AnyUrl:
    return AnyUrl(f"file://{path}")


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


def make_test_repo(
    repo_path: Path,
    user_name: str = "Test User",
    user_email: str = "test@example.com",
    initial_file: str = "test.txt",
    initial_content: str = "content",
    initial_commit_msg: str = "Initial commit",
) -> LocalGitRepo:
    repo_path.mkdir(parents=True, exist_ok=True)
    repo = LocalGitRepo(repo_path)
    repo.write_file(initial_file, initial_content)
    repo.configure_git(git_user_name=user_name, git_user_email=user_email)
    return repo


def add_commit_to_repo(repo_path: Path, filename: str, content: str, commit_msg: str) -> None:
    """Helper to add a commit to an existing repository."""
    repo = LocalGitRepo(repo_path)
    repo.write_file(filename, content)
    # NOTE: Using LocalGitRepo helper for file write and commit as these operations
    # are testing helpers that aren't part of the public API we're testing
    repo.run_git(["add", filename])
    repo.run_git(["commit", "-m", commit_msg])


def serialize_directory_state(directory: Path) -> dict:
    """
    Serialize a directory state to a dictionary for snapshotting.

    Returns a nested dictionary structure representing:
    - files: mapping of relative paths to file contents
    - directories: set of directory paths (relative)
    """
    files = {}
    directories = set()

    for item in directory.rglob("*"):
        relative_path = item.relative_to(directory)
        relative_path_str = str(relative_path)

        # Only check if the .git directory exists;
        # checking the contents is too complicated because it's nondeterministic
        if relative_path_str.startswith(".git") and relative_path_str != ".git":
            continue

        if item.is_dir():
            directories.add(relative_path_str)
        elif item.is_file():
            files[relative_path_str] = "<file contents omitted>"

    return {"files": files, "directories": sorted(directories)}


class TestFetchRemoteBranchIntoLocal:
    """Tests for the fetch_remote_branch_into_local method."""

    def test_successful_fetch(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test successful fetch from remote to local branch."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create source repo and add a commit
        make_test_repo(source_path).clone_repo(target_path)
        add_commit_to_repo(source_path, "new_file.txt", "new content", "Add new file")

        # Checkout a different branch to avoid "refusing to fetch into checked out branch" error
        target_repo_for_setup = LocalWritableGitRepo(
            repo_path=target_path, concurrency_group=test_root_concurrency_group
        )
        target_repo_for_setup.create_branch("other-branch")
        target_repo_for_setup.git_checkout_branch("other-branch")

        # Fetch using the new method
        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        target_repo.fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
        )

        # Verify the fetch was successful
        source_repo = LocalReadOnlyGitRepo(repo_path=source_path, concurrency_group=test_root_concurrency_group)
        source_commit = source_repo.get_branch_head_commit_hash("main")
        target_commit = target_repo.get_branch_head_commit_hash("main")
        assert source_commit == target_commit

    def test_fetch_with_force(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test fetch with force flag for non-fast-forward updates."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create divergent histories
        make_test_repo(source_path).clone_repo(target_path)
        add_commit_to_repo(source_path, "source_file.txt", "source content", "Source commit")
        add_commit_to_repo(target_path, "target_file.txt", "target content", "Target commit")

        # Checkout a different branch to avoid "refusing to fetch into checked out branch" error
        target_repo_for_setup = LocalWritableGitRepo(
            repo_path=target_path, concurrency_group=test_root_concurrency_group
        )
        target_repo_for_setup.create_branch("other-branch")
        target_repo_for_setup.git_checkout_branch("other-branch")

        # Fetch with force should succeed
        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        target_repo.fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
            force=True,
        )

        # Verify forced fetch was successful
        source_repo = LocalReadOnlyGitRepo(repo_path=source_path, concurrency_group=test_root_concurrency_group)
        source_commit = source_repo.get_branch_head_commit_hash("main")
        target_commit = target_repo.get_branch_head_commit_hash("main")
        assert source_commit == target_commit

    def test_fetch_with_dry_run(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test fetch with dry_run flag doesn't modify the repository."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create source repo and add a commit
        make_test_repo(source_path).clone_repo(target_path)
        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        initial_commit = target_repo.get_branch_head_commit_hash("main")
        add_commit_to_repo(source_path, "new_file.txt", "new content", "Add new file")

        # Checkout a different branch
        target_repo.create_branch("other-branch")
        target_repo.git_checkout_branch("other-branch")

        # Fetch with dry_run should not change the repo
        target_repo.fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
            dry_run=True,
        )

        # Verify no changes occurred
        current_commit = target_repo.get_branch_head_commit_hash("main")
        assert current_commit == initial_commit

    def test_fetch_with_dangerously_update_head_ok(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test fetch with dangerously_update_head_ok flag on checked out branch."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create source repo and add a commit
        make_test_repo(source_path).clone_repo(target_path)
        add_commit_to_repo(source_path, "new_file.txt", "new content", "Add new file")

        # Ensure target is on main branch
        LocalGitRepo(target_path).run_git(["checkout", "main"])

        # Fetch should succeed with dangerously_update_head_ok
        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        target_repo.fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
            dangerously_update_head_ok=True,
        )

        # Verify the fetch was successful
        source_commit = LocalGitRepo(source_path).run_git(["rev-parse", "main"])
        target_commit = LocalGitRepo(target_path).run_git(["rev-parse", "main"])
        assert source_commit == target_commit

    def test_fetch_raises_on_rejection(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that fetch raises GitRepoError on rejection (exit code 1)."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create divergent histories
        make_test_repo(source_path).clone_repo(target_path)
        add_commit_to_repo(source_path, "source_file.txt", "source content", "Source commit")
        add_commit_to_repo(target_path, "target_file.txt", "target content", "Target commit")

        # Checkout a different branch
        LocalGitRepo(target_path).run_git(["checkout", "-b", "other-branch"])

        # Fetch without force should raise GitRepoError
        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        with pytest.raises(GitRepoError) as exc_info:
            target_repo.fetch_remote_branch_into_local(
                local_branch="main",
                remote=wrap_path_in_url(source_path),
                remote_branch="main",
                force=False,
            )
        assert exc_info.value.exit_code == 1

    def test_fetch_raises_on_invalid_remote(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that fetch raises GitRepoError on invalid remote."""
        target_path = temp_dir / "target"
        make_test_repo(target_path)

        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        with pytest.raises(GitRepoError) as exc_info:
            target_repo.fetch_remote_branch_into_local(
                local_branch="main",
                remote=wrap_path_in_url(temp_dir / "nonexistent"),
                remote_branch="main",
            )
        # Invalid remote typically gives exit code 128
        assert exc_info.value.exit_code == 128

    def test_fetch_raises_on_invalid_branch(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that fetch raises GitRepoError when remote branch doesn't exist."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        make_test_repo(source_path).clone_repo(target_path)

        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        with pytest.raises(GitRepoError) as exc_info:
            target_repo.fetch_remote_branch_into_local(
                local_branch="main",
                remote=wrap_path_in_url(source_path),
                remote_branch="nonexistent-branch",
            )
        assert exc_info.value.exit_code == 128


class TestMaybeFetchRemoteBranchIntoLocal:
    """Tests for the maybe_fetch_remote_branch_into_local method."""

    def test_returns_true_on_success(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that maybe_fetch returns True on successful fetch."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        make_test_repo(source_path).clone_repo(target_path)
        add_commit_to_repo(source_path, "new_file.txt", "new content", "Add new file")

        # Checkout a different branch
        LocalGitRepo(target_path).run_git(["checkout", "-b", "other-branch"])

        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        result = target_repo.maybe_fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
        )

        assert result is True

    def test_returns_false_on_rejection(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that maybe_fetch returns False on rejection (exit code 1)."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create divergent histories
        make_test_repo(source_path).clone_repo(target_path)
        add_commit_to_repo(source_path, "source_file.txt", "source content", "Source commit")
        add_commit_to_repo(target_path, "target_file.txt", "target content", "Target commit")

        # Checkout a different branch
        LocalGitRepo(target_path).run_git(["checkout", "-b", "other-branch"])

        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        result = target_repo.maybe_fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
            force=False,
        )

        assert result is False

    def test_reraises_on_unexpected_error(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that maybe_fetch re-raises on unexpected errors (exit code != 1)."""
        target_path = temp_dir / "target"
        make_test_repo(target_path)

        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        with pytest.raises(GitRepoError) as exc_info:
            target_repo.maybe_fetch_remote_branch_into_local(
                local_branch="main",
                remote=wrap_path_in_url(temp_dir / "nonexistent"),
                remote_branch="main",
            )
        # Should re-raise with exit code 128
        assert exc_info.value.exit_code == 128

    def test_dry_run_behavior(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that maybe_fetch with dry_run doesn't modify repository."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        make_test_repo(source_path).clone_repo(target_path)
        initial_commit = LocalGitRepo(target_path).run_git(["rev-parse", "main"])
        add_commit_to_repo(source_path, "new_file.txt", "new content", "Add new file")

        # Checkout a different branch
        LocalGitRepo(target_path).run_git(["checkout", "-b", "other-branch"])

        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        result = target_repo.maybe_fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
            dry_run=True,
        )

        assert result is True
        current_commit = LocalGitRepo(target_path).run_git(["rev-parse", "main"])
        assert current_commit == initial_commit

    def test_force_flag_enables_non_fast_forward(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that maybe_fetch with force=True succeeds on non-fast-forward."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create divergent histories
        make_test_repo(source_path).clone_repo(target_path)
        add_commit_to_repo(source_path, "source_file.txt", "source content", "Source commit")
        add_commit_to_repo(target_path, "target_file.txt", "target content", "Target commit")

        # Checkout a different branch
        LocalGitRepo(target_path).run_git(["checkout", "-b", "other-branch"])

        target_repo = LocalWritableGitRepo(repo_path=target_path, concurrency_group=test_root_concurrency_group)
        result = target_repo.maybe_fetch_remote_branch_into_local(
            local_branch="main",
            remote=wrap_path_in_url(source_path),
            remote_branch="main",
            force=True,
        )

        assert result is True


class TestHasAnyCommits:
    """Tests for the has_any_commits method."""

    def test_returns_false_for_non_git_directory(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that has_any_commits returns False when .git doesn't exist."""
        repo_path = temp_dir / "not_a_repo"
        repo_path.mkdir()

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)

        assert repo.has_any_commits() is False

    def test_returns_false_for_empty_git_repo(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that has_any_commits returns False for initialized repo with no commits."""
        repo_path = temp_dir / "empty_repo"
        repo_path.mkdir()
        LocalGitRepo(repo_path).run_git(["init"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)

        assert repo.has_any_commits() is False

    def test_returns_true_for_repo_with_commits(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that has_any_commits returns True when commits exist."""
        repo_path = temp_dir / "repo_with_commits"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)

        assert repo.has_any_commits() is True


class TestFromNewRepository:
    """Tests for the from_new_repository class method."""

    def test_initializes_new_repo_successfully(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test successful initialization of a new repository."""
        repo_path = temp_dir / "new_repo"
        repo_path.mkdir()

        repo = LocalWritableGitRepo.from_new_repository(
            repo_path=repo_path,
            concurrency_group=test_root_concurrency_group,
            user_email="test@example.com",
            user_name="Test User",
        )

        assert (repo_path / ".git").exists()
        assert repo.repo_path == repo_path

        # Verify git config was set
        git_repo = LocalGitRepo(repo_path)
        user_email = git_repo.run_git(["config", "user.email"])
        user_name = git_repo.run_git(["config", "user.name"])
        assert user_email == "test@example.com"
        assert user_name == "Test User"

    def test_raises_when_directory_already_git_repo(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that initialization fails if directory is already a git repo."""
        repo_path = temp_dir / "existing_repo"
        make_test_repo(repo_path)

        with pytest.raises(GitRepoError) as exc_info:
            LocalWritableGitRepo.from_new_repository(
                repo_path=repo_path,
                user_email="test@example.com",
                user_name="Test User",
                concurrency_group=test_root_concurrency_group,
            )

        assert "already a git repository" in str(exc_info.value)

    def test_raises_when_directory_does_not_exist(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that initialization fails if directory doesn't exist."""
        repo_path = temp_dir / "nonexistent"

        with pytest.raises(GitRepoError) as exc_info:
            LocalWritableGitRepo.from_new_repository(
                repo_path=repo_path,
                user_email="test@example.com",
                user_name="Test User",
                concurrency_group=test_root_concurrency_group,
            )

        assert "does not exist" in str(exc_info.value)


class TestStageAllFiles:
    """Tests for the stage_all_files method."""

    def test_stages_new_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test staging new files with git add -A."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Add a new file
        (repo_path / "new_file.txt").write_text("new content")

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.stage_all_files()

        # Verify file is staged
        git_repo = LocalGitRepo(repo_path)
        staged_files = git_repo.run_git(["diff", "--cached", "--name-only"])
        assert "new_file.txt" in staged_files

    def test_stages_modified_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test staging modified files."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Modify existing file
        (repo_path / "test.txt").write_text("modified content")

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.stage_all_files()

        # Verify file is staged
        git_repo = LocalGitRepo(repo_path)
        staged_files = git_repo.run_git(["diff", "--cached", "--name-only"])
        assert "test.txt" in staged_files

    def test_stages_deleted_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test staging deleted files."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Delete existing file
        (repo_path / "test.txt").unlink()

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.stage_all_files()

        # Verify deletion is staged
        git_repo = LocalGitRepo(repo_path)
        staged_files = git_repo.run_git(["diff", "--cached", "--name-only"])
        assert "test.txt" in staged_files


class TestCreateCommit:
    """Tests for the create_commit method."""

    def test_creates_commit_with_staged_changes(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test creating a commit with staged changes."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Stage a change
        (repo_path / "new_file.txt").write_text("content")
        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["add", "new_file.txt"])

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        commit_hash = repo.create_commit("Add new file")

        assert commit_hash is not None
        assert len(commit_hash) == 40  # SHA-1 hash length

        # Verify commit message
        commit_msg = git_repo.run_git(["log", "-1", "--format=%s"])
        assert commit_msg == "Add new file"

    def test_creates_empty_commit_when_allowed(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test creating an empty commit with allow_empty=True."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        commit_hash = repo.create_commit("Empty commit", allow_empty=True)

        assert commit_hash is not None
        assert len(commit_hash) == 40

        # Verify commit message
        git_repo = LocalGitRepo(repo_path)
        commit_msg = git_repo.run_git(["log", "-1", "--format=%s"])
        assert commit_msg == "Empty commit"

    def test_returns_commit_hash(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that create_commit returns the new commit hash."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Stage a change
        (repo_path / "new_file.txt").write_text("content")
        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["add", "new_file.txt"])

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        commit_hash = repo.create_commit("Test commit")

        # Verify returned hash matches HEAD
        head_hash = git_repo.run_git(["rev-parse", "HEAD"])
        assert commit_hash == head_hash


class TestGetGlobalGitConfig:
    """Tests for the get_global_git_config module function."""

    def test_returns_config_value_when_set(self, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test reading an existing global git config value."""
        # Note: This test assumes git is configured on the system
        result = get_global_git_config("user.name", test_root_concurrency_group)

        # The result might be None if not configured, but should not raise
        assert result is None or isinstance(result, str)

    def test_returns_none_when_key_not_set(self, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that non-existent config keys return None."""
        result = get_global_git_config(
            "nonexistent.key.that.definitely.does.not.exist",
            test_root_concurrency_group,
        )

        assert result is None


class TestCreateBranch:
    """Tests for the create_branch method."""

    def test_creates_branch_at_head(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test creating a new branch at HEAD."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.create_branch("new-branch")

        # Verify branch exists
        git_repo = LocalGitRepo(repo_path)
        branches = git_repo.run_git(["branch"])
        assert "new-branch" in branches

    def test_creates_branch_at_specific_commit(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test creating a branch at a specific commit."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Get the initial commit hash
        git_repo = LocalGitRepo(repo_path)
        initial_commit = git_repo.run_git(["rev-parse", "HEAD"])

        # Add another commit
        add_commit_to_repo(repo_path, "another.txt", "content", "Another commit")

        # Create branch at initial commit
        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.create_branch("old-branch", start_point=initial_commit)

        # Verify branch points to initial commit
        branch_commit = git_repo.run_git(["rev-parse", "old-branch"])
        assert branch_commit == initial_commit

    def test_creates_branch_from_another_branch(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test creating a branch from another branch name."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Create first branch
        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["checkout", "-b", "feature-branch"])
        add_commit_to_repo(repo_path, "feature.txt", "content", "Feature commit")
        feature_commit = git_repo.run_git(["rev-parse", "HEAD"])

        # Create second branch from first
        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.create_branch("feature-copy", start_point="feature-branch")

        # Verify they point to same commit
        copy_commit = git_repo.run_git(["rev-parse", "feature-copy"])
        assert copy_commit == feature_commit


class TestGetCurrentCommitHash:
    """Tests for the get_current_commit_hash method."""

    def test_returns_current_commit_hash(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test getting current commit hash."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        commit_hash = repo.get_current_commit_hash()

        # Verify it's a valid SHA-1 hash
        assert len(commit_hash) == 40
        assert all(c in "0123456789abcdef" for c in commit_hash)

        # Verify it matches git rev-parse HEAD
        git_repo = LocalGitRepo(repo_path)
        expected_hash = git_repo.run_git(["rev-parse", "HEAD"])
        assert commit_hash == expected_hash

    def test_raises_when_no_commits(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that get_current_commit_hash raises error when repo has no commits."""

        repo_path = temp_dir / "empty_repo"
        repo_path.mkdir()
        LocalGitRepo(repo_path).run_git(["init"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        with pytest.raises(GitRepoError):
            repo.get_current_commit_hash()


class TestGetBranchHeadCommitHash:
    """Tests for the get_branch_head_commit_hash method."""

    def test_returns_branch_head_hash(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test getting head commit hash for a specific branch."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Create a new branch and add a commit
        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["checkout", "-b", "feature"])
        add_commit_to_repo(repo_path, "feature.txt", "content", "Feature commit")
        feature_hash = git_repo.run_git(["rev-parse", "HEAD"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        result = repo.get_branch_head_commit_hash("feature")

        assert result == feature_hash

    def test_works_for_main_branch(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test getting head hash for main branch."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        main_hash = git_repo.run_git(["rev-parse", "main"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        result = repo.get_branch_head_commit_hash("main")

        assert result == main_hash


class TestGetCurrentGitBranch:
    """Tests for the get_current_git_branch method."""

    def test_returns_current_branch(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test getting current branch name."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        branch = repo.get_current_git_branch()

        assert branch == "main"

    def test_returns_different_branch(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test getting current branch after checkout."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["checkout", "-b", "feature"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        branch = repo.get_current_git_branch()

        assert branch == "feature"


class TestGetNumUncommittedChanges:
    """Tests for the get_num_uncommitted_changes method."""

    def test_returns_zero_for_clean_repo(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that clean repo has zero uncommitted changes."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        num_changes = repo.get_num_uncommitted_changes()

        assert num_changes == 0

    def test_counts_modified_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test counting modified files."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Modify existing file
        (repo_path / "test.txt").write_text("modified")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        num_changes = repo.get_num_uncommitted_changes()

        assert num_changes == 1

    def test_counts_new_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test counting new untracked files."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Add new file
        (repo_path / "new.txt").write_text("new content")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        num_changes = repo.get_num_uncommitted_changes()

        assert num_changes == 1

    def test_counts_multiple_changes(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test counting multiple types of changes."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Modify existing file
        (repo_path / "test.txt").write_text("modified")
        # Add new file
        (repo_path / "new.txt").write_text("new")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        num_changes = repo.get_num_uncommitted_changes()

        assert num_changes == 2


class TestListMatchingFiles:
    """Tests for the list_matching_files method."""

    def test_lists_all_files_with_empty_pattern(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test listing all files with empty pattern."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)
        add_commit_to_repo(repo_path, "file1.txt", "content", "Add file1")
        add_commit_to_repo(repo_path, "file2.py", "content", "Add file2")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        files = repo.list_matching_files("")

        assert "test.txt" in files
        assert "file1.txt" in files
        assert "file2.py" in files

    def test_filters_by_substring_pattern(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test filtering files by substring pattern (case-insensitive)."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)
        add_commit_to_repo(repo_path, "file1.txt", "content", "Add file1")
        add_commit_to_repo(repo_path, "file2.py", "content", "Add file2")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        # Pattern is a substring match, not a glob
        files = repo.list_matching_files(".txt")

        assert "test.txt" in files
        assert "file1.txt" in files
        assert "file2.py" not in files


class TestListMatchingFolders:
    """Tests for the list_matching_folders method."""

    def test_lists_folders(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test listing folders in repo."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Create directories and files
        (repo_path / "dir1").mkdir()
        (repo_path / "dir1" / "file.txt").write_text("content")
        (repo_path / "dir2").mkdir()
        (repo_path / "dir2" / "file.txt").write_text("content")

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["add", "."])
        git_repo.run_git(["commit", "-m", "Add dirs"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        folders = repo.list_matching_folders("")

        # Folder names include trailing slashes
        assert "dir1/" in folders
        assert "dir2/" in folders


class TestListUntrackedFiles:
    """Tests for the list_untracked_files method."""

    def test_returns_empty_for_clean_repo(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that clean repo has no untracked files."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        untracked = repo.list_untracked_files()

        assert len(untracked) == 0

    def test_lists_untracked_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test listing untracked files."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Add untracked files
        (repo_path / "untracked1.txt").write_text("content")
        (repo_path / "untracked2.py").write_text("content")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        untracked = repo.list_untracked_files()

        assert "untracked1.txt" in untracked
        assert "untracked2.py" in untracked
        assert "test.txt" not in untracked  # Tracked file


class TestListStaged:
    """Tests for the list_staged method."""

    def test_returns_empty_for_no_staged_files(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that repo with no staged changes returns empty list."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        staged = repo.list_staged()

        assert len(staged) == 0

    def test_lists_staged_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test listing staged files."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Add and stage files
        (repo_path / "staged1.txt").write_text("content")
        (repo_path / "staged2.py").write_text("content")

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["add", "staged1.txt", "staged2.py"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        staged = repo.list_staged()

        assert "staged1.txt" in staged
        assert "staged2.py" in staged


class TestListUnstaged:
    """Tests for the list_unstaged method."""

    def test_returns_empty_for_clean_repo(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that clean repo has no unstaged changes."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        unstaged = repo.list_unstaged()

        assert len(unstaged) == 0

    def test_lists_unstaged_modified_files(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test listing unstaged modified files."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Modify tracked file
        (repo_path / "test.txt").write_text("modified")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        unstaged = repo.list_unstaged()

        assert "test.txt" in unstaged


class TestGetAllBranches:
    """Tests for the get_all_branches method."""

    def test_returns_all_branches_in_alphabetical_order(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test getting all branches in alphabetical order."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        # Create branches
        git_repo.run_git(["checkout", "-b", "zebra-branch"])
        add_commit_to_repo(repo_path, "file1.txt", "content", "Commit on zebra-branch")

        git_repo.run_git(["checkout", "-b", "alpha-branch"])
        add_commit_to_repo(repo_path, "file2.txt", "content", "Commit on alpha-branch")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        branches = repo.get_all_branches()

        # Should return all branches including main
        assert "alpha-branch" in branches
        assert "zebra-branch" in branches
        assert "main" in branches
        # Branches should be in alphabetical order
        assert branches.index("alpha-branch") < branches.index("main")
        assert branches.index("main") < branches.index("zebra-branch")


class TestIsMergeInProgress:
    """Tests for the is_merge_in_progress property."""

    def test_returns_false_for_clean_repo(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that clean repo has no merge in progress."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_merge_in_progress is False

    def test_returns_true_during_merge_conflict(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that is_merge_in_progress returns True during a merge conflict."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        # Create conflicting changes
        git_repo.run_git(["checkout", "-b", "feature"])
        (repo_path / "test.txt").write_text("feature content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Feature change"])

        git_repo.run_git(["checkout", "main"])
        (repo_path / "test.txt").write_text("main content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Main change"])

        # Attempt merge which will create conflict
        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.merge_from_ref("feature")

        # Should detect merge in progress
        assert repo.is_merge_in_progress is True


class TestIsRebaseInProgress:
    """Tests for the is_rebase_in_progress property."""

    def test_returns_false_for_clean_repo(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that clean repo has no rebase in progress."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_rebase_in_progress is False

    def test_returns_true_during_rebase_conflict(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that is_rebase_in_progress returns True during a rebase conflict."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        # Create a feature branch with a commit
        git_repo.run_git(["checkout", "-b", "feature"])
        (repo_path / "feature.txt").write_text("feature content")
        git_repo.run_git(["add", "feature.txt"])
        git_repo.run_git(["commit", "-m", "Feature commit"])

        # Create conflicting change on main
        git_repo.run_git(["checkout", "main"])
        (repo_path / "test.txt").write_text("main content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Main change"])

        # Go back to feature and modify the same file
        git_repo.run_git(["checkout", "feature"])
        (repo_path / "test.txt").write_text("feature test content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Feature test change"])

        # Attempt rebase which will create conflict
        try:
            git_repo.run_git(["rebase", "main"])
        except Exception:
            pass  # Rebase will fail due to conflict, which is expected

        # Should detect rebase in progress
        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_rebase_in_progress is True


class TestIsCherryPickInProgress:
    """Tests for the is_cherry_pick_in_progress property."""

    def test_returns_false_for_clean_repo(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that clean repo has no cherry-pick in progress."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_cherry_pick_in_progress is False

    def test_returns_true_during_cherry_pick_conflict(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that is_cherry_pick_in_progress returns True during a cherry-pick conflict."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        # Create a feature branch with a commit
        git_repo.run_git(["checkout", "-b", "feature"])
        (repo_path / "test.txt").write_text("feature content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Feature change"])
        feature_commit = git_repo.run_git(["rev-parse", "HEAD"])

        # Create conflicting change on main
        git_repo.run_git(["checkout", "main"])
        (repo_path / "test.txt").write_text("main content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Main change"])

        # Attempt to cherry-pick the feature commit, which will create conflict
        try:
            git_repo.run_git(["cherry-pick", feature_commit])
        except Exception:
            pass  # Cherry-pick will fail due to conflict, which is expected

        # Should detect cherry-pick in progress
        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_cherry_pick_in_progress is True


class TestReadFile:
    """Tests for the read_file method."""

    def test_reads_file_content(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test reading file content from repo."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path, initial_content="initial content")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        content = repo.read_file(Path("test.txt"))

        assert content == "initial content"

    def test_returns_none_for_nonexistent_file(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that nonexistent file returns None."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        content = repo.read_file(Path("nonexistent.txt"))

        assert content is None

    def test_reads_file_in_subdirectory(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test reading file in subdirectory."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Create subdirectory and file
        (repo_path / "subdir").mkdir()
        (repo_path / "subdir" / "file.txt").write_text("subdir content")

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["add", "."])
        git_repo.run_git(["commit", "-m", "Add subdir"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        content = repo.read_file(Path("subdir/file.txt"))

        assert content == "subdir content"


class TestDoesRelativeFileExist:
    """Tests for the does_relative_file_exist method."""

    def test_returns_true_for_existing_file(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that existing file returns True."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        exists = repo.does_relative_file_exist(Path("test.txt"))

        assert exists is True

    def test_returns_false_for_nonexistent_file(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that nonexistent file returns False."""

        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        exists = repo.does_relative_file_exist(Path("nonexistent.txt"))

        assert exists is False


class TestGitCheckoutBranch:
    """Tests for the git_checkout_branch method."""

    def test_checkouts_existing_branch(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test checking out an existing branch."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["checkout", "-b", "feature"])
        git_repo.run_git(["checkout", "main"])

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.git_checkout_branch("feature")

        # Verify we're on feature branch
        current_branch = git_repo.run_git(["branch", "--show-current"])
        assert current_branch == "feature"

    def test_raises_error_when_branch_does_not_exist(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that checking out a nonexistent branch raises GitRepoError."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)

        with pytest.raises(GitRepoError) as exc_info:
            repo.git_checkout_branch("nonexistent-branch")

        # Git returns exit code 1 for "pathspec did not match any file(s) known to git"
        assert exc_info.value.exit_code == 1
        assert exc_info.value.stderr is not None


class TestResetWorkingDirectory:
    """Tests for the reset_working_directory method."""

    def test_resets_modified_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test resetting modified files to HEAD."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path, initial_content="original")

        # Modify file
        (repo_path / "test.txt").write_text("modified")

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.reset_working_directory()

        # Verify file was reset
        content = (repo_path / "test.txt").read_text()
        assert content == "original"

    def test_removes_untracked_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test that reset removes untracked files."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Add untracked file
        untracked_file = repo_path / "untracked.txt"
        untracked_file.write_text("untracked content")

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.reset_working_directory()

        # Verify untracked file was removed
        assert not untracked_file.exists()


class TestPushRefToRemote:
    """Tests for the push_ref_to_remote method."""

    def test_pushes_branch_to_remote(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test pushing a branch to remote repository."""
        source_path = temp_dir / "source"
        target_path = temp_dir / "target"

        # Create source and bare target repo
        make_test_repo(source_path)
        target_path.mkdir()
        LocalGitRepo(target_path).run_git(["init", "--bare", str(target_path)])

        repo = LocalWritableGitRepo(repo_path=source_path, concurrency_group=test_root_concurrency_group)
        repo.push_ref_to_remote(
            remote=str(target_path),
            local_ref="main",
            remote_ref="main",
            is_forced=False,
        )

        # Verify push succeeded by checking target repo
        result = LocalGitRepo(target_path).run_git(["branch"])
        assert "main" in result


class TestMergeFromRef:
    """Tests for the merge_from_ref method."""

    def test_successful_fast_forward_merge(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test successful fast-forward merge."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        # Create feature branch and add commit
        git_repo.run_git(["checkout", "-b", "feature"])
        add_commit_to_repo(repo_path, "feature.txt", "content", "Feature commit")
        feature_hash = git_repo.run_git(["rev-parse", "HEAD"])

        # Go back to main and merge
        git_repo.run_git(["checkout", "main"])

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        result = repo.merge_from_ref("feature")

        assert result.is_merged is True
        assert result.was_up_to_date is False
        # Verify main now points to feature commit
        main_hash = git_repo.run_git(["rev-parse", "HEAD"])
        assert main_hash == feature_hash

    def test_merge_with_conflicts_returns_result(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that merge with conflicts returns GitRepoMergeResult without raising."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        # Create conflicting changes
        git_repo.run_git(["checkout", "-b", "feature"])
        (repo_path / "test.txt").write_text("feature content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Feature change"])

        git_repo.run_git(["checkout", "main"])
        (repo_path / "test.txt").write_text("main content")
        git_repo.run_git(["add", "test.txt"])
        git_repo.run_git(["commit", "-m", "Main change"])

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        result = repo.merge_from_ref("feature")

        # Should return GitRepoMergeResult with is_merged=False instead of raising
        assert result.is_merged is False
        # Verify merge is in progress (indicates conflict)
        assert repo.is_merge_in_progress


class TestDeleteTag:
    """Tests for the delete_tag method."""

    def test_deletes_existing_tag(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test deleting an existing tag."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["tag", "v1.0.0"])

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        result = repo.delete_tag("v1.0.0")

        assert result is True
        # Verify tag is gone
        tags = git_repo.run_git(["tag"])
        assert "v1.0.0" not in tags

    def test_returns_false_for_nonexistent_tag(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that deleting nonexistent tag returns False."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        result = repo.delete_tag("nonexistent-tag")

        assert result is False


class TestIsBranchRef:
    """Tests for the is_branch_ref method."""

    def test_returns_true_for_existing_branch(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that existing branch returns True."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["checkout", "-b", "feature"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_branch_ref("feature") is True
        assert repo.is_branch_ref("main") is True

    def test_returns_false_for_nonexistent_branch(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that nonexistent branch returns False."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_branch_ref("nonexistent") is False

    def test_returns_false_for_commit_hash(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that commit hash is not considered a branch ref."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        commit_hash = git_repo.run_git(["rev-parse", "HEAD"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        assert repo.is_branch_ref(commit_hash) is False


class TestGetCurrentStatus:
    """Tests for the get_current_status method."""

    def test_returns_clean_status_for_clean_repo(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that clean repo returns clean status."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        status = repo.get_current_status()

        assert status.files.unstaged == 0
        assert status.files.staged == 0
        assert status.files.untracked == 0
        assert status.files.deleted == 0
        assert status.is_merging is False
        assert status.is_rebasing is False
        assert status.is_cherry_picking is False

    def test_counts_unstaged_changes(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test counting unstaged changes."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Modify file
        (repo_path / "test.txt").write_text("modified")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        status = repo.get_current_status()

        assert status.files.unstaged == 1

    def test_counts_staged_changes(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test counting staged changes."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Add and stage new file
        (repo_path / "new.txt").write_text("content")
        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["add", "new.txt"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        status = repo.get_current_status()

        assert status.files.staged == 1

    def test_counts_untracked_files(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test counting untracked files."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        # Add untracked file
        (repo_path / "untracked.txt").write_text("content")

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        status = repo.get_current_status()

        assert status.files.untracked == 1


class TestGetAbsoluteReferenceToCurrentLocation:
    """Tests for the get_absolute_reference_to_current_location method."""

    def test_returns_absolute_reference(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test getting absolute reference to current location."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        commit_hash = git_repo.run_git(["rev-parse", "HEAD"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        ref = repo.get_current_absolute_git_position()

        assert ref.branch == "main"
        assert ref.commit_hash == commit_hash
        assert str(repo_path) in str(ref.repo_url)

    def test_works_on_feature_branch(self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> None:
        """Test getting reference on feature branch."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path)

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["checkout", "-b", "feature"])
        add_commit_to_repo(repo_path, "feature.txt", "content", "Feature commit")
        commit_hash = git_repo.run_git(["rev-parse", "HEAD"])

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        ref = repo.get_current_absolute_git_position()

        assert ref.branch == "feature"
        assert ref.commit_hash == commit_hash


class TestExportCurrentRepoState:
    """Tests for the export_current_repo_state method."""

    def test_exports_changed_and_untracked_files(
        self, snapshot: SnapshotAssertion, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test exporting changed and untracked files (not entire repo)."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path, initial_content="original")

        # Create some changes: unstaged, staged, and untracked
        (repo_path / "test.txt").write_text("modified")  # unstaged change
        (repo_path / "new_staged.txt").write_text("staged content")
        (repo_path / "untracked.txt").write_text("untracked content")

        git_repo = LocalGitRepo(repo_path)
        git_repo.run_git(["add", "new_staged.txt"])

        target_path = temp_dir / "export"
        target_path.mkdir()

        repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
        repo.export_current_repo_state(target_path)

        # Use snapshot testing to verify the exported directory state
        exported_state = serialize_directory_state(target_path)
        assert exported_state == snapshot

    def test_rsync_handles_socket_files_with_no_d_flag(
        self, temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
    ) -> None:
        """Test that export_current_repo_state with --no-D flag handles socket files correctly."""
        repo_path = temp_dir / "repo"
        make_test_repo(repo_path, initial_content="original")

        # Create a Unix socket file simulating .git/fsmonitor--daemon.ipc
        socket_path = repo_path / ".git" / "test-socket.ipc"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        try:
            sock.bind(str(socket_path))

            # Verify socket file creation
            assert socket_path.exists(), "Socket file was not created"
            socket_stat = os.stat(socket_path)
            assert stat.S_ISSOCK(socket_stat.st_mode), "Created file is not a socket"

            # Create test files
            (repo_path / "test.txt").write_text("modified")
            (repo_path / "new_file.txt").write_text("new content")

            target_path = temp_dir / "export"
            target_path.mkdir()

            repo = LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)

            # Execute export - should succeed despite socket file due to --no-D flag
            repo.export_current_repo_state(target_path)

            # Verify .git directory was copied (excluding objects)
            assert (target_path / ".git").exists(), "The .git directory was not copied"
            assert (target_path / ".git" / "config").exists() or (target_path / ".git" / "HEAD").exists(), (
                "Basic .git files were not copied"
            )

            # Verify socket file was NOT copied (--no-D skips special files)
            assert not (target_path / ".git" / "test-socket.ipc").exists(), (
                "Socket file should not have been copied with --no-D flag"
            )

            # Verify patch files were created
            assert (target_path / "tracked_changes.patch").exists(), "tracked_changes.patch was not created"
            assert (target_path / "untracked_files.tar").exists(), "untracked_files.tar was not created"

            # Verify patch content
            patch_content = (target_path / "tracked_changes.patch").read_text()
            assert "modified" in patch_content or len(patch_content) == 0, "Patch does not contain expected changes"

        finally:
            # Ensure proper cleanup
            try:
                sock.close()
            except OSError:
                pass
            try:
                if socket_path.exists():
                    os.unlink(str(socket_path))
            except (OSError, FileNotFoundError):
                pass  # Ignore errors during cleanup
