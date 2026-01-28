import os
from abc import ABC
from abc import abstractmethod
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from shlex import quote
from threading import Lock
from typing import Final
from typing import Generator
from typing import TypeVar

from loguru import logger
from pydantic import AnyUrl
from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.file_utils import atomic_writer_to
from imbue_core.subprocess_utils import ProcessError
from imbue_core.subprocess_utils import ProcessSetupError
from sculptor.database.models import Project
from sculptor.database.models import TaskID
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.constants import ENVIRONMENT_WORKSPACE_DIRECTORY
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.git_repo_service.git_repos import AbsoluteGitPosition
from sculptor.services.git_repo_service.git_repos import GitRepoFileStatus
from sculptor.services.git_repo_service.git_repos import GitRepoMergeResult
from sculptor.services.git_repo_service.git_repos import GitRepoStatus
from sculptor.services.git_repo_service.git_repos import ReadOnlyGitRepo
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.tasks.handlers.run_agent.errors import GitCommandFailure
from sculptor.tasks.handlers.run_agent.git import run_git_command_in_environment
from sculptor.tasks.handlers.run_agent.git import run_git_command_local
from sculptor.utils.timeout import log_runtime_decorator

T = TypeVar("T")

NULL_DELIMITER_FOR_FOOLPROOF_PARSING: Final = "\x00"
TRACKED_CHANGES_PATCHFILE_NAME: Final = "tracked_changes.patch"
UNTRACKED_FILES_TARBALL_NAME: Final = "untracked_files.tar"


def get_global_git_config(key: str, concurrency_group: ConcurrencyGroup) -> str | None:
    """Read global git configuration (e.g., "user.name"). Returns None if not set."""
    try:
        result = concurrency_group.run_process_to_completion(
            command=["git", "config", "--global", key], is_checked_after=True
        )
        return result.stdout.strip()
    except ProcessError:
        return None


class _GitRepoSharedMethods(ReadOnlyGitRepo, ABC):
    @abstractmethod
    def _run_git(self, args: list[str]) -> str: ...

    # TODO: is it necessary to have this here, instead of something shared by all git repos?
    # NOTE: A little goofy that a read-only repo can push a write to a different repo but seems fine
    def push_ref_to_remote(self, remote: str, local_ref: str, remote_ref: str, is_forced: bool = False) -> str:
        _validate_ref_normalcy(remote_ref)
        _validate_ref_normalcy(local_ref)
        args = [
            "push",
            "--no-verify",  # disable pre-push hook
        ]
        if is_forced:
            args.append("--force")

        args.extend(
            [
                remote,
                f"{local_ref}:{remote_ref}",
            ]
        )
        return self._run_git(args)


class _ReadOnlyGitRepoSharedMethods(_GitRepoSharedMethods, ABC):
    @abstractmethod
    def get_repo_url(self) -> AnyUrl:
        """Get a reference to the git repository."""
        ...

    @abstractmethod
    def read_file(self, repo_relative_path: Path) -> str | None: ...

    @abstractmethod
    def does_relative_file_exist(self, repo_relative_path: Path) -> bool: ...

    @cached_property
    def is_bare_repo(self) -> bool:
        return self._run_git(["rev-parse", "--is-bare-repository"]).strip() == "true"

    @property
    def _git_dir_relpath(self) -> Path:
        return Path(".") if self.is_bare_repo else Path(".git")

    def is_branch_ref(self, branch: str) -> bool:
        try:
            self._run_git(["rev-parse", "--verify", f"refs/heads/{branch}"])
            return True
        except GitRepoError:
            return False

    def get_current_commit_hash(self) -> str:
        return self._run_git(["rev-parse", "HEAD"]).strip()

    def get_branch_head_commit_hash(self, branch_name: str) -> str:
        "will raise GitRepoError if branch doesn't exist"
        return self._run_git(["rev-parse", branch_name]).strip()

    def get_current_git_branch(self, is_detached_head_ok: bool = True) -> str:
        """Get the current git branch name for a repository."""
        args = ["rev-parse", "--abbrev-ref", "HEAD"]
        logger.trace("Getting current branch...")
        try:
            branch = self._run_git(args).strip()
        except GitRepoError as e:
            if e.branch_name == "" or e.branch_name is None:
                raise
            branch = e.branch_name
        logger.trace("Current branch: {}", branch)
        if branch == "HEAD" and (not is_detached_head_ok):
            message = "in a detached HEAD state, which is not allowed in this context"
            raise GitRepoError(message=message, operation="git " + " ".join(args), repo_url=self.get_repo_url())
        return branch

    def get_num_uncommitted_changes(self) -> int:
        # TODO delete in favor of self.get_current_status, and/or add --no-optional-locks flag
        return len(self._run_git(["status", "--porcelain"]).strip().splitlines())

    def get_current_absolute_git_position(self) -> AbsoluteGitPosition:
        return AbsoluteGitPosition(
            repo_url=self.get_repo_url(),
            # TODO after adding all the validation above... couldn't we just go back to whatever commit and pop?
            branch=self.get_current_git_branch(is_detached_head_ok=False),
            commit_hash=self.get_current_commit_hash(),
        )

    def list_matching_folders(self, pattern: str = "") -> list[str]:
        """List all folders in the repository."""
        logger.info("Listing all folders in the repository...")
        result = self._run_git(["ls-tree", "-d", "--name-only", "-r", "-z", "HEAD"])
        folders = [f.strip() for f in result.split("\0")[:-1] if f.strip()]
        return [(f + "/") for f in folders if pattern.lower() in f.lower()]

    def list_matching_files(self, pattern: str | None = "") -> list[str]:
        """List all files in the repository."""
        logger.info("Listing all files in the repository...")
        result = self._run_git(["ls-files", "--cached", "--others", "-z"])
        files = [f.strip() for f in result.split("\0")[:-1] if f.strip()]
        if not pattern:
            return files
        return [f for f in files if pattern.lower() in f.lower()]

    def list_untracked_files(self) -> list[str]:
        """List all untracked files in the repository, including .gitignored files."""
        logger.info("Checking for untracked files (including .gitignored)...")
        result = self._run_git(["ls-files", "--others", "--exclude-standard", "-z"])
        return [f.strip() for f in result.split("\0")[:-1] if f.strip()]

    def _list_diff_files(self, is_staged: bool, diff_filter: str | None = None) -> list[str]:
        cmd = ["diff", "--name-only"]
        if is_staged:
            cmd.append("--cached")
        if diff_filter:
            cmd.append(f"--diff-filter={diff_filter}")
        result = self._run_git(cmd)
        return [f.strip() for f in result.split("\n") if f.strip()]

    # TODO(mjr): consider if this helper really belong here? also bare diff filter feels awkward
    def list_staged(self, diff_filter: str | None = None) -> list[str]:
        return self._list_diff_files(is_staged=True, diff_filter=diff_filter)

    def list_unstaged(self, diff_filter: str | None = None) -> list[str]:
        return self._list_diff_files(is_staged=False, diff_filter=diff_filter)

    @log_runtime_decorator("get_all_branches")
    def get_all_branches(self) -> list[str]:
        # Get all local branches in alphabetical order
        all_branches_result = self._run_git(["branch", "--format=%(refname:short)"])
        all_branches = [b.strip() for b in all_branches_result.strip().split("\n") if b.strip()]

        if not all_branches:
            # fallback to current branch if no branches found
            current = self.get_current_git_branch()
            return [current] if current and current != "HEAD" else []

        return all_branches

    # TODO: Hopefully we never run these over ssh
    @property
    def is_merge_in_progress(self) -> bool:
        return self.does_relative_file_exist(self._git_dir_relpath / "MERGE_HEAD")

    @property
    def is_rebase_in_progress(self) -> bool:
        if self.does_relative_file_exist(self._git_dir_relpath / "rebase-merge"):
            return True
        return self.does_relative_file_exist(self._git_dir_relpath / "rebase-apply")

    @property
    def is_cherry_pick_in_progress(self) -> bool:
        return self.does_relative_file_exist(self._git_dir_relpath / "CHERRY_PICK_HEAD")

    def get_current_status(self, is_read_only_and_lockless: bool = False) -> GitRepoStatus:
        """Get the current status of the git repository."""
        # NOTEs on the flags:
        #       --no-renames is used to simplify parsing, otherwise renames produce two lines per operation with -z
        #       --untracked-files=all can be fairly costly without the git's fsmonitor, this is also the default value
        #           for this flag; in practice repository with huge numbers of untracked files are a problem for us
        #           in all different areas anyway
        args = ["status", "--porcelain=v1", "-z", "--no-renames", "--ignored=no", "--untracked-files=all"]

        if is_read_only_and_lockless:
            args = ["--no-optional-locks", *args]
        status_output = self._run_git(args)

        repo_file_status = _parse_git_status_file_counts(status_output, delimiter=NULL_DELIMITER_FOR_FOOLPROOF_PARSING)
        return GitRepoStatus(
            files=repo_file_status,
            is_merging=self.is_merge_in_progress,
            is_rebasing=self.is_rebase_in_progress,
            is_cherry_picking=self.is_cherry_pick_in_progress,
        )


class LocalReadOnlyGitRepo(_ReadOnlyGitRepoSharedMethods):
    repo_path: Path
    concurrency_group: ConcurrencyGroup

    def get_repo_path(self) -> Path:
        """Get the path to the git repository."""
        return self.repo_path

    def get_repo_url(self) -> AnyUrl:
        return AnyUrl(f"file://{self.repo_path}")

    def has_any_commits(self) -> bool:
        """Check if repository has any commits. Returns False if not initialized or no commits exist."""
        if not (self.repo_path / ".git").exists():
            return False
        try:
            self.get_current_commit_hash()
            return True
        except GitRepoError:
            return False

    @cached_property
    def is_bare_repo(self) -> bool:
        return self._run_git(["rev-parse", "--is-bare-repository"]).strip() == "true"

    def export_current_repo_state(self, target_folder: Path) -> None:
        current_user_repo_path = self.repo_path
        # we are copying everything from .git *except* the objects folder, which can be very large
        # and we are copying all files that have changed (staged and unstaged) plus untracked files
        self.concurrency_group.run_process_to_completion(
            [
                "rsync",
                "-rav",
                "--no-D",
                "--exclude='.git/objects/'",
                str(current_user_repo_path / ".git").rstrip("/"),
                str(target_folder).rstrip("/") + "/",
            ],
            cwd=current_user_repo_path,
        )

        with atomic_writer_to(target_folder / TRACKED_CHANGES_PATCHFILE_NAME) as patch_file_writer:
            self.concurrency_group.run_process_to_completion(
                ["bash", "-c", f"git diff --binary --no-color > {quote(str(patch_file_writer))}"],
                cwd=current_user_repo_path,
            )

        with atomic_writer_to(target_folder / UNTRACKED_FILES_TARBALL_NAME) as untracked_files_archive_writer:
            self.concurrency_group.run_process_to_completion(
                [
                    "bash",
                    "-c",
                    f"git ls-files -z --others --exclude-standard | tar -cf {quote(str(untracked_files_archive_writer))} --null -T -",
                ],
                cwd=current_user_repo_path,
                env={**os.environ, "COPYFILE_DISABLE": "1"},
            )

    def _run_git(self, args: list[str]) -> str:
        """Run a git command in the specified repository."""
        try:
            cmd_to_run = ["git"] + args
            _, result_stdout, _ = run_git_command_local(
                self.concurrency_group, cmd_to_run, self.repo_path, is_retry_safe=False
            )
            return result_stdout
        except FileNotFoundError:
            raise
        except (GitCommandFailure, ProcessError) as e:
            if not self.repo_path.exists():
                raise FileNotFoundError(f"Repository path does not exist: {self.repo_path}") from e
            branch_name = None
            message = "Git command failed"
            try:
                cmd_to_run = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
                _, result_stdout, _ = run_git_command_local(
                    self.concurrency_group, cmd_to_run, self.repo_path, is_retry_safe=False
                )
                branch_name = result_stdout.strip()
            except Exception as e2:
                if isinstance(e2, FileNotFoundError):
                    raise
                if isinstance(e2, ProcessSetupError) and not self.repo_path.exists():
                    raise FileNotFoundError(f"Repository path does not exist: {self.repo_path}") from e
                if isinstance(e2, ProcessError) and "unknown revision or path not in the working tree" in e.stderr:
                    message += " (repository appears to be empty, no commits yet)"
                else:
                    message += f" (failed to get current branch name for error reporting: {e})"
            raise GitRepoError(
                message=message,
                operation=" ".join(args),
                branch_name=branch_name,
                repo_url=self.get_repo_url(),
                exit_code=getattr(e, "returncode", -1),
                stderr=e.stderr,
            ) from e

    def read_file(self, repo_relative_path: Path) -> str | None:
        try:
            file_path = self.repo_path / repo_relative_path
            if not file_path.exists():
                return None
            with file_path.open("r", encoding="utf-8") as f:
                return f.read()
        except (OSError, FileNotFoundError) as e:
            logger.trace("Failed to read file {}: {}", repo_relative_path, e)
            return None
        except Exception as e:
            log_exception(e, "Failed to read file from git repository", priority=ExceptionPriority.LOW_PRIORITY)
            return None

    def does_relative_file_exist(self, repo_relative_path: Path) -> bool:
        return (self.repo_path / repo_relative_path).exists()


class RemoteReadOnlyGitRepo(_ReadOnlyGitRepoSharedMethods):
    environment: Environment

    def get_repo_url(self) -> AnyUrl:
        return self.environment.get_repo_url()

    def get_repo_path(self) -> Path:
        msg = "RemoteReadOnlyGitRepo does not have a local path. Leaving it in the base class for legacy reasons."
        raise NotImplementedError(msg)

    def get_internal_environment_path_str(self, repo_relative_path: Path) -> str:
        return f"{self.environment.get_workspace_path()}/{repo_relative_path.as_posix()}"

    def export_current_repo_state(self, target_folder: Path) -> None:
        raise NotImplementedError("No need to support this yet")

    def read_file(self, repo_relative_path: Path) -> str | None:
        try:
            content = self.environment.read_file(self.get_internal_environment_path_str(repo_relative_path))
            assert isinstance(content, str), "this shouldn't be called much but should definitely be text if it is"
            return content
        except FileNotFoundError as e:
            logger.trace("Failed to read file {}: {}", repo_relative_path, e)
            return None

    def does_relative_file_exist(self, repo_relative_path: Path) -> bool:
        try:
            return self.environment.exists(self.get_internal_environment_path_str(repo_relative_path))
        except FileNotFoundError as e:
            logger.trace("Failed to read file {}: {}", repo_relative_path, e)
            return False

    def _run_git(self, args: list[str]) -> str:
        """Run a git command in the specified repository."""
        try:
            cmd_to_run = ["git"] + args
            _, result_stdout, _ = run_git_command_in_environment(
                self.environment, cmd_to_run, secrets={}, cwd=str(ENVIRONMENT_WORKSPACE_DIRECTORY), is_retry_safe=False
            )
            return result_stdout
        except FileNotFoundError:
            raise
        except (GitCommandFailure, ProcessError) as e:
            branch_name = None
            message = "Git command failed"
            try:
                cmd_to_run = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
                _, result_stdout, _ = run_git_command_in_environment(
                    self.environment,
                    cmd_to_run,
                    secrets={},
                    is_retry_safe=False,
                    cwd=str(ENVIRONMENT_WORKSPACE_DIRECTORY),
                )
                branch_name = result_stdout.strip()
            except Exception as e2:
                if isinstance(e2, FileNotFoundError):
                    raise
                if isinstance(e2, ProcessError) and "unknown revision or path not in the working tree" in e.stderr:
                    message += " (repository appears to be empty, no commits yet)"
                else:
                    message += f" (failed to get current branch name for error reporting: {e})"
            raise GitRepoError(
                message=message,
                operation=" ".join(args),
                branch_name=branch_name,
                repo_url=self.get_repo_url(),
                exit_code=getattr(e, "returncode", -1),
                stderr=e.stderr,
            ) from e


def _parse_git_status_file_counts(status_output: str, delimiter: str) -> GitRepoFileStatus:
    """
    Parses the output of git status. Expects it to be a list
    of delimiter-separated entries. One entry per file.

    Each entry in the result describes a single file (newlines in paths
    are escaped by `git`). Each entry opens with two characters (XY below)
    and is followed by a filename or two (for renames).

    Excerpt from `git-status` manual page, the first section describes the
    results if a no merge is in progress or merge is resolved, the second
    section indicates the details of a merge that is in progress, untracked
    and ignored files are shows independently of the two states.

        X          Y     Meaning
        -------------------------------------------------
                 [AMD]   not updated
        M        [ MTD]  updated in index
        T        [ MTD]  type changed in index
        A        [ MTD]  added to index
        D                deleted from index
        R        [ MTD]  renamed in index
        C        [ MTD]  copied in index
        [MTARC]          index and work tree matches
        [ MTARC]    M    work tree changed since index
        [ MTARC]    T    type changed in work tree since index
        [ MTARC]    D    deleted in work tree
                    R    renamed in work tree
                    C    copied in work tree
        -------------------------------------------------
        D           D    unmerged, both deleted
        A           U    unmerged, added by us
        U           D    unmerged, deleted by them
        U           A    unmerged, added by them
        D           U    unmerged, deleted by us
        A           A    unmerged, both added
        U           U    unmerged, both modified
        -------------------------------------------------
        ?           ?    untracked
        !           !    ignored
        -------------------------------------------------
    """

    unstaged_files = 0
    staged_files = 0
    untracked_files = 0
    deleted_files = 0
    ignored_files = 0

    for line in status_output.split(delimiter):
        if not line:
            continue

        # Porcelain format: XY filename
        # X = staged status, Y = unstaged status
        if len(line) < 2:
            continue
        staged_status = line[0]
        unstaged_status = line[1]

        # Count total deleted files (can be staged or unstaged)
        if staged_status == "D" or unstaged_status == "D":
            deleted_files += 1
        # Count total unstaged changes
        if unstaged_status != " " and unstaged_status != "?" and unstaged_status != "!":
            unstaged_files += 1

        # Untracked files will have both flags set to '?'
        if staged_status == "?" or unstaged_status == "?":
            untracked_files += 1
        # Ignored files will have both flags set to '!'
        elif staged_status == "!" or unstaged_status == "!":
            ignored_files += 1
        # Count staged changes, their nature does not matter
        elif staged_status != " ":
            staged_files += 1

    # NOTE: ignored files are ignored, keeping the parsing logic above in case they are needed in the future
    return GitRepoFileStatus(
        unstaged=unstaged_files,
        staged=staged_files,
        untracked=untracked_files,
        deleted=deleted_files,
    )


class _WritableGitRepoSharedMethods(_GitRepoSharedMethods, WritableGitRepo, ABC):
    @abstractmethod
    def _run_git(self, args: list[str]) -> str: ...

    def _process_merge_error(
        self, e: GitRepoError, *, is_fast_forward_only: bool = False, should_abort_on_conflict: bool = False
    ) -> GitRepoMergeResult:
        """Parse git merge/pull errors and return appropriate GitRepoMergeResult.

        Common parsing logic for both merge_from_ref and pull_from_remote operations.
        """
        stderr = str(e.stderr)

        # Check for fast-forward only failure
        is_fastforward_failure = (
            is_fast_forward_only
            and e.exit_code == 128
            and any(line.startswith("fatal: Not possible to fast-forward") for line in stderr.splitlines())
        )
        if is_fastforward_failure:
            return GitRepoMergeResult(is_merged=False, raw_output=stderr)

        # Check for uncommitted changes blocking the merge
        # "error: Your local changes to the following files would be overwritten by merge"
        # "error: The following untracked working tree files would be overwritten"
        if any((line.startswith("error:") and "files would be overwritten" in line) for line in stderr.splitlines()):
            return GitRepoMergeResult(
                is_merged=False,
                is_stopped_by_uncommitted_changes=True,
                raw_output=stderr,
            )

        # Check if merge is in progress (conflicts occurred)
        if self.is_merge_in_progress:
            if should_abort_on_conflict:
                logger.debug("Attempting to abort the merge operation")
                # TODO: handle `git merge --abort` failing?
                abort_output = self._run_git(["merge", "--abort"])
                return GitRepoMergeResult(
                    is_merged=False, is_aborted=True, raw_output="\n\n".join((stderr, abort_output))
                )
            else:
                return GitRepoMergeResult(
                    is_merged=False,
                    raw_output=stderr,
                )

        # If none of the above conditions matched and fast-forward wasn't required,
        # return a general failure result
        if not is_fast_forward_only:
            return GitRepoMergeResult(
                is_merged=False,
                raw_output=stderr,
            )

        # Re-raise if we couldn't parse the error
        raise e

    def fetch_remote_branch_into_local(
        self,
        local_branch: str,
        remote: AnyUrl,
        remote_branch: str,
        dry_run: bool = False,
        force: bool = False,
        dangerously_update_head_ok: bool = False,
    ) -> None:
        """Fetch remote branch into local branch. Raises GitRepoError on any failure."""
        logger.debug(
            "Attempting to fetch the branch {} from {} onto {} (force={}, dry-run={}, update-head-ok={})",
            remote_branch,
            remote,
            local_branch,
            force,
            dry_run,
            dangerously_update_head_ok,
        )
        # NOTE: we can't use --porcelain, it's too new
        #       this means the result is on stderr and not stdout
        #       and that it includes other garbage in the output
        args = [
            "fetch",
            str(remote),
            f"refs/heads/{remote_branch}:refs/heads/{local_branch}",
            "--show-forced-updates",
            "--no-tags",
        ]
        if dry_run:
            args.append("--dry-run")
        if force:
            args.append("--force")
        if dangerously_update_head_ok:
            args.append("--update-head-ok")

        try:
            self._run_git(args)
            logger.debug("Git fetch successful ({})", " ".join(args))
        except GitRepoError as e:
            logger.debug("Fetch failed with exit code {}; {} (full command: {})", e.exit_code, str(e), " ".join(args))
            # Re-raise all errors - let caller decide how to handle them
            raise

    def maybe_fetch_remote_branch_into_local(
        self,
        local_branch: str,
        remote: AnyUrl,
        remote_branch: str,
        dry_run: bool = False,
        force: bool = False,
        dangerously_update_head_ok: bool = False,
    ) -> bool:
        """Wrapper around fetch_remote_branch_into_local that returns success/failure as boolean."""
        try:
            self.fetch_remote_branch_into_local(
                local_branch=local_branch,
                remote=remote,
                remote_branch=remote_branch,
                dry_run=dry_run,
                force=force,
                dangerously_update_head_ok=dangerously_update_head_ok,
            )
            return True
        except GitRepoError as e:
            if e.exit_code == 1:
                # FIXME: parse the stderr to confirm that we got a rejection and not bad refs or similar
                return False
            # likely 128 and an unexpected error
            raise

    def merge_from_ref(self, ref: str, commit_message: str | None = None) -> GitRepoMergeResult:
        """Merge the given ref into current checkout.

        Does not re-raise any git operation errors.
        """
        logger.debug("Merging from ref {} onto local branch (message={})", ref, commit_message)
        # FIXME: would want to have --no-autostash but that's not something that all git versions support
        args = ["merge", "--commit", "--ff", "--no-edit", "--stat"]
        if commit_message:
            args.extend(["-m", commit_message])

        args.append(ref)
        try:
            merge_output = self._run_git(args)
            return GitRepoMergeResult(
                is_merged=True,
                raw_output=merge_output,
                was_up_to_date=_is_git_merge_result_up_to_date(stdout=merge_output),
            )
        except GitRepoError as e:
            return self._process_merge_error(e, is_fast_forward_only=False, should_abort_on_conflict=False)

    def pull_from_remote(
        self,
        remote: str,
        remote_branch: str,
        should_abort_on_conflict: bool = False,
        is_fast_forward_only: bool = False,
        assert_local_branch_equals_to: str | None = None,
    ) -> GitRepoMergeResult:
        # TODO: consider auto-stashing as an option
        logger.debug(
            "Pulling a remote branch {} from {} onto local branch (should_abort_on_conflict={}, is_fast_forward_only={}, assert_local_branch_equals_to={})",
            remote_branch,
            remote,
            should_abort_on_conflict,
            is_fast_forward_only,
            assert_local_branch_equals_to,
        )

        args = [
            "pull",
            remote,
            f"refs/heads/{remote_branch}",
            "--no-rebase",
            "--no-tags",
        ]
        if is_fast_forward_only:
            args.append("--ff-only")

        try:
            merge_output = self._run_git(args)
            logger.debug("Git pull successful ({}). Output: {}", " ".join(args), merge_output)

            return GitRepoMergeResult(
                is_merged=True,
                raw_output=merge_output,
                was_up_to_date=_is_git_merge_result_up_to_date(stdout=merge_output),
            )
        except GitRepoError as e:
            return self._process_merge_error(
                e, is_fast_forward_only=is_fast_forward_only, should_abort_on_conflict=should_abort_on_conflict
            )

    def ensure_local_branch_has_remote_branch_ref(self, remote_repo: AnyUrl, remote_branch: str) -> bool:
        return self.is_branch_ref(remote_branch) or self.maybe_fetch_remote_branch_into_local(
            local_branch=remote_branch, remote=remote_repo, remote_branch=remote_branch
        )

    def git_checkout_branch(self, branch_name: str) -> None:
        """Checkout a git branch."""
        logger.debug("Checking out task branch: {}", branch_name)
        self._run_git(["checkout", branch_name])

    def reset_working_directory(self) -> None:
        """Reset working directory to clean state."""
        logger.debug("Cleaning up uncommitted changes...")
        self._run_git(["reset", "--hard", "HEAD"])
        logger.info("Reset staged/modified files")

        self._run_git(["clean", "-fd"])
        logger.debug("Removed untracked files and directories")

    def delete_tag(self, tag_name: str) -> bool:
        try:
            self._run_git(["tag", "-d", tag_name])
            logger.debug("deleted tag: {}", tag_name)
            return True
        except GitRepoError as e:
            # It's okay if the tag doesn't exist, log and continue
            if "not found" in str(e.stderr).lower():
                logger.debug("couldn't delete tag {}: does not exist", tag_name)
                return False
            # let the caller handle it
            raise

    def create_branch(self, branch_name: str, start_point: str | None = None) -> None:
        """
        Create a new branch at the specified start point.
        Raises GitRepoError if the branch already exists or any other error occurs.
        """
        args = ["branch", branch_name]
        if start_point:
            args.append(start_point)

        self._run_git(args)
        logger.debug("Created branch '{}' at {}", branch_name, start_point or "HEAD")


class LocalWritableGitRepo(LocalReadOnlyGitRepo, _WritableGitRepoSharedMethods):
    @classmethod
    def from_new_repository(
        cls,
        repo_path: Path,
        concurrency_group: ConcurrencyGroup,
        user_email: str | None = None,
        user_name: str | None = None,
    ) -> "LocalWritableGitRepo":
        """Factory method that creates a NEW git repository and returns a LocalWritableGitRepo wrapper for it.

        This method runs `git init` to create a fresh repository at the specified path.
        For wrapping an EXISTING repository, use the regular constructor: LocalWritableGitRepo(repo_path, concurrency_group)

        Args:
            repo_path: Path where the new repository will be initialized (directory must exist but should not contain a .git folder)
            concurrency_group: ConcurrencyGroup to use for running git commands
            user_email: Optional email to configure for the repository. If not provided, uses global git config.
            user_name: Optional name to configure for the repository. If not provided, uses global git config.

        Returns:
            LocalWritableGitRepo: A wrapper for the newly created repository

        Raises:
            GitRepoError: If the directory doesn't exist, already contains a git repository, or initialization fails
        """
        if (repo_path / ".git").exists():
            raise GitRepoError(
                message=f"Directory is already a git repository: {repo_path}",
                operation="init",
                repo_url=AnyUrl(f"file://{repo_path}"),
                exit_code=None,
                stderr="",
            )

        if not repo_path.exists():
            raise GitRepoError(
                message=f"Directory does not exist: {repo_path}",
                operation="init",
                repo_url=None,
                exit_code=None,
                stderr="",
            )

        try:
            result = concurrency_group.run_process_to_completion(
                command=["git", "init"], cwd=repo_path, is_checked_after=True
            )
            logger.debug("Initialized git repository at: {}", repo_path)
        except ProcessError as e:
            raise GitRepoError(
                message="Failed to initialize git repository",
                operation="init",
                repo_url=AnyUrl(f"file://{repo_path}"),
                exit_code=e.returncode,
                stderr=e.stderr,
            ) from e

        # Only configure user.email and user.name if explicitly provided
        if user_email is not None and user_name is not None:
            try:
                concurrency_group.run_process_to_completion(
                    command=["git", "config", "user.email", user_email], cwd=repo_path, is_checked_after=True
                )
                concurrency_group.run_process_to_completion(
                    command=["git", "config", "user.name", user_name], cwd=repo_path, is_checked_after=True
                )
                logger.debug("Configured git user.email={} user.name={}", user_email, user_name)
            except ProcessError as e:
                raise GitRepoError(
                    message="Failed to configure git user",
                    operation="config",
                    repo_url=AnyUrl(f"file://{repo_path}"),
                    exit_code=e.returncode,
                    stderr=e.stderr,
                ) from e

        return cls(repo_path=repo_path, concurrency_group=concurrency_group)

    def stage_all_files(self) -> None:
        """Stage all files (git add -A)."""
        self._run_git(["add", "-A"])
        logger.debug("Staged all files in repository")

    def create_commit(self, message: str, allow_empty: bool = False) -> str:
        """Create a commit. Returns the commit hash."""
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")

        self._run_git(args)
        commit_hash = self.get_current_commit_hash()
        logger.debug("Created commit: {} ({})", message, commit_hash)
        return commit_hash


class RemoteWritableGitRepo(RemoteReadOnlyGitRepo, _WritableGitRepoSharedMethods):
    pass


class DefaultGitRepoService(GitRepoService):
    """Default implementation of GitRepoService using direct git commands in an Environment."""

    _lock_lock: Lock = PrivateAttr(default_factory=Lock)
    _local_lock_by_project_id: dict[ProjectID, Lock] = PrivateAttr(default_factory=dict)
    _agent_lock_by_task_id: dict[TaskID, Lock] = PrivateAttr(default_factory=dict)

    def _get_lock(self, key: T, lock_map: dict[T, Lock]) -> Lock:
        with self._lock_lock:
            if lock_map.get(key) is None:
                lock_map[key] = Lock()
        return lock_map[key]

    def _get_local_project_lock(self, project_id: ProjectID) -> Lock:
        """Get a lock for the local project to ensure thread-safe access."""
        return self._get_lock(project_id, self._local_lock_by_project_id)

    def _get_agent_task_lock(self, task_id: TaskID) -> Lock:
        """Get a lock for the agent repo to ensure thread-safe access."""
        return self._get_lock(task_id, self._agent_lock_by_task_id)

    def _get_repo_path(self, project: Project) -> Path:
        user_git_repo_url = project.user_git_repo_url
        assert user_git_repo_url is not None and user_git_repo_url.startswith("file://"), (
            "Only local git repositories are supported"
        )
        return Path(user_git_repo_url.replace("file://", ""))

    @contextmanager
    def open_local_user_git_repo_for_read(self, project: Project) -> Generator[LocalReadOnlyGitRepo, None, None]:
        with self._get_local_project_lock(project.object_id):
            repo_path = self._get_repo_path(project)
            yield LocalReadOnlyGitRepo(repo_path=repo_path, concurrency_group=self.concurrency_group)

    @contextmanager
    def open_local_user_git_repo_for_write(self, project: Project) -> Generator[LocalWritableGitRepo, None, None]:
        with self._get_local_project_lock(project.object_id):
            repo_path = self._get_repo_path(project)
            yield LocalWritableGitRepo(repo_path=repo_path, concurrency_group=self.concurrency_group)

    @contextmanager
    def open_remote_agent_git_repo_for_read(
        self, task_id: TaskID, environment: Environment
    ) -> Generator[RemoteReadOnlyGitRepo, None, None]:
        with self._get_agent_task_lock(task_id):
            yield RemoteReadOnlyGitRepo(environment=environment)


def _validate_ref_normalcy(git_ref: str):
    """Validates the the ref can be used directly as a parameter, does not include special characters, and does not start with special +"""
    git_ref = git_ref.strip()
    assert git_ref and (":" not in git_ref) and not git_ref.startswith("+")


def _is_git_merge_result_up_to_date(*, stdout: str) -> bool:
    # from man git-merge:
    # > If all named commits are already ancestors of HEAD, git merge will exit early with the message "Already up to date."
    return stdout.strip() == "Already up to date."
