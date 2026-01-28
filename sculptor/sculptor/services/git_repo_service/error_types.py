from enum import auto

from pydantic import AnyUrl

from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.upper_case_str_enum import UpperCaseStrEnum


class GitRepoError(Exception):
    """Exception raised when git operations"""

    def __init__(
        self,
        message: str,
        operation: str,
        repo_url: AnyUrl | None = None,
        branch_name: str | None = None,
        exit_code: int | None = None,
        stderr: str | bytes | None = None,
    ) -> None:
        # TODO these inits result in SerializedException.build(e).construct_instance() not working
        super().__init__(message)
        self.operation = operation
        self.repo_url = repo_url
        self.branch_name = branch_name
        self.exit_code = exit_code
        self.stderr = stderr

    def __str__(self) -> str:
        details = [super().__str__()]
        details.append(f"Operation: {self.operation}")
        if self.repo_url:
            details.append(f"Repository: {self.repo_url}")
        if self.branch_name is not None:
            details.append(f"Branch: {self.branch_name}")
        if self.exit_code is not None:
            details.append(f"Exit code: {self.exit_code}")
        if self.stderr:
            details.append(f"Stderr: {self.stderr}")
        return "\n".join(details)


class CommitRef(SerializableModel):
    ref_name: str
    commit_hash: str


class StashApplyEndState(UpperCaseStrEnum):
    # was able to branch off and merge --no-commit
    MERGE_FALlBACK_SUCCESS = auto()
    # merge fallback failed, left state as-is
    MERGE_FAILURE = auto()
    # recovery from merge fallback failure failed, we're cooked lads
    TOTAL_FAILURE = auto()


class GitStashApplyError(GitRepoError):
    def __init__(
        self,
        message: str,
        source_ref: CommitRef,
        end_state: StashApplyEndState,
        operation: str = "sculptor_stash_apply",
        repo_url: AnyUrl | None = None,
        branch_name: str | None = None,
        exit_code: int | None = None,
        stderr: str | bytes | None = None,
    ) -> None:
        super().__init__(message, operation, repo_url, branch_name, exit_code, stderr)
        self.source_ref = source_ref
        self.end_state = end_state

    def __str__(self) -> str:
        details = [super().__str__()]
        details.append(f"Source ref: {self.source_ref}")
        details.append(f"End state: {self.end_state}")
        return "\n".join(details)
