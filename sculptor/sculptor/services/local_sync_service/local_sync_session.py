import threading
from pathlib import Path
from time import localtime
from time import strftime
from typing import Any
from typing import TypeVar

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import ShutdownEvent
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.thread_utils import ObservableThread
from imbue_core.thread_utils import log_exception
from sculptor.interfaces.agents.agent import DockerEnvironment
from sculptor.interfaces.agents.agent import LocalSyncSetupStep
from sculptor.interfaces.environments.base import Environment
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.ref_namespace_stasher import checkout_branch_maybe_stashing_as_we_go
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import SlightlySaferObserver
from sculptor.services.local_sync_service._environment_restart_helpers import EnvironmentAliveHealthCheck
from sculptor.services.local_sync_service._environment_restart_helpers import EnvironmentRestartHandler
from sculptor.services.local_sync_service._misc_utils_and_constants import ConcurrencyGroupController
from sculptor.services.local_sync_service._misc_utils_and_constants import join_background_threads_and_log_exceptions
from sculptor.services.local_sync_service._periodic_health_checker import LocalSyncHealthChecker
from sculptor.services.local_sync_service._watchmedo_via_environment import (
    hack_watchmedo_watcher_into_watchdog_event_queue,
)
from sculptor.services.local_sync_service.api import LocalSyncSessionState
from sculptor.services.local_sync_service.api import SyncSessionInfo
from sculptor.services.local_sync_service.errors import ExpectedStartupBlocker
from sculptor.services.local_sync_service.errors import ExpectedSyncStartupError
from sculptor.services.local_sync_service.errors import MutagenSyncError
from sculptor.services.local_sync_service.errors import SyncCleanupError
from sculptor.services.local_sync_service.git_branch_sync import RepoBranchSyncReconciler
from sculptor.services.local_sync_service.local_sync_update_messenger import LocalSyncUpdateMessengerAPI
from sculptor.services.local_sync_service.mutagen_filetree_sync import LocalSyncGitStateGuardian
from sculptor.services.local_sync_service.mutagen_filetree_sync import MutagenSyncSession
from sculptor.services.local_sync_service.mutagen_filetree_sync import MutagenSyncSessionReconciler
from sculptor.services.local_sync_service.mutagen_filetree_sync import create_bidirectional_user_prioritized_sync
from sculptor.services.local_sync_service.mutagen_filetree_sync import overwrite_local_with_remote_once
from sculptor.services.local_sync_service.path_batch_scheduler import DEFAULT_LOCAL_SYNC_DEBOUNCE_SECONDS
from sculptor.services.local_sync_service.path_batch_scheduler import DEFAULT_LOCAL_SYNC_MAX_DEBOUNCE_SECONDS
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncPathBatchScheduler
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncPathBatchSchedulerStatus
from sculptor.services.local_sync_service.path_batch_scheduler import register_batch_scheduler_with_observer
from sculptor.utils.shared_exclusive_lock import SharedExclusiveLock
from sculptor.utils.timeout import log_runtime

ExceptionT = TypeVar("ExceptionT", bound=Exception)


def _validate_branches_are_safely_syncable(
    syncer: "RepoBranchSyncReconciler", info: SyncSessionInfo, is_stashing_ok: bool
) -> None:
    """Only run on first startup of a sync session, not on restarts.

    Raises an ExpectedSyncStartupError if:
      - agent branch has gone missing
      - the agent's repo is not in the correct agent branch
      - user is ahead of agent branch, because then their changes would get clobbered
      - branches are divergent
      - user local checkout is dirty in any way

    Composits the error messages if multiple are true so user doesn't have to do as many round-trips.
    """
    branch_name = syncer.branch_name
    does_agent_branch_exist = syncer.does_agent_branch_exist()
    if does_agent_branch_exist:
        # doesn't have message/blockers as it failing is undefined behavior (maybe git corruption)
        syncer.ensure_branch_is_mirrored_locally_or_fail()

    does_agent_branch_exist = syncer.does_agent_branch_exist()
    if does_agent_branch_exist:
        # NOTE: this is _kinda validation but failure is unexpected and essentially undefined.
        # Just wanted it out of Reconciler model_post_init b/c restarts might not handle it well
        syncer.ensure_branch_is_mirrored_locally_or_fail()

    if not syncer.is_agent_branch_checked_out():
        if does_agent_branch_exist:
            messages = [f"Agent's repo must be in {branch_name} branch."]
            blockers = [ExpectedStartupBlocker.AGENT_REPO_WRONG_BRANCH]
        else:
            messages = [f"Agent branch {branch_name} not found in agent's repo."]
            blockers = [ExpectedStartupBlocker.AGENT_BRANCH_MISSING]

    elif syncer.is_user_head_equal_to_agent_head() or syncer.is_agent_a_fastforward_ahead_of_user():
        messages = []
        blockers = []

    elif syncer.is_user_a_fastforward_ahead_of_agent():
        messages = [f"Must push to agent: There are local commits to {branch_name} that would be lost."]
        blockers = [ExpectedStartupBlocker.USER_BRANCH_AHEAD_OF_AGENT]

    else:
        # no one is ahead and we aren't equal, must be diverged
        messages = [f"Must merge into agent: local and agent histories have diverged for {branch_name}."]
        blockers = [ExpectedStartupBlocker.BRANCHES_DIVERGED]

    user_status = syncer.user_repo.repo.get_current_status()
    if user_status.is_in_intermediate_state:
        messages.append("Local git state cannot have a merge, rebase, or cherry-pick in progress when starting sync.")
        blockers.append(ExpectedStartupBlocker.USER_GIT_STATE_UNSTASHABLE)

    # similar to is_singleton_stash_slot_available
    can_stash = is_stashing_ok and info.stash is None
    if (not can_stash) and (not user_status.files.are_clean_including_untracked):
        reason = "local git state is dirty or has untracked files"
        if is_stashing_ok:
            if info.is_carried_forward_from_previous_sync:
                message = "Cannot start new sync: Already have a stash and git state changed since prior sync."
            else:
                message = f"Cannot start new sync (unexpected logic path): Already have a stash and {reason}. "
                message += "Contact support if you see this twice and aren't sure why."
        else:
            message = f"Cannot sync without stashing if {reason}."
        messages.append(message)
        blockers.append(ExpectedStartupBlocker.USER_GIT_STATE_STASHING_PREVENTED)

    if len(blockers) == 0:
        return

    if (
        ExpectedStartupBlocker.USER_GIT_STATE_UNSTASHABLE in blockers
        or ExpectedStartupBlocker.USER_GIT_STATE_STASHING_PREVENTED in blockers
    ):
        messages.append(f"Current status:\n{user_status.describe()}")

    message = "Cannot start Pairing Mode: " + " Also: ".join(messages)
    raise ExpectedSyncStartupError(message, blockers, task_branch=branch_name)


class LocalSyncCommonInputs(MutableModel):
    """Inputs common to LocalSyncSession.build_and_start & StrandBundle.build across restarts"""

    agent_environment: Environment
    session_info: SyncSessionInfo
    user_repo_path: Path
    messenger: LocalSyncUpdateMessengerAPI

    debounce_seconds: float = DEFAULT_LOCAL_SYNC_DEBOUNCE_SECONDS
    max_debounce_seconds: float = DEFAULT_LOCAL_SYNC_MAX_DEBOUNCE_SECONDS

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)
        self.user_repo_path = self.user_repo_path.resolve(strict=True)

    @property
    def snapshot_guard(self) -> SharedExclusiveLock | None:
        if isinstance(self.agent_environment, DockerEnvironment):
            return self.agent_environment.get_snapshot_guard()
        return None

    @property
    def remote_mutagen_url(self) -> str:
        return self.agent_environment.get_repo_url_for_mutagen()


# TODO these make_* helpers are pulled out as called multiple times
# the "smell" here is that RepoBranchSyncReconciler pulls double duty as a LocalAgentBranchBridge or something -
# it has a bunch of handy helpers and state that gets used in _validate_branches_are_safely_syncable
# and to construct the Guardian
def make_branch_sync_reconciler(
    inputs: LocalSyncCommonInputs, concurrency_group: ConcurrencyGroup
) -> RepoBranchSyncReconciler:
    return RepoBranchSyncReconciler.build(
        agent_environment=inputs.agent_environment,
        branch_name=inputs.session_info.sync_branch,
        user_repo=LocalWritableGitRepo(repo_path=inputs.user_repo_path, concurrency_group=concurrency_group),
    )


def make_git_state_guardian(
    git_sync_reconciler: RepoBranchSyncReconciler, concurrency_group: ConcurrencyGroup
) -> LocalSyncGitStateGuardian:
    return LocalSyncGitStateGuardian.build(
        user_repo=git_sync_reconciler.user_repo.repo,
        agent_repo=git_sync_reconciler.agent_repo.repo,
        branch_name=git_sync_reconciler.branch_name,
        concurrency_group=concurrency_group,
    )


class _LocalSyncSessionStrandBundle(MutableModel):
    """All the mucky thread etc that actually bridge into the agent environment, meaning they need to be rebuilt when it crashes

    Bundle of strands (threads and background processes) that consitute an actively connected local sync session.

    The top-level LocalSyncSession represents the user-facing state.
    However, if a container errors or restarts intentionally, all our living state will get disconnected.
    That's what this strand bundle is for.
    """

    session_info: SyncSessionInfo
    concurrency_controller: ConcurrencyGroupController
    observer: SlightlySaferObserver
    watchmedo_over_ssh_thread: ObservableThread
    # debounces events into batches, reports notices for pausing (and nonblocking, ie mutagen conflicts), and handles automatic restarting.
    scheduler: LocalSyncPathBatchScheduler
    mutagen_session: MutagenSyncSession
    fs_healthchecker: LocalSyncHealthChecker

    @property
    def concurrency_group(self) -> ConcurrencyGroup:
        return self.concurrency_controller.concurrency_group

    @property
    def all_background_observable_threads(self) -> tuple[ObservableThread, ...]:
        return (self.watchmedo_over_ssh_thread, *self.fs_healthchecker.background_threads)

    @property
    def all_background_threads(self) -> tuple[threading.Thread, ...]:
        return (self.observer, *self.all_background_observable_threads)

    @classmethod
    def build(
        cls,
        inputs: LocalSyncCommonInputs,
        bundle_concurrency_controller: ConcurrencyGroupController,
        git_sync_reconciler: RepoBranchSyncReconciler,  # TODO: this can just be rebuilt here only used externally for helpers
        is_first_session_start: bool,
    ) -> "_LocalSyncSessionStrandBundle":
        concurrency_group = bundle_concurrency_controller.active_group
        with log_runtime("LOCAL_SYNC.create_bidirectional_user_prioritized_sync"):
            # TODO needs to start with strand controller
            mutagen_session = create_bidirectional_user_prioritized_sync(
                # inputs
                local_path=inputs.user_repo_path,
                remote_mutagen_url=inputs.remote_mutagen_url,
                session_name=inputs.session_info.sync_name,
                snapshot_guard=inputs.snapshot_guard,
                #
                concurrency_group=concurrency_group,
                # TODO: with the new force-flush logic in scheduler, we could skip flushing here entirely,
                # but then unforseen setup or state issues would insta-pause instead of fail.
                is_flush_immediately=is_first_session_start,
            )

        # Not expected to fail - this should mostly be pure class-hierarchy setup.
        # Just want to be very certain we terminate mutagen if anything from here down is borked.
        try:
            observer = SlightlySaferObserver(name="watchdog_observer")
            mutagen_reconciler = MutagenSyncSessionReconciler(
                session=mutagen_session,
                stop_event=observer.stopped_event,
                guardian=make_git_state_guardian(git_sync_reconciler, concurrency_group),
            )
            fs_healthchecker = LocalSyncHealthChecker.build(
                observer.threading_context.stop_event, inputs.agent_environment
            )
            env_healthchecker = EnvironmentAliveHealthCheck(
                environment_concurrency_group=inputs.agent_environment.concurrency_group
            )
            scheduler = LocalSyncPathBatchScheduler(
                threading_context=observer.threading_context,
                subpath_reconcilers=(git_sync_reconciler, mutagen_reconciler),
                healthcheckers=(env_healthchecker, fs_healthchecker),
                lifecycle_callbacks=inputs.messenger,
                debounce_seconds=inputs.debounce_seconds,
                max_debounce_seconds=inputs.max_debounce_seconds,
                environment_interaction_lock=inputs.snapshot_guard,
            )
            register_batch_scheduler_with_observer(observer, scheduler)
            # needs to be registered after because we're piggie-backing on the event emitter
            watchmedo_over_ssh_thread = hack_watchmedo_watcher_into_watchdog_event_queue(
                observer=observer,
                agent_environment=inputs.agent_environment,
                environment_dirs_to_watch=scheduler.top_level_environment_dirs_to_register,
            )
            return cls(
                session_info=inputs.session_info,
                concurrency_controller=bundle_concurrency_controller,
                observer=observer,
                watchmedo_over_ssh_thread=watchmedo_over_ssh_thread,
                scheduler=scheduler,
                mutagen_session=mutagen_session,
                fs_healthchecker=fs_healthchecker,
            )
        except Exception:
            mutagen_session.terminate(is_skipped_if_uncreated=True)
            raise

    def start(self) -> None:
        # Now we start everything, and have references for our except handling
        si = self.session_info
        bundle_label = self.concurrency_controller.name
        started = []
        try:
            self.observer.start()
            started.append(self.observer)
            for thread in self.all_background_observable_threads:
                # we don't want the cg to get softlocked due to any thread failures, thus is_checked=False
                self.concurrency_group.start_thread(thread, is_checked=False)
                started.append(thread)
            logger.info("[{}] started sync for task {}, branch {}", bundle_label, si.task_id, si.sync_branch)
        except Exception as e:
            # TODO: consider sending an error message here and having /enable kick-off enable sequence without blocking for completion
            log_exception(e, "local_sync_session: attempting mutagen cleanup after failed start. {si}", i=si)
            # TODO: I don't remember why this is not just .attempt_strand_cleanup() ?
            self.mutagen_session.terminate(is_skipped_if_uncreated=True)
            self.concurrency_controller.stop()
            join_background_threads_and_log_exceptions(started, join_timeout=5)
            raise

    def attempt_strand_cleanup(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        bundle_name = self.concurrency_controller.name
        logger.trace("{}: ensuring observer is stopped and joined.", bundle_name)

        self.observer.stopped_event.set()
        self.mutagen_session.terminate()

        self.concurrency_controller.stop()
        self.observer.ensure_stopped(source=f"bundle({bundle_name}).attempt_strand_cleanup")
        join_background_threads_and_log_exceptions(reversed(self.all_background_threads), join_timeout=10)

        threads = self.all_background_threads
        exited = tuple(t.name for t in threads if not t.is_alive())
        failed = tuple(t.name for t in threads if t.is_alive())
        logger.trace("local sync session joined threads: exited={} failed={}", exited, failed)
        return exited, failed

    def validated_strand_cleanup(self) -> None:
        exited, failed = self.attempt_strand_cleanup()
        if len(failed) > 0:
            message = f"background threads encountered errors during run or did not stop cleanly {exited=}, {failed=}!"
            logger.error(message)
            raise SyncCleanupError(message, task_id=self.session_info.task_id, cleanup_step="observer_cleanup")

    @property
    def is_fully_stopped(self) -> bool:
        if self.concurrency_group.shutdown_event.is_set():
            # in our logic this always happens first, but if somehow we're asking this and it isn't we'll just fix it here
            self.observer.stopped_event.set()
            return True
        return False

    # FIXME: Scrutinize logic for container entanglement, make crash handling
    def stop_from_ostensibly_healthy_state(self) -> LocalSyncPathBatchSchedulerStatus:
        """returns last observed scheduler status"""
        # We want this so children (ie mutagent reconciler) will know not to undo any shutdown hard-kills,
        # but we can't always get the watchdog observer to stop cleanly without hard-killing the mutagen session first if necessary.
        # idk why exactly, the watchdog internals are kinda hairball-y.
        #
        # TODO: am bypassing the lifecycle system as seemed to be messing with stuff more
        self.observer.stopped_event.set()

        # This waits for the scheduler lock, ensuring any pending batch has been flushed before we go killing mutagen.
        #
        # We really want mutagen to flush cleanly, but the user could be intentionally trying to kill a bloated/off-the-rails sync session,
        # ie syncing a my_big_data/ dir.
        #
        # So, we have to balance these possibilities for now until we can inspect the mutagen state more precisely
        timeout = 15
        with log_runtime("LOCAL_SYNC.LocalSyncSession.stop.wait_for_final_batch_for_graceful_shutdown"):
            is_fully_flushed = self.scheduler.wait_for_final_batch_for_graceful_shutdown(timeout=timeout)

        flush_error = None
        if not is_fully_flushed:
            message = (
                f"Terminating mutagen in sync teardown after wait_for_final_batch_for_graceful_shutdown timeout of {timeout}s.",
                "This means the final batch of changes may not have fully flushed to the agent,",
                "though it was likely in a bad state or syncing something suspiciously large regardless.",
            )
            # TODO raise to user?
            flush_error = SyncCleanupError(
                " ".join(message), task_id=self.session_info.task_id, cleanup_step="wait_for_final_batch"
            )
            log_exception(flush_error, ExceptionPriority.MEDIUM_PRIORITY)
        elif not self.scheduler.status.is_paused:
            # Do one last mutagen flush because unsynced local changes could be lost irrecoverably.
            #
            # TODO: Here we're being extra careful and checking that no issues have arisen since the final batch.
            # But that might be more time than we care to spend here, given we've either:
            # 1. Just reconciled the last batch and so checked for pause seconds ago
            # 2. Never scheduled a batch, in which case a syncable file change would have had to happen in the last few seconds.
            # Which is all to say that maybe this method call + 2x check is overkill
            with log_runtime("LOCAL_SYNC.LocalSyncSession.stop.refresh_notices_by_tag"):
                self.scheduler.refresh_notices_by_tag()
            if not self.scheduler.status.is_paused:
                try:
                    self.mutagen_session.flush()
                except MutagenSyncError as e:
                    flush_error = e
                    log_exception(
                        flush_error,
                        "LOCAL_SYNC: final mutagen flush error from unpaused state. Continuing termination and will reraise if no other errors are encountered",
                        ExceptionPriority.MEDIUM_PRIORITY,
                    )
        final_status = self.scheduler.status

        self.validated_strand_cleanup()

        if flush_error is not None:
            raise flush_error

        logger.info("LOCAL_SYNC: Session stopped cleanly ({}), final_status={}", self.session_info, final_status)
        return final_status


class LocalSyncSession(MutableModel):
    """Container for all event messaging, threads (watchdog), sidecare daemons (mutagen) involved in synchronization.

    DOES NOT handle the handling of git and untracked files at the beginning or end of a sync

    Handles constructing the underlying watchers and registering them with the observer,
    while retaining reference to the underlying reconciler (our scheduler) for extracting and handling pause state notices.

    All Reconcilers do initial verification and first sync on build.

    NOTE:
    * This is getting a bit tangled, and should probably be refactored later esp if we migrate to watchman (as we probably should)
    * sculptor/docs/proposals/local_sync_lifecycle.md refers to NoSync, ActiveSync, PausedSync, which is repesented in HighLevelStatus.
    * implemention-wise, the observer STARTS and STOPs, while the LocalSyncPathBatchScheduler PAUSES.
    """

    inputs: LocalSyncCommonInputs
    cg: "ConcurrencyGroupController"

    restart_handler: EnvironmentRestartHandler
    restart_count: int = 0

    # set based on restart_handler in .start()
    _strand_bundle: _LocalSyncSessionStrandBundle = PrivateAttr()
    _restart_on_new_environment_thread: ObservableThread = PrivateAttr()
    _stop_restarting_event: ShutdownEvent = PrivateAttr()

    @property
    def session_info(self) -> SyncSessionInfo:
        return self._strand_bundle.session_info

    @property
    def state(self) -> LocalSyncSessionState | None:
        return LocalSyncSessionState.build_if_sensible(
            info=self._strand_bundle.session_info,
            observer=self._strand_bundle.observer,
            last_sent_message=self.inputs.messenger.last_sent_message,
            scheduler_status=self._strand_bundle.scheduler.status,
        )

    @staticmethod
    def _initial_validation_and_sync_operations(
        inputs: LocalSyncCommonInputs,
        concurrency_group: ConcurrencyGroup,
        is_stashing_ok: bool,
    ) -> RepoBranchSyncReconciler:
        """
        Initial operations that establish sync state:
        1. Setup git reconciler which attempts to validate some ref files
        2. _validate_branches_are_safely_syncable
        3. _sync_agent_to_user_and_checkout_branch
        4. overwrite_local_with_remote_once
        5. create_bidirectional_user_prioritized_sync
        """
        messenger = inputs.messenger
        session_info = inputs.session_info
        branch_name = session_info.sync_branch
        git_sync_reconciler = make_branch_sync_reconciler(inputs, concurrency_group)
        messenger.on_setup_update(next_step=LocalSyncSetupStep.VALIDATE_GIT_STATE_SAFETY)
        with log_runtime("LOCAL_SYNC._validate_branches_are_safely_syncable"):
            _validate_branches_are_safely_syncable(git_sync_reconciler, session_info, is_stashing_ok=is_stashing_ok)

        messenger.on_setup_update(next_step=LocalSyncSetupStep.MIRROR_AGENT_INTO_LOCAL_REPO)

        # One-way git fast-forward from agent to user
        user_sync_repo_helper = git_sync_reconciler.user_repo
        user_sync_repo_helper.fetch_and_reset_mixed_on_branch(from_remote_repo=git_sync_reconciler.agent_repo.repo)

        # Checkout branch, stashing if ok and necessary
        user_repo = user_sync_repo_helper.repo
        is_singleton_stash_slot_available = session_info.stash is None

        # Reasoning as to why we don't check user_status.files.are_clean_including_untracked here is that
        # I wanted the logical code path / sequence of git actions to be invariant when starting a new sync with is_stashing_ok
        #
        # TODO for clarity: also connected to _unsync_from_task logic in that that code has to git reset
        # so that the working tree is clean if there's a stash already
        #
        # There's a kinda odd relationship when switching atm:
        # 1. if there's no stash, then if the state becomes dirty between _unsync_from_task and here, we will create a new stash while switching
        # 2. if there is a stash in the same scenario, we should ho on the is_switching_branches route and maybe fail with a dirty index
        if is_stashing_ok and is_singleton_stash_slot_available:
            with log_runtime("LOCAL_SYNC.checkout_branch_maybe_stashing_as_we_go"):
                # TODO TODO: mutates session_info, not obvious from rest of code
                # NOTE: I considered gating this on a status we could get back from _validate... ,
                # but we already know by now that it is safe WRT our business logic,
                # & git should blow up at us if it has managed to get into an intermediate state in the last half second or w/e
                stash_singleton = checkout_branch_maybe_stashing_as_we_go(
                    session_info.project_id, user_repo, branch_name
                )
                session_info.stash = stash_singleton.stash if stash_singleton else None
        elif session_info.is_switching_branches:
            user_repo.git_checkout_branch(branch_name)

        try:
            with log_runtime("LOCAL_SYNC.overwrite_local_with_remote_once"):
                overwrite_local_with_remote_once(
                    local_path=inputs.user_repo_path,
                    remote_mutagen_url=inputs.remote_mutagen_url,
                    session_name=f"{session_info.sync_name}-init",
                    snapshot_guard=inputs.snapshot_guard,
                    concurrency_group=concurrency_group,
                )
        except Exception:
            status = user_repo.get_current_status()
            if status.is_in_intermediate_state:
                logger.error("Skipping cleanup! Entered intermediate state during initial sync: {}", status.describe())
            elif status.files.are_clean_including_untracked:
                logger.info("cleaning via git reset after failed/partial initial mutagen sync: {}", status.describe())
                user_repo.reset_working_directory()
            raise

        # NOTE; see make_* note also but this was only made here for the helpers and could be thrown away
        return git_sync_reconciler

    @classmethod
    def build_and_start(
        cls,
        inputs: LocalSyncCommonInputs,
        restart_handler: EnvironmentRestartHandler,
        concurrency_group: ConcurrencyGroup,
        is_stashing_ok: bool,
    ) -> "LocalSyncSession":
        """
        Builds and starts a LocalSyncSession, including starting all background threads and mutagen sync.

        We use a single ConcurrencyGroup child of the DefaultLocalSyncService concurrency_group here.
        This means we _should_ be attempting to treat environment failures as recoverable,
        but the code is still likely brittle to unhandled container restarts in any sub-threads/processes that access the environment directly.

        This includes logic in _watchmedo_via_environment and the similar _pipe_healthcheck_signals_from_environment_into_sink in _periodic_health_checker,
        which will probably both error out with broken pipes on a restart.
        """
        cg = ConcurrencyGroupController(concurrency_group=concurrency_group)
        with cg.start_but_close_on_failure():
            git_sync_reconciler = cls._initial_validation_and_sync_operations(
                inputs, concurrency_group, is_stashing_ok=is_stashing_ok
            )

            inputs.messenger.on_setup_update(next_step=LocalSyncSetupStep.BEGIN_TWO_WAY_CONTROLLED_SYNC)
            strand_concurrency_controller = cg.make_controlled_child(name=_label_bundle(0))
            with strand_concurrency_controller.start_but_close_on_failure():
                strands = _LocalSyncSessionStrandBundle.build(
                    inputs,
                    strand_concurrency_controller,
                    git_sync_reconciler,
                    is_first_session_start=True,
                )
            session = cls(inputs=inputs, restart_handler=restart_handler, cg=cg)
            session.start(strands)
            session.inputs.messenger.on_setup_complete()
        return session

    def start(self, first_strand_bundle: _LocalSyncSessionStrandBundle) -> None:
        self._strand_bundle = first_strand_bundle
        self._strand_bundle.start()
        self._stop_restarting_event = ShutdownEvent.from_parent(self.cg.concurrency_group.shutdown_event)
        self._restart_on_new_environment_thread = self.restart_handler.create_background_thread(
            session_level_shutdown_event=self._stop_restarting_event,
            on_new_environment=self._restart_strand_bundle,
        )
        self._restart_on_new_environment_thread.start()

    def _restart_strand_bundle(self, new_environment: Environment) -> None:
        """Starts or restarts the strand bundle, replacing the prior one.

        Used on initial start and on restarts after environment crashes.
        """
        if self._stop_restarting_event.is_set():
            logger.info("LOCAL_SYNC: not restarting strand bundle as shutdown event is set.")
            return
        old_label = self._strand_bundle.concurrency_controller.name
        try:
            assert self._strand_bundle.scheduler.is_current_state_fatal, (
                "Odd: received local sync restart request when prior sync state isn't obviously fatal"
            )
        except AssertionError as ae:
            log_exception(ae, "LOCAL_SYNC: proceeding with restart anyway old_bundle={ob}", ob=old_label)

        exited, failed = self._strand_bundle.attempt_strand_cleanup()
        if len(failed) > 0:
            logger.error("LOCAL_SYNC Restart: attempt_strand_cleanup failed={} exited={}", old_label, failed, exited)

        self.restart_count += 1
        self.inputs.agent_environment = new_environment
        bundle_name = _label_bundle(self.restart_count)
        logger.info("LOCAL_SYNC Restart bundle #{}: {} -> {}", self.restart_count, old_label, bundle_name)

        bundle_concurrency_controller = self.cg.make_controlled_child(name=bundle_name)
        with (
            self.cg.close_on_failure(),
            bundle_concurrency_controller.start_but_close_on_failure(),
        ):
            self._strand_bundle = _LocalSyncSessionStrandBundle.build(
                self.inputs,
                bundle_concurrency_controller,
                make_branch_sync_reconciler(self.inputs, bundle_concurrency_controller.active_group),
                is_first_session_start=False,
            )
            self._strand_bundle.start()

    @property
    def is_fully_stopped(self) -> bool:
        return self._strand_bundle.is_fully_stopped

    def stop(self) -> LocalSyncPathBatchSchedulerStatus:
        """returns last observed scheduler status"""
        self._stop_restarting_event.set()

        # attempt to make softlocks impossible (well, we do our best at least)
        if self._strand_bundle.concurrency_group.shutdown_event.is_set():
            status = self._strand_bundle.scheduler.status
            self._strand_bundle.validated_strand_cleanup()
        else:
            status = self._strand_bundle.stop_from_ostensibly_healthy_state()

        try:
            self._restart_on_new_environment_thread.join(5.0)
        except Exception as e:
            msg = (
                "LOCAL_SYNC: exception while joining restart thread during shutdown.",
                "IDK what to do here but think it is at most inert garbage",
            )
            log_exception(e, " ".join(msg), ExceptionPriority.MEDIUM_PRIORITY)
        self.cg.stop()
        return status

    # TODO(mjr): scrutinize the new state model and error states for failure cases,
    # considering unexpected external failure states via diagram &| test mocking


def _label_bundle(restart_count: int) -> str:
    return f"local_sync_strand_bundle_r{restart_count}_{strftime('%Y-%m-%d_%H%M_%s', localtime())}"
