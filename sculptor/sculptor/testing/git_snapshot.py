import functools
import hashlib
from concurrent.futures import as_completed
from pathlib import Path
from typing import Mapping

import attr

from imbue_core.cattrs_serialization import serialize_to_json
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.frozen_utils import empty_mapping
from imbue_core.secrets_utils import get_secret
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.testing.computing_environment import apply_patch_via_git
from sculptor.testing.computing_environment import make_commit
from sculptor.testing.local_git_repo import LocalGitRepo


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class GitRepo:
    git_user_name: str
    git_user_email: str


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class GitRepoSnapshot(GitRepo):
    git_hash: str
    git_branch: str
    git_diff: str | None

    @functools.cached_property
    def reference_hash(self) -> str:
        hash_fn = hashlib.md5()
        hash_fn.update(serialize_to_json(self).encode("UTF-8"))
        return hash_fn.hexdigest()


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class RemoteGitRepoSnapshot(GitRepoSnapshot):
    git_repo_url: str


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class LocalGitRepoSnapshot(GitRepoSnapshot):
    git_repo_path: str
    # the relative paths of any untracked files in the repo
    git_untracked_files: tuple[str, ...] | None


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class GitCommitSnapshot:
    contents_by_path: Mapping[str, str]
    commit_message: str
    # ex: "2023-05-15T14:30:00"
    # used for GIT_AUTHOR_DATE and GIT_COMMITTER_DATE
    commit_time: str


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class FullLocalGitRepo(GitRepo):
    main_history: tuple[GitCommitSnapshot, ...]
    # the relative paths of any currently untracked files in the repo, and their content
    untracked_file_content_by_path: Mapping[str, str] = attr.ib(factory=empty_mapping)
    git_branch: str = "main"
    git_diff: str | None = None


def create_repo_from_snapshot(
    full_repo: FullLocalGitRepo,
    destination_path: Path,
    concurrency_group: ConcurrencyGroup,
) -> LocalGitRepo:
    """Creates an entire repo history locally from scratch. Much faster and more reliable than checking from remote."""
    if not destination_path.exists():
        destination_path.mkdir(parents=True)
    assert destination_path.is_dir(), (
        f"Destination for repo checkout must be a directory. {destination_path} is not a directory."
    )

    if any(destination_path.iterdir()):
        raise Exception(f"Destination for repo creation must be an empty directory. {destination_path} is not empty.")

    # create the empty repo
    new_repo = LocalGitRepo(destination_path)
    new_repo.run_git(("init",))
    new_repo.run_git(("checkout", "-b", "main"))
    if full_repo.git_branch != "main":
        new_repo.run_git(("branch", "-m", "main", full_repo.git_branch))
    new_repo.run_git(("config", "user.name", f"'{full_repo.git_user_name}'"))
    new_repo.run_git(("config", "user.email", f"'{full_repo.git_user_email}'"))

    # put the history in
    for commit in full_repo.main_history:
        _write_files_in_parallel(new_repo, commit.contents_by_path, concurrency_group)
        make_commit(new_repo, commit.commit_message, commit_time=commit.commit_time)

    if full_repo.git_diff:
        # apply any diffs from between git_hash and repo snapshot state of repo being checked out
        apply_patch_via_git(new_repo, full_repo.git_diff, is_error_logged=True)

    if full_repo.untracked_file_content_by_path:
        # make sure the untracked file contents are there
        _write_files_in_parallel(new_repo, full_repo.untracked_file_content_by_path, concurrency_group)

    return new_repo


def checkout_repo_from_snapshot(
    repo_snapshot: LocalGitRepoSnapshot | RemoteGitRepoSnapshot,
    destination_path: Path,
) -> LocalGitRepo:
    """Checks out an existing repo into a directory in the fastest way possible.

    Uses a snapshot of the existing repo to checkout a specific commit and branch, and apply any uncommited changes.
    Will checkout from a remote or local repo depending on the type of snapshot.

    Note, checking out from a remote repo requires that the necessary git permissions, etc are configured.

    See here: https://stackoverflow.com/questions/31278902/how-to-shallow-clone-a-specific-commit-with-depth-1
    """
    assert destination_path.is_dir(), (
        f"Destination for repo checkout must be a directory. {destination_path} is not a directory."
    )
    if not destination_path.exists():
        destination_path.mkdir(parents=True)
    if any(destination_path.iterdir()):
        raise Exception(f"Destination for repo checkout must be an empty directory. {destination_path} is not empty.")

    remote_address: str | None = None
    env = None
    if isinstance(repo_snapshot, RemoteGitRepoSnapshot):
        token = get_secret("GIT_TOKEN")
        assert token is not None, "Must set GIT_TOKEN environment variable to clone git repos"
        env = {"GIT_TOKEN": token}

        repo_url = repo_snapshot.git_repo_url
        assert repo_url.startswith("https://"), "Only https git urls are supported"
        if repo_url.startswith("https://oauth2"):
            raise Exception("Wait no, that doesn't make sense--that will hardcode the oauth token into the DB")
        remote_address = repo_url.replace("https://", f"https://oauth2:{token}@", 1)
    elif isinstance(repo_snapshot, LocalGitRepoSnapshot):
        remote_address = repo_snapshot.git_repo_path
        env = None
    assert remote_address is not None, "Remote address not found"

    new_repo = LocalGitRepo(destination_path)
    new_repo.run_git(("init",))
    new_repo.run_git(("config", "user.name", f"'{repo_snapshot.git_user_name}'"))
    new_repo.run_git(("config", "user.email", f"'{repo_snapshot.git_user_email}'"))
    new_repo.run_git(("remote", "add", "origin", str(remote_address)))
    new_repo.run_command(("git", "fetch", "--depth", "1", "origin", repo_snapshot.git_hash), secrets=env)
    new_repo.run_git(("checkout", "FETCH_HEAD"))

    if repo_snapshot.git_diff:
        # pyre wants this, even though it's implied by the conditional
        assert repo_snapshot.git_diff is not None
        # apply any diffs from between git_hash and repo snapshot state of repo being checked out
        apply_patch_via_git(new_repo, repo_snapshot.git_diff, is_error_logged=True)

    return new_repo


def _write_files_in_parallel(
    repo: LocalGitRepo, content: Mapping[str, str], concurrency_group: ConcurrencyGroup
) -> None:
    def write_file(file_path: str, file_content: str) -> None:
        repo.write_file(file_path, file_content)

    with ObservableThreadPoolExecutor(concurrency_group) as executor:
        futures = [executor.submit(write_file, file_path, file_content) for file_path, file_content in content.items()]
        for future in as_completed(futures):
            future.result()  # This will raise any exceptions that occurred
