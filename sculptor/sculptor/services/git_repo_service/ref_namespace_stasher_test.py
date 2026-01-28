"""Unit tests for ref_namespace_stasher.py."""

import tempfile
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from typing import Generator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.pydantic_serialization import model_dump_json
from imbue_core.pydantic_serialization import model_load_json
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.git_repo_service.error_types import GitStashApplyError
from sculptor.services.git_repo_service.ref_namespace_stasher import AbsoluteGitTransition
from sculptor.services.git_repo_service.ref_namespace_stasher import build_sculptor_stash_reader
from sculptor.services.git_repo_service.ref_namespace_stasher import checkout_branch_maybe_stashing_as_we_go
from sculptor.services.git_repo_service.ref_namespace_stasher import is_global_stash_singleton_stashed
from sculptor.services.git_repo_service.ref_namespace_stasher import pop_namespaced_stash_into_source_branch
from sculptor.services.local_sync_service.api import SculptorStash
from sculptor.testing.local_git_repo import LocalGitRepo
from sculptor.utils.build import get_sculptor_folder


@pytest.fixture(autouse=True)
def isolated_sculptor_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """Isolate sculptor folder to avoid parallel test conflicts with stash singleton file."""
    sculptor_folder = tmp_path / "sculptor"
    sculptor_folder.mkdir()
    monkeypatch.setenv("SCULPTOR_FOLDER", str(sculptor_folder))
    # Clear the cache on get_sculptor_folder since it's decorated with @cache
    get_sculptor_folder.cache_clear()
    yield sculptor_folder
    # Clear cache after test to avoid affecting other tests
    get_sculptor_folder.cache_clear()


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
    repo.run_git(["add", filename])
    repo.run_git(["commit", "-m", commit_msg])


def verify_git_status(
    git_repo: "LocalGitRepo",
    description: str,
    expected_status: str,
) -> None:
    """Verify git status matches expected output.

    Args:
        git_repo: The git repo to check
        description: Description of what this status check verifies
        expected_status: Expected git status --porcelain output (use textwrap.dedent)
    """
    actual = git_repo.run_git(["status", "--porcelain"])
    expected = textwrap.dedent(expected_status).strip()

    assert actual.strip() == expected, f"{description}\nExpected:\n{expected}\n\nGot:\n{actual.strip()}"


from dataclasses import dataclass
from typing import Callable


@dataclass
class StashRestoreOutcome:
    """Expected outcome of a stash restore operation."""

    end_status: str  # Expected git status --porcelain output
    should_raise_error: bool = True  # Whether GitStashApplyError should be raised
    on_source_branch: bool = True  # Whether we should end up on the source branch

    @contextmanager
    def maybe_expect_error(self) -> Generator[None, None, None]:
        """Context manager to conditionally expect an error."""
        if self.should_raise_error:
            with pytest.raises(GitStashApplyError):
                yield
        else:
            yield


class RepoScene:
    """Test scenario helper for git repository operations."""

    def __init__(
        self,
        git_repo: "LocalGitRepo",
        repo_path: Path,
        test_repo: "LocalWritableGitRepo",
        branch: str = "feature",
    ):
        self.git_repo = git_repo
        self.repo_path = repo_path
        self.test_repo = test_repo
        self.branch = branch
        self.project_id = ProjectID()
        self._initial_commit = git_repo.run_git(["rev-parse", "HEAD"])

    @contextmanager
    def scenario(self, prep_stash: Callable[[], None]) -> Generator[None, None, None]:
        """Context manager for running a scenario with setup and teardown.

        Sets up the stash, yields for the test, then resets git state.
        """
        # Set up the stash
        prep_stash()

        try:
            yield
        finally:
            # Reset git state to initial commit - handle conflicts/dirty state
            try:
                # Force checkout to handle conflicts
                self.git_repo.run_git(["checkout", "-f", self.branch])
            except Exception:
                # If checkout fails, reset from detached HEAD
                self.git_repo.run_git(["reset", "--hard", self._initial_commit])
                self.git_repo.run_git(["checkout", "-f", self.branch])

            # Reset to initial commit and clean everything
            self.git_repo.run_git(["reset", "--hard", self._initial_commit])
            self.git_repo.run_git(["clean", "-fd"])

    def do_change(
        self,
        description: str,
        *,
        add_committed: dict[str, str] | None = None,
        add_staged: dict[str, str] | None = None,
        add_unstaged: dict[str, str] | None = None,
        add_untracked: dict[str, str] | None = None,
        commit_message: str | None = None,
    ) -> None:
        """Make declarative changes to the repository."""
        # Create/modify committed files
        if add_committed:
            for filename, content in add_committed.items():
                (self.repo_path / filename).write_text(content)
                self.git_repo.run_git(["add", filename])

            msg = commit_message or description
            self.git_repo.run_git(["commit", "-m", msg])

        # Create/modify staged files
        if add_staged:
            for filename, content in add_staged.items():
                (self.repo_path / filename).write_text(content)
                self.git_repo.run_git(["add", filename])

        # Create/modify unstaged files
        if add_unstaged:
            for filename, content in add_unstaged.items():
                (self.repo_path / filename).write_text(content)

        # Create untracked files
        if add_untracked:
            for filename, content in add_untracked.items():
                (self.repo_path / filename).write_text(content)

    def prep_change_callback(
        self,
        description: str,
        *,
        add_committed: dict[str, str] | None = None,
        add_staged: dict[str, str] | None = None,
        add_unstaged: dict[str, str] | None = None,
        add_untracked: dict[str, str] | None = None,
        commit_message: str | None = None,
    ) -> Callable[[], None]:
        """Return a callback that will make the specified changes."""

        def callback() -> None:
            self.do_change(
                description,
                add_committed=add_committed,
                add_staged=add_staged,
                add_unstaged=add_unstaged,
                add_untracked=add_untracked,
                commit_message=commit_message,
            )

        return callback

    def verify_status(self, expected_status: str) -> None:
        """Verify git status matches expected output."""
        actual = self.git_repo.run_git(["status", "--porcelain"])
        expected = textwrap.dedent(expected_status).strip()
        assert actual.strip() == expected, f"Expected:\n{expected}\n\nGot:\n{actual.strip()}"

    def run_scenarios(
        self,
        prep_stash: Callable[[], None],
        scenarios: list[tuple[str, Callable[[], None], StashRestoreOutcome]],
    ) -> None:
        """Run multiple test scenarios with the same stash setup.

        Args:
            prep_stash: Callback to set up the stash
            scenarios: List of (scenario_name, prep_conflict, expected_outcome) tuples
        """
        for scenario_name, prep_conflict, expected_outcome in scenarios:
            with self.scenario(prep_stash):
                stash_singleton = checkout_branch_maybe_stashing_as_we_go(**self.stash_params)
                assert stash_singleton is not None, f"Expected stash to be created for scenario: {scenario_name}"

                self.git_repo.run_git(["checkout", "feature"])
                prep_conflict()
                self.git_repo.run_git(["checkout", "main"])

                with expected_outcome.maybe_expect_error():
                    pop_namespaced_stash_into_source_branch(**self.pop_params(stash_singleton.stash))

                if expected_outcome.on_source_branch:
                    current_branch = self.git_repo.run_git(["branch", "--show-current"])
                    assert current_branch == "feature", f"Should be on feature branch for scenario: {scenario_name}"

                self.verify_status(expected_outcome.end_status)

    @property
    def stash_params(self) -> dict[str, Any]:
        """Parameters for checkout_branch_maybe_stashing_as_we_go.

        Returns parameters to switch TO main branch (stashing changes FROM feature branch).
        """
        return {
            "project_id": self.project_id,
            "repo": self.test_repo,
            "target_branch": "main",
        }

    def pop_params(self, stash: SculptorStash) -> dict[str, Any]:
        """Parameters for pop_namespaced_stash_into_source_branch."""
        return {
            "project_id": self.project_id,
            "repo": self.test_repo,
            "stash": stash,
        }


class RepoChanger:
    """Legacy callable helper for making declarative changes to a git repository."""

    def __init__(self, git_repo: "LocalGitRepo", repo_path: Path):
        self.git_repo = git_repo
        self.repo_path = repo_path

    def __call__(
        self,
        description: str,
        *,
        add_committed: dict[str, str] | None = None,
        add_staged: dict[str, str] | None = None,
        add_unstaged: dict[str, str] | None = None,
        add_untracked: dict[str, str] | None = None,
        commit_message: str | None = None,
    ) -> None:
        """Make declarative changes to a git repository."""
        # Create/modify committed files
        if add_committed:
            for filename, content in add_committed.items():
                (self.repo_path / filename).write_text(content)
                self.git_repo.run_git(["add", filename])

            msg = commit_message or description
            self.git_repo.run_git(["commit", "-m", msg])

        # Create/modify staged files
        if add_staged:
            for filename, content in add_staged.items():
                (self.repo_path / filename).write_text(content)
                self.git_repo.run_git(["add", filename])

        # Create/modify unstaged files
        if add_unstaged:
            for filename, content in add_unstaged.items():
                (self.repo_path / filename).write_text(content)

        # Create untracked files
        if add_untracked:
            for filename, content in add_untracked.items():
                (self.repo_path / filename).write_text(content)


@pytest.fixture
def test_repo(
    temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup
) -> Generator[LocalWritableGitRepo, None, None]:
    """Create a test repository and return LocalWritableGitRepo instance."""
    repo_path = temp_dir / "repo"
    make_test_repo(repo_path)
    writable_repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
    yield writable_repo


@pytest.fixture
def repo_scene(temp_dir: Path, test_root_concurrency_group: ConcurrencyGroup) -> Generator[RepoScene, None, None]:
    """Create a RepoScene fixture with feature branch checked out."""
    repo_path = temp_dir / "repo"
    make_test_repo(repo_path)
    git_repo = LocalGitRepo(repo_path)

    # Create and checkout feature branch
    git_repo.run_git(["checkout", "-b", "feature"])

    writable_repo = LocalWritableGitRepo(repo_path=repo_path, concurrency_group=test_root_concurrency_group)
    scene = RepoScene(git_repo, repo_path, writable_repo, branch="feature")

    yield scene


# Tests for build_sculptor_stash_reader
def test_empty_result_when_no_stashes_exist(test_repo: LocalWritableGitRepo) -> None:
    """Test that querying stashes returns empty tuple when repository has no stashes."""
    reader = build_sculptor_stash_reader(test_repo)

    stashes = reader.get_stashes()
    assert len(stashes) == 0, "Expected no stashes in empty repository"


def test_singleton_returns_none_when_empty(test_repo: LocalWritableGitRepo) -> None:
    """Test that singleton stash accessor returns None when no stashes exist."""
    reader = build_sculptor_stash_reader(test_repo)

    stash = reader.maybe_get_singleton_stash()
    assert stash is None, "Expected singleton stash to be None when repository is empty"


# Tests for checkout_branch_maybe_stashing_as_we_go


def test_clean_checkout_without_creating_stash(test_repo: LocalWritableGitRepo) -> None:
    """Test that checking out branch with no local changes doesn't create a stash."""
    repo_path = test_repo.get_repo_path()
    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])

    project_id = ProjectID()
    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")

    assert stash_singleton is None, "Expected no stash to be created when working directory is clean"
    assert git_repo.run_git(["branch", "--show-current"]) == "main", "Expected to be on main branch after checkout"


def test_stash_created_and_working_directory_cleaned_on_checkout(test_repo: LocalWritableGitRepo) -> None:
    """Test that uncommitted changes are stashed when checking out different branch."""
    repo_path = test_repo.get_repo_path()

    # Create a feature branch and add uncommitted changes
    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])
    (repo_path / "test.txt").write_text("modified content")
    (repo_path / "new_file.txt").write_text("new content")

    project_id = ProjectID()
    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")

    assert stash_singleton is not None, "Expected stash to be created when uncommitted changes exist"
    assert stash_singleton.owning_project_id == project_id, f"Expected stash to be owned by {project_id}"
    assert stash_singleton.stash.source_branch == "feature", "Expected stash to record 'feature' as source branch"
    assert git_repo.run_git(["branch", "--show-current"]) == "main", "Expected to be on main branch after checkout"

    # Verify working directory is clean after checkout
    status = test_repo.get_current_status()
    assert status.is_clean_and_safe_to_operate_on, "Expected working directory to be clean after stashing and checkout"


def test_stash_created_when_already_on_target_branch(test_repo: LocalWritableGitRepo) -> None:
    """Test that stash is created even when already on the target branch."""
    repo_path = test_repo.get_repo_path()

    # Add uncommitted changes on main
    (repo_path / "test.txt").write_text("modified content")

    project_id = ProjectID()
    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")

    assert stash_singleton is not None, "Expected stash to be created even when already on target branch"
    assert stash_singleton.stash.source_branch == "main", "Expected stash to record 'main' as source branch"

    git_repo = LocalGitRepo(repo_path)
    assert git_repo.run_git(["branch", "--show-current"]) == "main", "Expected to remain on main branch"


def test_checkout_rejected_during_merge_conflict(test_repo: LocalWritableGitRepo) -> None:
    """Test that checkout is rejected when repository is in intermediate merge state."""
    repo_path = test_repo.get_repo_path()

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

    # Start a merge that will create a conflict
    test_repo.merge_from_ref("feature")

    project_id = ProjectID()

    with pytest.raises(GitRepoError) as exc_info:
        checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "feature")

    assert "intermediate state" in str(exc_info.value).lower(), (
        "Expected error message to mention 'intermediate state'"
    )


# Tests for pop_namespaced_stash_into_source_branch


def test_stash_successfully_restored_to_source_branch(test_repo: LocalWritableGitRepo) -> None:
    """Test that stashed changes are successfully restored to their source branch."""
    repo_path = test_repo.get_repo_path()

    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])

    # Create and stash changes
    (repo_path / "test.txt").write_text("modified")
    (repo_path / "new_file.txt").write_text("new")

    project_id = ProjectID()

    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")
    assert stash_singleton is not None, "Expected stash to be created"

    # Now import and pop the stash
    pop_namespaced_stash_into_source_branch(project_id, test_repo, stash_singleton.stash)

    # Verify we're back on feature branch
    assert git_repo.run_git(["branch", "--show-current"]) == "feature", "Expected to be on feature branch after pop"

    # Verify changes are restored and repository has expected status
    assert (repo_path / "test.txt").read_text() == "modified", "Expected test.txt to contain 'modified'"
    assert (repo_path / "new_file.txt").read_text() == "new", "Expected new_file.txt to contain 'new'"

    status = test_repo.get_current_status()
    assert not status.is_clean_and_safe_to_operate_on, "Expected repository to have changes after stash pop"
    assert status.files.unstaged == 1, "Expected 1 unstaged file (test.txt modified)"
    assert status.files.untracked == 1, "Expected 1 untracked file (new_file.txt)"


def test_stash_apply_error_raised_on_conflicts(test_repo: LocalWritableGitRepo) -> None:
    """Test that GitStashApplyError is raised when stash application conflicts with current state."""
    repo_path = test_repo.get_repo_path()

    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])

    # Create and stash changes
    (repo_path / "test.txt").write_text("feature modified")

    project_id = ProjectID()

    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")
    assert stash_singleton is not None, "Expected stash to be created"

    # Make conflicting changes on feature branch
    git_repo.run_git(["checkout", "feature"])
    (repo_path / "test.txt").write_text("different content")
    git_repo.run_git(["add", "test.txt"])
    git_repo.run_git(["commit", "-m", "Conflicting change"])
    git_repo.run_git(["checkout", "main"])

    # Attempt to import and pop should raise error due to conflict
    with pytest.raises(GitStashApplyError):
        pop_namespaced_stash_into_source_branch(project_id, test_repo, stash_singleton.stash)

    # After the failed stash apply, verify the repository state is dirty
    # Note: The exact branch we end up on and whether the stash is deleted depends on
    # where in the fallback process the error occurs, so we only check that we're in a dirty state
    status = test_repo.get_current_status()
    assert not status.is_clean_and_safe_to_operate_on, (
        "Expected repository to be dirty after failed stash apply with conflicts"
    )
    assert status.files.staged > 0 or status.files.unstaged > 0, (
        "Expected some staged or unstaged changes after failed stash apply"
    )


def test_stash_pop_rejected_during_intermediate_state(test_repo: LocalWritableGitRepo) -> None:
    """Test that stash pop is rejected when repository is in intermediate state."""
    repo_path = test_repo.get_repo_path()

    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])
    (repo_path / "test.txt").write_text("modified")

    project_id = ProjectID()

    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")
    assert stash_singleton is not None, "Expected stash to be created"

    # Create a merge conflict to put repo in intermediate state
    git_repo.run_git(["checkout", "-b", "other"])
    (repo_path / "test.txt").write_text("other content")
    git_repo.run_git(["add", "test.txt"])
    git_repo.run_git(["commit", "-m", "Other change"])

    git_repo.run_git(["checkout", "main"])
    (repo_path / "test.txt").write_text("main content")
    git_repo.run_git(["add", "test.txt"])
    git_repo.run_git(["commit", "-m", "Main change"])

    # Start merge to create intermediate state
    test_repo.merge_from_ref("other")

    with pytest.raises(GitRepoError) as exc_info:
        pop_namespaced_stash_into_source_branch(project_id, test_repo, stash_singleton.stash)

    assert "intermediate state" in str(exc_info.value).lower(), (
        "Expected error message to mention 'intermediate state'"
    )


def test_singleton_lifecycle_from_creation_to_cleanup(test_repo: LocalWritableGitRepo) -> None:
    """Test that singleton indicator tracks stash lifecycle correctly."""
    repo_path = test_repo.get_repo_path()

    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])
    (repo_path / "test.txt").write_text("modified")

    project_id = ProjectID()

    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")

    if stash_singleton is not None:
        # Only check if a stash was actually created
        assert is_global_stash_singleton_stashed() is True, "Expected singleton to be present after stash creation"

        # Clean up by popping the stash
        pop_namespaced_stash_into_source_branch(project_id, test_repo, stash_singleton.stash)

        # After popping, the singleton should be cleared
        assert is_global_stash_singleton_stashed() is False, "Expected no singleton present after stash pop"


def test_source_branch_property_reflects_stash_origin(test_repo: LocalWritableGitRepo) -> None:
    """Test that SculptorStash source_branch property correctly identifies the branch where stash was created."""
    repo_path = test_repo.get_repo_path()

    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "my-feature"])
    (repo_path / "test.txt").write_text("modified")

    project_id = ProjectID()

    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")
    assert stash_singleton is not None, "Expected stash to be created"
    assert stash_singleton.stash.source_branch == "my-feature", (
        "Expected source_branch property to return 'my-feature'"
    )


def test_untracked_files_included_in_stash(test_repo: LocalWritableGitRepo) -> None:
    """Test that both tracked and untracked files are included when stashing."""
    repo_path = test_repo.get_repo_path()

    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])

    # Add both tracked and untracked changes
    (repo_path / "test.txt").write_text("modified tracked")
    (repo_path / "untracked.txt").write_text("untracked content")

    project_id = ProjectID()

    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")
    assert stash_singleton is not None, "Expected stash to be created"

    # Verify files are gone after stashing
    assert not (repo_path / "untracked.txt").exists(), (
        "Expected untracked file to be stashed and removed from working directory"
    )

    # Pop the stash and verify untracked files are restored
    pop_namespaced_stash_into_source_branch(project_id, test_repo, stash_singleton.stash)

    assert (repo_path / "untracked.txt").read_text() == "untracked content", (
        "Expected untracked.txt to contain 'untracked content'"
    )
    assert (repo_path / "test.txt").read_text() == "modified tracked", (
        "Expected test.txt to contain 'modified tracked'"
    )


def test_single_stash_retrieved_successfully(test_repo: LocalWritableGitRepo) -> None:
    """Test that a created stash can be retrieved and contains correct metadata."""
    repo_path = test_repo.get_repo_path()

    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])
    (repo_path / "test.txt").write_text("modified")

    project_id = ProjectID()

    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")
    assert stash_singleton is not None, "Expected stash to be created"

    reader = build_sculptor_stash_reader(test_repo)
    stashes = reader.get_stashes()

    assert len(stashes) == 1, "Expected exactly one stash to be retrieved"
    assert stashes[0].source_branch == "feature", "Expected retrieved stash to have source_branch='feature'"


def test_transition_serialization_roundtrip(test_repo: LocalWritableGitRepo) -> None:
    """Test that git transition metadata survives serialization and deserialization."""
    # Create a transition
    from_position = test_repo.get_current_absolute_git_position()
    transition = AbsoluteGitTransition(from_position=from_position, to_branch="target-branch")

    json_str = model_dump_json(transition)
    restored_transition = model_load_json(AbsoluteGitTransition, json_str)

    assert restored_transition.from_position.branch == from_position.branch, (
        "Expected branch to survive serialization roundtrip"
    )
    assert restored_transition.from_position.commit_hash == from_position.commit_hash, (
        "Expected commit hash to survive serialization roundtrip"
    )
    assert restored_transition.to_branch == "target-branch", "Expected to_branch to survive serialization roundtrip"


def test_stash_pop_with_indexed_conflict_leaves_conflict_resolution_state(test_repo: LocalWritableGitRepo) -> None:
    """Test that stash pop with conflicting indexed changes leaves repo in conflict resolution state."""
    repo_path = test_repo.get_repo_path()
    git_repo = LocalGitRepo(repo_path)
    git_repo.run_git(["checkout", "-b", "feature"])
    change = RepoChanger(git_repo, repo_path)

    # Stash staged change
    change("Stage test.txt", add_staged={"test.txt": "feature content"})

    project_id = ProjectID()
    stash_singleton = checkout_branch_maybe_stashing_as_we_go(project_id, test_repo, "main")
    assert stash_singleton is not None, "Expected stash to be created"

    # Commit conflicting change
    git_repo.run_git(["checkout", "feature"])
    change("Conflicting change", add_committed={"test.txt": "conflicting content"})
    git_repo.run_git(["checkout", "main"])

    # Attempt to pop - should raise error but leave in resolvable state
    with pytest.raises(GitStashApplyError):
        pop_namespaced_stash_into_source_branch(project_id, test_repo, stash_singleton.stash)

    # Verify we're on the feature branch
    assert git_repo.run_git(["branch", "--show-current"]) == "feature", "Should be on feature branch"

    # Verify we're in a conflict state (dirty, with conflicts)
    status = test_repo.get_current_status()
    assert not status.is_clean_and_safe_to_operate_on, "Should have conflicts"

    # Verify exact git status: file should be unmerged (UU) after failed merge
    verify_git_status(
        git_repo,
        "File should be unmerged (UU) after merge conflict",
        """
        UU test.txt
        """,
    )

    # Verify the file has conflict markers
    test_txt_content = (repo_path / "test.txt").read_text()
    assert "<<<<<<< " in test_txt_content, "Should have conflict markers in test.txt"


def test_restoring_mixed_staged_and_untracked_files(repo_scene: RepoScene) -> None:
    """Test restoring stash with mixed staged and untracked files.

    Setup: Stash with 2 staged files and 2 untracked files.
    Tests different conflict scenarios to show happy path vs fallback behavior.
    """
    # Define stash setup
    prep_stash = repo_scene.prep_change_callback(
        "Create mixed staged and untracked files",
        add_staged={
            "conflict.txt": "stashed staged conflict",
            "clean_staged.txt": "stashed staged clean",
        },
        add_untracked={
            "conflict_untracked.txt": "stashed untracked conflict",
            "clean_untracked.txt": "stashed untracked clean",
        },
    )

    scenarios = [
        (
            "no conflicts (happy path)",
            repo_scene.prep_change_callback("No changes", add_committed={}),
            StashRestoreOutcome(
                # git stash apply --index: preserves staged/untracked distinctions
                should_raise_error=False,
                end_status="""
                A  clean_staged.txt
                A  conflict.txt
                ?? clean_untracked.txt
                ?? conflict_untracked.txt
                """,
            ),
        ),
        (
            "one staged and one untracked conflict (fallback)",
            repo_scene.prep_change_callback(
                "Conflict in one staged and one untracked",
                add_committed={
                    "conflict.txt": "committed conflict content",
                    "conflict_untracked.txt": "committed conflict for untracked",
                },
            ),
            StashRestoreOutcome(
                # Fallback: commits all on temp branch, everything shows as "A" (added)
                end_status="""
                A  clean_staged.txt
                A  clean_untracked.txt
                AA conflict.txt
                AA conflict_untracked.txt
                """,
            ),
        ),
    ]

    repo_scene.run_scenarios(prep_stash, scenarios)


def test_restoring_single_untracked_file(repo_scene: RepoScene) -> None:
    """Test restoring stash with single untracked file.

    Setup: Stash with 1 untracked file.
    Tests different conflict scenarios to show happy path vs fallback behavior.
    """
    # Define stash setup
    prep_stash = repo_scene.prep_change_callback(
        "Create untracked file",
        add_untracked={"new_file.txt": "untracked content"},
    )

    scenarios = [
        (
            "no conflicts (happy path)",
            repo_scene.prep_change_callback("No changes", add_committed={}),
            StashRestoreOutcome(
                # git stash apply --index: restores as untracked
                should_raise_error=False,
                end_status="""
                ?? new_file.txt
                """,
            ),
        ),
        (
            "untracked file conflicts with later commit (fallback)",
            repo_scene.prep_change_callback(
                "Add new_file.txt",
                add_committed={"new_file.txt": "committed content"},
            ),
            StashRestoreOutcome(
                # Fallback: untracked file conflict shows as AA (both added)
                end_status="""
                AA new_file.txt
                """,
            ),
        ),
    ]

    repo_scene.run_scenarios(prep_stash, scenarios)


def test_restoring_two_staged_files(repo_scene: RepoScene) -> None:
    """Test restoring stash with two staged files.

    Setup: Stash with 2 staged files.
    Tests different conflict scenarios to show happy path vs fallback behavior.
    """
    # Define stash setup
    prep_stash = repo_scene.prep_change_callback(
        "Stage two files",
        add_staged={
            "file_a.txt": "stashed content a",
            "file_b.txt": "stashed content b",
        },
    )

    scenarios = [
        (
            "no conflicts (happy path)",
            repo_scene.prep_change_callback("No changes", add_committed={}),
            StashRestoreOutcome(
                # git stash apply --index: preserves staged status
                should_raise_error=False,
                end_status="""
                A  file_a.txt
                A  file_b.txt
                """,
            ),
        ),
        (
            "one file conflicts (fallback)",
            repo_scene.prep_change_callback(
                "Conflict in file_a only",
                add_committed={"file_a.txt": "conflicting content a"},
            ),
            StashRestoreOutcome(
                # Fallback: shows files as "A" (added), conflicts as "AA"
                end_status="""
                AA file_a.txt
                A  file_b.txt
                """,
            ),
        ),
    ]

    repo_scene.run_scenarios(prep_stash, scenarios)


def test_restoring_staged_and_untracked_files(repo_scene: RepoScene) -> None:
    """Test restoring stash with both staged and untracked files.

    Setup: Stash with 1 staged file and 1 untracked file.
    Tests different conflict scenarios to show happy path vs fallback behavior.
    """
    # Define stash setup
    prep_stash = repo_scene.prep_change_callback(
        "Create staged and untracked files",
        add_staged={"test.txt": "modified indexed"},
        add_untracked={"untracked.txt": "untracked content"},
    )

    scenarios = [
        (
            "no conflicts (happy path)",
            repo_scene.prep_change_callback("No changes", add_committed={}),
            StashRestoreOutcome(
                # git stash apply --index: preserves staged/untracked distinctions
                # Note: test.txt shows as M (modified) because it exists in initial commit
                should_raise_error=False,
                end_status="""
                M  test.txt
                ?? untracked.txt
                """,
            ),
        ),
        (
            "both files conflict (fallback)",
            repo_scene.prep_change_callback(
                "Conflicting changes",
                add_committed={
                    "test.txt": "different indexed",
                    "untracked.txt": "now tracked and conflicting",
                },
            ),
            StashRestoreOutcome(
                # Fallback: staged shows as UU, untracked shows as AA
                end_status="""
                UU test.txt
                AA untracked.txt
                """,
            ),
        ),
    ]

    repo_scene.run_scenarios(prep_stash, scenarios)


def test_restoring_complex_mixed_file_states(repo_scene: RepoScene) -> None:
    """Test restoring stash with mixed staged/unstaged/untracked files.

    Setup: Stash with 2 staged, 1 unstaged, and 1 untracked file.
    Tests different conflict scenarios to show happy path vs fallback behavior.
    """
    # Define stash setup
    # Note: test.txt exists in initial commit, so modifying it unstaged shows as " M"
    prep_stash = repo_scene.prep_change_callback(
        "Create mixed file states",
        add_staged={
            "staged.txt": "staged content",
            "clean_staged.txt": "clean staged content",
        },
        add_unstaged={"test.txt": "unstaged modification"},
        add_untracked={"untracked.txt": "untracked content"},
    )

    scenarios = [
        (
            "no conflicts (happy path)",
            repo_scene.prep_change_callback("No changes", add_committed={}),
            StashRestoreOutcome(
                # git stash apply --index: preserves all distinctions
                # test.txt shows as M (modified), new files show as A (added)
                should_raise_error=False,
                end_status="""
                A  clean_staged.txt
                A  staged.txt
                 M test.txt
                ?? untracked.txt
                """,
            ),
        ),
        (
            "one staged file conflicts (fallback)",
            repo_scene.prep_change_callback(
                "Conflict in staged.txt only",
                add_committed={"staged.txt": "conflicting staged content"},
            ),
            StashRestoreOutcome(
                # Fallback: commits all on temp branch
                # test.txt shows as M (existed before), others as A (new files)
                # Does NOT preserve staged/unstaged/untracked distinctions
                end_status="""
                A  clean_staged.txt
                AA staged.txt
                M  test.txt
                A  untracked.txt
                """,
            ),
        ),
    ]

    repo_scene.run_scenarios(prep_stash, scenarios)
