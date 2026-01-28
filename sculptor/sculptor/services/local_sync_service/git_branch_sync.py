import uuid
from abc import abstractmethod
from functools import cached_property
from pathlib import Path
from typing import Any
from typing import Final
from typing import Generic
from typing import TypeVar
from typing import assert_never

from loguru import logger
from pydantic import AnyUrl
from pydantic import PrivateAttr

from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion
from sculptor.interfaces.environments.base import Environment
from sculptor.primitives.service import MutableModel
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.default_implementation import RemoteReadOnlyGitRepo
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.git_repo_service.git_repos import ReadOnlyGitRepo
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.services.local_sync_service._misc_utils_and_constants import is_pause_necessary
from sculptor.services.local_sync_service.errors import NewNoticesInSyncHandlingError
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncBatchReconciler

RepoT = TypeVar("RepoT", bound=LocalWritableGitRepo | RemoteReadOnlyGitRepo)

LOCAL_GIT_SYNC_TAG: Final = "local_git_sync"

_REPORT_TO_SENTRY_AFTER_MAX_EVENTS_SINCE_LAST_CHANGE: Final = 100_000


def unwrap_url_path(url: AnyUrl) -> Path:
    if url.scheme != "file":
        raise ValueError(f"Expected a file:// URL, got {url}")
    assert url.path is not None
    return Path(url.path)


class CommandResultDivergenceReconciler(LocalSyncBatchReconciler):
    """base class for GitBranchSyncReconciler to make the file-watching interface more visible

    idea is the command is per-path and idempotent unless the path changes,
    so we want to avoid re-running on a path that didn't change.

    Admittedly this is maybe not that important practically, except for maybe avoiding snapshot locking on local events
    """

    _last_seen_results: dict[Path, str] = PrivateAttr(default_factory=dict)
    _path_causing_latest_divergence: Path | None = PrivateAttr(default=None)
    _events_since_last_change: int = 0
    _is_suspicious_watcher_already_reported: bool = False

    @property
    @abstractmethod
    def exact_paths_to_react_to(self) -> tuple[Path, ...]:
        raise NotImplementedError()

    @abstractmethod
    def _get_command_result_for_path(self, path: Path) -> str:
        raise NotImplementedError()

    # NOTE: Only called in init atm but named refresh_cache with the idea that post-healthcheck failure it wants to be rerun regardless of observed events
    def refresh_cache(self) -> None:
        self._last_seen_results = {
            path: self._get_command_result_for_path(path) for path in self.exact_paths_to_react_to
        }

    def _track_events_and_report_if_watcher_suspicious(self):
        self._events_since_last_change += 1
        if (
            self._is_suspicious_watcher_already_reported
            or _REPORT_TO_SENTRY_AFTER_MAX_EVENTS_SINCE_LAST_CHANGE is None
            or self._events_since_last_change < _REPORT_TO_SENTRY_AFTER_MAX_EVENTS_SINCE_LAST_CHANGE
        ):
            return
        message = f"SUSPICIOUS_LOCAL_SYNC_STATE: Too many git sync file events! {self._events_since_last_change=} > {_REPORT_TO_SENTRY_AFTER_MAX_EVENTS_SINCE_LAST_CHANGE=}"
        logger.error(message)
        self._is_suspicious_watcher_already_reported = True

    @property
    def is_divergence_cached(self) -> bool:
        unique_contents = set(self._last_seen_results.values())
        return len(unique_contents) > 1

    def _reconcile_divergence_state(self, path: Path) -> bool:
        is_already_divergent = self.is_divergence_cached
        self._last_seen_results[path] = self._get_command_result_for_path(path)
        is_now_divergent = self.is_divergence_cached
        if is_now_divergent and not is_already_divergent:
            self._path_causing_latest_divergence = path
        return is_now_divergent

    def is_relevant_subpath(self, path: Path) -> bool:
        if path not in self.exact_paths_to_react_to:
            return False
        is_now_divergent = self._reconcile_divergence_state(path)
        if not is_now_divergent:
            self._track_events_and_report_if_watcher_suspicious()
            logger.debug(
                "Ignoring event: No divergence of paths {} ({} since last change)",
                self.exact_paths_to_react_to,
                self._events_since_last_change,
            )
            return False
        return is_now_divergent


def _push_and_fetch_into_environment_repo_using_temp_branch(
    repo: RemoteReadOnlyGitRepo,
    from_user_repo: WritableGitRepo,
    head_ref: str,
    is_dangerously_updating_head: bool,
) -> None:
    tmp_branch = str(uuid.uuid4())
    # Extract branch name from head_ref since push_into_environment_repo expects branch names, not full refs
    branch_name = head_ref.removeprefix("refs/heads/")
    repo.environment.push_into_environment_repo(from_user_repo, branch_name, tmp_branch)
    cmd = ["fetch", "--show-forced-updates"]
    if is_dangerously_updating_head:
        cmd.append("--update-head-ok")
    cmd.extend([".", f"{tmp_branch}:{head_ref}"])
    repo._run_git(cmd)
    repo._run_git(["branch", "-D", tmp_branch])


class _BranchSyncRepo(MutableModel, Generic[RepoT]):
    """A git repository wrapper with convenience utilities for syncing.

    Note that while we wrap "read-only" repos, we do apply modifications
    """

    repo: RepoT
    branch_name: str

    @property
    def url(self) -> AnyUrl:
        return self.repo.get_repo_url()

    def _run_git(self, args: list[str]) -> str:
        return self.repo._run_git(args)

    # just referred a lot in testing
    def get_current_commit_hash(self) -> str:
        return self.repo.get_current_commit_hash()

    @property
    def head_ref(self) -> str:
        # NOTE: NO ADDING "+" HERE
        return f"refs/heads/{self.branch_name}"

    @property
    def _head_refs_relpath(self) -> Path:
        return Path("refs/heads" if self.repo.is_bare_repo else ".git/refs/heads")

    @property
    def head_refs_dir(self) -> Path:
        """Get the path to the internal git refs directory."""
        match self.repo:
            case RemoteReadOnlyGitRepo():
                return Path(self.repo.get_internal_environment_path_str(self._head_refs_relpath))
            case LocalWritableGitRepo():
                return self.repo.repo_path / self._head_refs_relpath
            case _ as unreachable:
                assert_never(unreachable)  # pyre-ignore[6]: pyre doesn't understand the TypeVar

    @cached_property
    def head_ref_pointer_ephemeral_abspath(self) -> Path:
        """Path to the ref pointer file to watch

        This file will _always_ get updated on an update to it's branch,
        even if it is immediately packed into .git/packed-refs.

        Discussion: https://imbue-ai.slack.com/archives/C09EJ979E6Q/p1762475756225719
        """
        head_ref_pointer_relpath = self._head_refs_relpath / self.branch_name
        match self.repo:
            case RemoteReadOnlyGitRepo():
                return Path(self.repo.get_internal_environment_path_str(head_ref_pointer_relpath))
            case LocalWritableGitRepo():
                return self.repo.repo_path / head_ref_pointer_relpath
            case _ as unreachable:
                assert_never(unreachable)  # pyre-ignore[6]: pyre doesn't understand the TypeVar

    def get_branch_head_commit(self) -> str:
        return self.repo.get_branch_head_commit_hash(branch_name=self.branch_name)

    def is_this_branch_child_of(self, commit: str) -> bool:
        try:
            self._run_git(["merge-base", "--is-ancestor", commit, self.head_ref])
            return True
        except GitRepoError:
            return False

    # TODO reconcile with fetch_branch???
    def get_commits_into_wrapped_repo_branch(
        self, from_remote_repo: ReadOnlyGitRepo | WritableGitRepo, is_dangerously_updating_head: bool = False
    ) -> None:
        repo = self.repo
        if isinstance(repo, LocalWritableGitRepo):
            assert isinstance(from_remote_repo, RemoteReadOnlyGitRepo), "User repo should fetch from agent repo"
            repo.fetch_remote_branch_into_local(
                local_branch=self.branch_name,
                remote=from_remote_repo.get_repo_url(),
                remote_branch=self.branch_name,
                dangerously_update_head_ok=is_dangerously_updating_head,
            )
            return
        assert isinstance(from_remote_repo, LocalWritableGitRepo), "Agent repo should fetch from user repo"
        assert isinstance(repo, RemoteReadOnlyGitRepo)  # for the type checker
        _push_and_fetch_into_environment_repo_using_temp_branch(
            repo, from_remote_repo, self.head_ref, is_dangerously_updating_head
        )

    def fetch_and_reset_mixed_on_branch(self, from_remote_repo: ReadOnlyGitRepo | WritableGitRepo) -> None:
        """Fetch from remote and reset to match remote state."""
        head_before_fetch = self.get_branch_head_commit()

        is_sync_branch_checked_out = self.repo.get_current_git_branch() == self.branch_name
        if not is_sync_branch_checked_out:
            # TODO consider aborting here - should be harmless but also not valuable
            logger.debug(
                "git_branch_sync: repo {} changed branches from {}, just fetching",
                self.url,
                self.branch_name,
            )

        self.get_commits_into_wrapped_repo_branch(
            from_remote_repo=from_remote_repo, is_dangerously_updating_head=is_sync_branch_checked_out
        )

        if not is_sync_branch_checked_out:
            return

        is_already_up_to_date_thus_no_reason_to_reset = head_before_fetch == self.get_branch_head_commit()
        if is_already_up_to_date_thus_no_reason_to_reset:
            logger.debug("No change in head after fetch from remote: {}", from_remote_repo.get_repo_url())
            return

        if self.repo.get_current_git_branch() != self.branch_name:
            logger.debug(
                "git_branch_sync: {} != {}, not resetting", self.repo.get_current_git_branch(), self.branch_name
            )
            return

        logger.debug(
            "Change in head after fetch from remote: {}, running reset --mixed", from_remote_repo.get_repo_url()
        )

        # Reset to match the fetched state (mixed reset keeps working directory changes)
        # TODO: We could actually be more granular in our reset and only reset files that changed in the synced commit(s)
        self._run_git(["reset", "--mixed", self.head_ref])

        logger.debug(
            "Successfully fetched and reset from remote {} into {}", from_remote_repo.get_repo_url(), self.url
        )


_AnySyncRepo = _BranchSyncRepo[LocalWritableGitRepo] | _BranchSyncRepo[RemoteReadOnlyGitRepo]


class RepoBranchSyncReconciler(CommandResultDivergenceReconciler):
    """Synchronizes git branch states between user and agent repositories.

    Will sync and validate branch consistency on initialization.
    """

    branch_name: str
    user_repo: _BranchSyncRepo[LocalWritableGitRepo]
    agent_repo: _BranchSyncRepo[RemoteReadOnlyGitRepo]
    tag: str = LOCAL_GIT_SYNC_TAG

    def model_post_init(self, context: Any) -> None:
        # TODO: need to or should handle exception here?
        self.refresh_cache()

    @classmethod
    def build(
        cls, branch_name: str, user_repo: LocalWritableGitRepo, agent_environment: Environment[Any]
    ) -> "RepoBranchSyncReconciler":
        return cls(
            branch_name=branch_name,
            user_repo=_BranchSyncRepo(
                repo=user_repo,
                branch_name=branch_name,
            ),
            agent_repo=_BranchSyncRepo(
                repo=RemoteReadOnlyGitRepo(environment=agent_environment), branch_name=branch_name
            ),
        )

    def _get_command_result_for_path(self, path: Path) -> str:
        match path:
            case self.user_repo.head_ref_pointer_ephemeral_abspath:
                return self.user_repo.get_branch_head_commit()
            case self.agent_repo.head_ref_pointer_ephemeral_abspath:
                return self.agent_repo.get_branch_head_commit()
            case _:
                # TODO: Consider making this a Known Notice error and/or treating as enum
                # Also, it's fragile cause maybe we eventually watch for packed-refs
                # Also, it's fragile cause it hinges on the intersection of multiple class behaviors working as expected in relation to one another
                # As Maciek said elsewhere: we convert a path that we've set back to a repo object that we control...
                # The coupling with exact_paths_to_react_to always matching those values is convoluted,
                # both over time and over code, and that exception [could blow up at runtime surprisingly even though it's more a high-level programming assertion]
                raise ValueError(f"{LOCAL_GIT_SYNC_TAG}: Unexpected {path=} (should be impossible)")

    def _get_repos_with_slight_ordering(self) -> tuple[_AnySyncRepo, _AnySyncRepo]:
        """First repo might be more likely to have changed.

        The cached _path_causing_latest_divergence might be present to inform comparison ordering
        """
        agent_ref_path = self.agent_repo.head_ref_pointer_ephemeral_abspath
        if self._path_causing_latest_divergence == agent_ref_path:
            return self.agent_repo, self.user_repo
        return self.user_repo, self.agent_repo

    @property
    def is_currently_easily_syncable(self) -> bool:
        """Check if the user and agent heads are currently easily syncable."""
        if not self.is_user_head_different_from_agent_head():
            return True
        likely_changed_repo, other_repo = self._get_repos_with_slight_ordering()
        if other_repo.is_this_branch_child_of(likely_changed_repo.get_branch_head_commit()):
            return True
        if likely_changed_repo.is_this_branch_child_of(other_repo.get_branch_head_commit()):
            return True
        return False

    def get_notices(self) -> tuple[LocalSyncNoticeUnion, ...]:
        try:
            if self.is_currently_easily_syncable:
                return tuple()
            local_head = self.user_repo.get_branch_head_commit()[:8]
            remote_head = self.agent_repo.get_branch_head_commit()[:8]
        except GitRepoError as e:
            is_in_user_repo = e.repo_url == self.user_repo.url
            repo_label = "user" if is_in_user_repo else "agent"
            reason = f"git repo failure in {repo_label} repo: {e.stderr} (current branch: {e.branch_name})"
            logger.debug("LOCAL_SYNC.{}: {}", self.tag, reason)
            return (LocalSyncNoticeOfPause(source_tag=self.tag, reason=reason),)
        return (
            LocalSyncNoticeOfPause(
                source_tag=self.tag,
                reason=f"local head@{local_head} and agent head@{remote_head} require manual merging",
            ),
        )

    def _describe_sync_process(self) -> str:
        return f"git_local_sync of {self.branch_name}: {self.user_repo.url} <-> {self.agent_repo.url}"

    def fetch_and_reset_mixed_with_reverse_retry(self, to_repo: _AnySyncRepo, from_repo: _AnySyncRepo) -> None:
        try:
            # will succeed if to is behind from
            to_repo.fetch_and_reset_mixed_on_branch(from_remote_repo=from_repo.repo)
        except GitRepoError as e:
            logger.debug(
                "Initial fetch and reset failed from {} - attempting reverse operation just in case",
                from_repo.url,
            )
            logger.trace("Initial fetch and reset failed from {} failed because: {}", from_repo.url, e)
            # will succeed if from is behind to. Otherwise we have a conflict or race or something
            from_repo.fetch_and_reset_mixed_on_branch(from_remote_repo=to_repo.repo)
            logger.debug(
                "Successfully completed reverse fetch and reset from {} to {}",
                to_repo.url,
                from_repo.url,
            )

    def _summarize_hash_states(self) -> str:
        user_commit = self.user_repo.get_branch_head_commit()
        agent_commit = self.agent_repo.get_branch_head_commit()
        return f"user@{user_commit[:8]} agent@{agent_commit[:8]}"

    def does_agent_branch_exist(self) -> bool:
        return self.agent_repo.repo.is_branch_ref(self.agent_repo.branch_name)

    def is_agent_branch_checked_out(self) -> bool:
        return self.agent_repo.repo.get_current_git_branch() == self.agent_repo.branch_name

    def is_user_head_different_from_agent_head(self) -> bool:
        return self.user_repo.get_branch_head_commit() != self.agent_repo.get_branch_head_commit()

    def is_user_head_equal_to_agent_head(self) -> bool:
        return not self.is_user_head_different_from_agent_head

    def is_user_a_fastforward_ahead_of_agent(self) -> bool:
        # children are ahead
        return self.user_repo.is_this_branch_child_of(self.agent_repo.get_branch_head_commit())

    def is_agent_a_fastforward_ahead_of_user(self) -> bool:
        # children are ahead
        return self.agent_repo.is_this_branch_child_of(self.user_repo.get_branch_head_commit())

    def ensure_branch_is_mirrored_locally_or_fail(self) -> None:
        try:
            self.user_repo.repo.ensure_local_branch_has_remote_branch_ref(self.agent_repo.url, self.branch_name)
        except GitRepoError as e:
            message = f"Likely invalid branch: Failed to ensure {self.user_repo.url} had a reference to {self.branch_name} of agent repo {self.agent_repo.url}"
            raise AssertionError(message) from e

    def sync_heads(self, changed_path: Path) -> bool:
        """Synchronize the HEAD states between user, agent repos.
        Returns True if a sync was performed, False if not.

        Because CommandResultDivergenceReconciler.is_path_relevant handles event filtering based on ref divergence,
        we should pretty much always have different heads here.
        """
        summary = self._summarize_hash_states()

        # NOTE: Because we kicked content-difference back to the parent class via is_any_path_divergent,
        # we should never get here with an unfinished commit due to overly-wide change event handling.
        #
        # BUT, I'm keeping this for safety for now
        if not self.is_user_head_different_from_agent_head():
            logger.trace("head commits equal despite change signal in {}, skipping sync ({})", changed_path, summary)
            return False

        match changed_path:
            case self.user_repo.head_ref_pointer_ephemeral_abspath:
                logger.debug("user change triggered sync_heads on {} {}", self.branch_name, summary)
                self.fetch_and_reset_mixed_with_reverse_retry(to_repo=self.agent_repo, from_repo=self.user_repo)

            case self.agent_repo.head_ref_pointer_ephemeral_abspath:
                logger.debug("agent change triggered sync_heads on {} {}", self.branch_name, summary)
                self.fetch_and_reset_mixed_with_reverse_retry(to_repo=self.user_repo, from_repo=self.agent_repo)

            case _:
                raise ValueError(f"{LOCAL_GIT_SYNC_TAG}: Unexpected {changed_path=} (should be impossible)")

        logger.debug("sync_heads complete: {}", self._summarize_hash_states())
        return True

    @property
    def local_dirs_to_watch(self) -> tuple[Path, ...]:
        return (self.user_repo.head_refs_dir,)

    @property
    def environment_dirs_to_watch(self) -> tuple[Path, ...]:
        return (self.agent_repo.head_refs_dir,)

    @property
    def exact_paths_to_react_to(self) -> tuple[Path, ...]:
        return (
            self.user_repo.head_ref_pointer_ephemeral_abspath,
            self.agent_repo.head_ref_pointer_ephemeral_abspath,
        )

    def handle_path_changes(self, relevant_paths: tuple[Path, ...], is_force_flush: bool) -> None:
        try:
            if len(relevant_paths) == 0 and is_force_flush:
                path = self.exact_paths_to_react_to[0]
            elif len(relevant_paths) > 0:
                path = relevant_paths[0]
            else:
                logger.trace(f"{self.tag}: No relevant paths to handle")
                return
            self.sync_heads(changed_path=path)
        except GitRepoError as e:
            notices = self.get_notices()
            if is_pause_necessary(notices):
                raise NewNoticesInSyncHandlingError(notices) from e
            raise e
