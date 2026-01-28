import datetime
from abc import ABC
from abc import abstractmethod
from pathlib import Path

from pydantic import AnyUrl
from pydantic import Field
from pydantic import computed_field

from imbue_core.pydantic_serialization import MutableModel
from imbue_core.pydantic_serialization import SerializableModel


class AbsoluteGitPosition(SerializableModel):
    """Pointer to a specific commit within a specific context in a git repository.

    Uses the term "position" rather than "reference" to avoid confusion with git references (branches, tags, etc).
    """

    repo_url: AnyUrl
    branch: str
    commit_hash: str
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now)

    def describe(self) -> str:
        return f"commit {self.commit_hash} on branch {self.branch} in repository {self.repo_url} created at {self.created_at.isoformat()}"

    @property
    def ref_safe_identifier(self) -> str:
        ts = self.created_at.strftime("%Y%m%d_%H%M_%S")
        return f"{self.branch}/{self.commit_hash[:8]}-{ts}"


class GitRepoFileStatus(SerializableModel):
    unstaged: int
    staged: int
    untracked: int
    deleted: int

    @computed_field
    @property
    def are_clean_including_untracked(self) -> bool:
        return all((f == 0 for f in (self.unstaged, self.staged, self.deleted, self.untracked)))

    @computed_field
    @property
    def description(self) -> str:
        if self.are_clean_including_untracked:
            return "no changed or unstaged files"

        maybe_description = (
            lambda count, name, entity: f"{count} {name} {entity}{'s' if count > 1 else ''}" if count > 0 else None
        )
        return "\n".join(
            filter(
                None,
                (
                    maybe_description(self.staged, "staged", "change"),
                    maybe_description(self.unstaged, "unstaged", "change"),
                    maybe_description(self.untracked, "untracked", "file"),
                    maybe_description(self.deleted, "deleted", "file"),
                ),
            )
        )


class GitRepoStatus(SerializableModel):
    """
    Current status of a git repository.

    Contains information about the working directory state, including
    merge/rebase/cherry-pick status and file change counts.
    """

    files: GitRepoFileStatus
    is_merging: bool
    is_rebasing: bool
    is_cherry_picking: bool

    @computed_field
    @property
    def is_in_intermediate_state(self) -> bool:
        return self.is_merging or self.is_rebasing or self.is_cherry_picking

    @computed_field
    @property
    def is_clean_and_safe_to_operate_on(self) -> bool:
        return self.files.are_clean_including_untracked and not self.is_in_intermediate_state

    def describe(self, is_file_changes_list_included: bool = True) -> str:
        ops_in_progress = []
        if self.is_merging:
            ops_in_progress.append("merge in progress")
        if self.is_rebasing:
            ops_in_progress.append("rebase in progress")
        if self.is_cherry_picking:
            ops_in_progress.append("cherry-pick in progress")

        ops = ", ".join(ops_in_progress) if ops_in_progress else "no operations in progress"
        if not is_file_changes_list_included:
            return ops
        return f"{ops}, \n{self.files.description}"


class GitRepoMergeResult(SerializableModel):
    is_merged: bool
    is_stopped_by_uncommitted_changes: bool = False
    was_up_to_date: bool = False
    is_aborted: bool = False

    raw_output: str

    @computed_field
    @property
    def description(self) -> str:
        if self.is_merged:
            if self.was_up_to_date:
                return "already up to date"
            else:
                return "merge successful"
        elif self.is_aborted:
            return "merge resulted in conflicts and was aborted"
        elif self.is_stopped_by_uncommitted_changes:
            return "uncommitted changes are blocking the merge"
        else:
            return "merge resulted in conflicts"


class ReadOnlyGitRepo(MutableModel, ABC):
    """
    All read operations on a git repository should be done through this interface.

    Should all raise FileNotFoundError if the repository does not exist.
    """

    @abstractmethod
    def get_repo_path(self) -> Path: ...

    @abstractmethod
    def get_repo_url(self) -> AnyUrl: ...

    @property
    def is_bare_repo(self) -> bool: ...

    @abstractmethod
    def get_all_branches(self) -> list[str]:
        """
        Get a list of all local branches in the repository.
        """

    @abstractmethod
    def get_current_commit_hash(self) -> str:
        """
        The output of `git rev-parse HEAD`

        Obviously there may be other current (uncommitted or untracked) changes in the repository,
        """

    @abstractmethod
    def get_branch_head_commit_hash(self, branch_name: str) -> str:
        """
        Get the commit hash of the head of the specified branch.
        """

    @abstractmethod
    def get_current_git_branch(self) -> str: ...

    @abstractmethod
    def export_current_repo_state(self, target_folder: Path) -> None: ...

    @abstractmethod
    def get_current_absolute_git_position(self) -> AbsoluteGitPosition: ...

    @abstractmethod
    def get_num_uncommitted_changes(self) -> int: ...

    @abstractmethod
    def is_branch_ref(self, branch: str) -> bool: ...

    @abstractmethod
    def list_matching_folders(self, pattern: str = "") -> list[str]:
        """
        List all folders in the repository.
        """

    @abstractmethod
    def list_matching_files(self, pattern: str | None = None) -> list[str]:
        """
        List all files in the repository.
        """

    @abstractmethod
    def list_untracked_files(self) -> list[str]: ...

    @abstractmethod
    def read_file(self, repo_relative_path: Path) -> str | None:
        """
        Return the contents of the file at the current commit (or None if the file does not exist).
        """

    @property
    @abstractmethod
    def is_merge_in_progress(self) -> bool: ...

    @property
    @abstractmethod
    def is_rebase_in_progress(self) -> bool: ...

    @property
    @abstractmethod
    def is_cherry_pick_in_progress(self) -> bool: ...

    @abstractmethod
    def get_current_status(self, is_read_only_and_lockless: bool = False) -> GitRepoStatus:
        """
        Get the current status of the git repository.

        Returns information about the working directory state, including
        merge/rebase status and file change counts.
        """


class WritableGitRepo(ReadOnlyGitRepo, ABC):
    """
    All write operations on a git repository should be done through this interface.
    """

    @abstractmethod
    def fetch_remote_branch_into_local(
        self,
        local_branch: str,
        remote: AnyUrl,
        remote_branch: str,
        dry_run: bool = False,
        force: bool = False,
        dangerously_update_head_ok: bool = False,
    ) -> None: ...

    @abstractmethod
    def maybe_fetch_remote_branch_into_local(
        self,
        local_branch: str,
        remote: AnyUrl,
        remote_branch: str,
        dry_run: bool = False,
        force: bool = False,
        dangerously_update_head_ok: bool = False,
    ) -> bool:
        """Wrapper around fetch_remote_branch_into_local that returns "expected" success/failure as boolean."""
        ...

    @abstractmethod
    def merge_from_ref(self, ref: str, commit_message: str | None = None) -> GitRepoMergeResult:
        """Merge the given ref into current checkout.

        Does not re-raise any git operation errors.
        """
        ...

    @abstractmethod
    def pull_from_remote(
        self,
        remote: str,
        remote_branch: str,
        should_abort_on_conflict: bool = False,
        is_fast_forward_only: bool = False,
        assert_local_branch_equals_to: str | None = None,
    ) -> GitRepoMergeResult: ...

    @abstractmethod
    def ensure_local_branch_has_remote_branch_ref(self, remote_repo: AnyUrl, remote_branch: str) -> bool: ...

    @abstractmethod
    def git_checkout_branch(self, branch_name: str) -> None:
        """Checkout a git branch."""
        ...

    @abstractmethod
    def reset_working_directory(self) -> None:
        """Reset working directory to clean state."""
        ...

    @abstractmethod
    def delete_tag(self, tag_name: str) -> bool:
        """Delete a local git tag if it exists.

        Returns:
            True if the tag was successfully deleted, False if it didn't exist.
        """
        ...

    @abstractmethod
    def _run_git(self, args: list[str]) -> str: ...

    @abstractmethod
    def push_ref_to_remote(self, remote: str, local_ref: str, remote_ref: str, is_forced: bool = False) -> str: ...

    @abstractmethod
    def create_branch(self, branch_name: str, start_point: str | None = None) -> None:
        """Create a new branch at the specified start point.

        Args:
            branch_name: Name of the branch to create
            start_point: Optional commit hash or ref to start the branch from. Defaults to HEAD.
        """
        ...
