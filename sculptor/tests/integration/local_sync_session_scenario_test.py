"""Realistic scenario tests for the entire Local Sync background process.

Most of this is scaffolding and dataclasses for creating test scenarios.
Would recommend skipping to the end to see actual tests as it looks more intimidating than it is.
"""

import contextlib
import json
import os
import re
import tempfile
import threading
import time
from abc import ABC
from abc import abstractmethod
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Generator
from typing import Iterable
from typing import Literal
from typing import cast

import pytest
from _pytest.logging import LogCaptureFixture
from loguru import logger
from pydantic import Field
from pydantic import SkipValidation
from pytest_mock import MockerFixture

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import ShutdownEvent
from imbue_core.processes.local_process import run_blocking
from imbue_core.pydantic_serialization import FrozenDict
from imbue_core.pydantic_serialization import FrozenModel
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.testing_utils import integration_test
from imbue_core.thread_utils import ObservableThread
from sculptor.interfaces.agents.agent import LocalSyncNotice
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.interfaces.agents.agent import LocalSyncNoticeUnion
from sculptor.interfaces.agents.agent import LocalSyncSetupStep
from sculptor.interfaces.agents.agent import LocalSyncUpdateMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdateMessageUnion
from sculptor.interfaces.agents.agent import LocalSyncUpdatePausedMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdatePendingMessage
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.constants import ENVIRONMENT_WORKSPACE_DIRECTORY
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.git_repo_service.default_implementation import LocalWritableGitRepo
from sculptor.services.git_repo_service.default_implementation import RemoteWritableGitRepo
from sculptor.services.git_repo_service.default_implementation import _WritableGitRepoSharedMethods
from sculptor.services.local_sync_service._debounce_and_watchdog_helpers import DebounceController
from sculptor.services.local_sync_service._environment_restart_helpers import EnvironmentRestartHandler
from sculptor.services.local_sync_service._misc_utils_and_constants import is_any_path_under
from sculptor.services.local_sync_service.api import SyncSessionInfo
from sculptor.services.local_sync_service.errors import ExpectedSyncStartupError
from sculptor.services.local_sync_service.git_branch_sync import LOCAL_GIT_SYNC_TAG
from sculptor.services.local_sync_service.git_branch_sync import RepoBranchSyncReconciler
from sculptor.services.local_sync_service.local_sync_session import DEFAULT_LOCAL_SYNC_DEBOUNCE_SECONDS
from sculptor.services.local_sync_service.local_sync_session import LocalSyncCommonInputs
from sculptor.services.local_sync_service.local_sync_session import LocalSyncSession
from sculptor.services.local_sync_service.local_sync_update_messenger import LocalSyncUpdateMessengerAPI
from sculptor.services.local_sync_service.mutagen_filetree_sync import LOCAL_FILESYNC_TAG
from sculptor.services.local_sync_service.mutagen_filetree_sync import LOCAL_GIT_STATE_GUARDIAN_TAG
from sculptor.services.local_sync_service.mutagen_filetree_sync import MutagenSyncSessionReconciler
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncPathBatchScheduler
from sculptor.services.local_sync_service.path_batch_scheduler_test import LocalSyncPathBatchSchedulerStatus
from sculptor.services.task_service.api import TaskService
from sculptor.tasks.handlers.run_agent.setup import hard_overwrite_full_agent_workspace
from sculptor.testing.local_git_repo import LocalGitRepo

_DEBUGGING_TEST_FOLDER: str | None = os.environ.get("DEBUGGING_TEST_FOLDER", None)
_SKIP_DEBOUNCE_MOCK_FOR_HIGH_REALISM_MANUAL_TESTING: bool = os.environ.get(
    "SKIP_DEBOUNCE_MOCK_FOR_HIGH_REALISM_MANUAL_TESTING", "0"
).lower()[0] in ("1", "t", "y")

# was originally 60sec, added 1.5 min for env creation buffer to be safe
_TEST_TIMEOUT_SECONDS = 2.5 * 60.0


def _mock_new_timer(self: DebounceController, debounce_seconds: float | None = None) -> threading.Timer:
    "separated for easier mocking in scenario testing"
    debounce_seconds = debounce_seconds or self.debounce_seconds
    timer = MockTimer(debounce_seconds, self.callback)
    timer.name = self.name
    return timer


@pytest.fixture(autouse=True)
def mock_debounce_timer(
    mocker: MockerFixture,
) -> Generator[None, None, None]:
    if _SKIP_DEBOUNCE_MOCK_FOR_HIGH_REALISM_MANUAL_TESTING:
        yield
        return
    mocker.patch.object(DebounceController, "_new_timer", _mock_new_timer)
    yield


@contextlib.contextmanager
def noop_context():
    yield


def is_condition_true_within_timeout(
    label: str, condition: Callable[[], bool], timeout_seconds: float, retry_interval_seconds: float = 0.01
) -> bool:
    if timeout_seconds <= 0.0:
        return condition()
    start_time = time.time()
    have_waited_for_seconds = 0.0
    result = condition()
    while (not result) and have_waited_for_seconds < timeout_seconds:
        time.sleep(retry_interval_seconds)
        have_waited_for_seconds = time.time() - start_time
        result = condition()
    if not result:
        logger.debug(
            "{}=>False, giving up after {}s, (timeout: {:.3f}s)", label, have_waited_for_seconds, timeout_seconds
        )
    return result


class ScenarioWritableGitRepo(_WritableGitRepoSharedMethods, ABC):
    @abstractmethod
    def create_or_append_to_file(self, relative_path: str, contents: str) -> None: ...

    @abstractmethod
    def delete_file(self, relative_path: str) -> None: ...

    @abstractmethod
    def move_file(self, relative_path_src: str, relative_path_dest: str) -> None: ...

    @abstractmethod
    def non_git_files_in_repo(self) -> Iterable[str]: ...


class ScenarioLocalWritableGitRepo(LocalWritableGitRepo, ScenarioWritableGitRepo):
    def create_or_append_to_file(self, relative_path: str, contents: str) -> None:
        abs_path = self.repo_path / relative_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with abs_path.open("a") as file:
            file.write(contents)

    def delete_file(self, relative_path: str) -> None:
        abs_path = self.repo_path / relative_path
        if abs_path.exists():
            abs_path.unlink()

    def move_file(self, relative_path_src: str, relative_path_dest: str) -> None:
        src = self.repo_path / relative_path_src
        dst = self.repo_path / relative_path_dest
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            src.rename(dst)

    def non_git_files_in_repo(self) -> Iterable[str]:
        for file_path in self.repo_path.rglob("*"):
            if file_path.is_file() and ".git" not in str(file_path):
                yield str(file_path.relative_to(self.repo_path))


class ScenarioRemoteWritableGitRepo(RemoteWritableGitRepo, ScenarioWritableGitRepo):
    def create_or_append_to_file(self, relative_path: str, contents: str) -> None:
        self.environment.write_file(str(ENVIRONMENT_WORKSPACE_DIRECTORY / relative_path), contents, mode="a")

    def delete_file(self, relative_path: str) -> None:
        self.environment.run_process_to_completion(["rm", str(ENVIRONMENT_WORKSPACE_DIRECTORY / relative_path)], {})

    def move_file(self, relative_path_src: str, relative_path_dest: str) -> None:
        self.environment.move_file(
            str(ENVIRONMENT_WORKSPACE_DIRECTORY / relative_path_src),
            str(ENVIRONMENT_WORKSPACE_DIRECTORY / relative_path_dest),
        )

    def non_git_files_in_repo(self) -> Iterable[str]:
        file_list_result = self.environment.run_process_to_completion(
            [
                "find",
                str(ENVIRONMENT_WORKSPACE_DIRECTORY),
                "-type",
                "d",
                "-name",
                ".git",
                "-prune",
                "-o",
                "-type",
                "f",
                "-print0",
            ],
            {},
        )
        return (
            str(Path(line).relative_to(ENVIRONMENT_WORKSPACE_DIRECTORY))
            for line in file_list_result.stdout.split("\0")
        )


class MockTimer(threading.Timer):
    """This mock lets us avoid racing in CI.

    When developing it is still good to run without the mock to ensure our IRL expectations of debounce timeouts are accurate
    """

    is_scheduled: bool = False
    flush_tolerance: float = 0.5

    def start(self):
        self.is_scheduled = True

    def flush_and_call(self):
        assert is_condition_true_within_timeout(
            "MockTimer.is_scheduled",
            lambda: self.is_scheduled,
            self.flush_tolerance,
        ), f"MockTimer.flush invalid: was not started within {{flush_tolerance={self.flush_tolerance}}}"
        self.function(*self.args, **self.kwargs)
        self.finished.set()


# Base Operation class
class Operation(FrozenModel):
    def execute(self, repos: dict[str, ScenarioWritableGitRepo]) -> None:
        raise NotImplementedError()


class Create(Operation):
    """Create a new file with content."""

    in_repo: str
    path: str
    content: str = ""

    def execute(self, repos: dict[str, ScenarioWritableGitRepo]) -> None:
        repo = repos[self.in_repo]
        repo.create_or_append_to_file(self.path, self.content)


class Append(Create):
    """Append content to an existing file."""


class Delete(Operation):
    """Delete a file."""

    in_repo: str
    path: str

    def execute(self, repos: dict[str, ScenarioWritableGitRepo]) -> None:
        repo = repos[self.in_repo]
        repo.delete_file(self.path)


class Move(Operation):
    """Move/rename a file."""

    in_repo: str
    source: str
    destination: str

    def execute(self, repos: dict[str, ScenarioWritableGitRepo]) -> None:
        repo = repos[self.in_repo]
        repo.move_file(self.source, self.destination)


class WhiteboxOperation(Operation):
    def flush_mocked_timer(self, reconcilers: "_Reconcilers") -> bool:
        if _SKIP_DEBOUNCE_MOCK_FOR_HIGH_REALISM_MANUAL_TESTING:
            logger.debug("_SKIP_DEBOUNCE_MOCK_FOR_HIGH_REALISM_MANUAL_TESTING is True: not flushing real timer")
            return False
        is_condition_true_within_timeout(
            "batch_scheduler.debounce._timer is not None",
            lambda: reconcilers.batch_scheduler.debounce._timer is not None,
            timeout_seconds=1.0,
        )
        timer = reconcilers.batch_scheduler.debounce._timer
        assert timer is not None, "should have a timer if flush is expected"
        assert isinstance(timer, MockTimer), (
            f"should be MockTimer here but is {type(timer)} (debounce={reconcilers.batch_scheduler.debounce}"
        )
        timer.flush_and_call()
        return True

    def execute_whitebox(self, repos: dict[str, ScenarioWritableGitRepo], reconcilers: "_Reconcilers") -> None:
        raise NotImplementedError()


class Sleep(WhiteboxOperation):
    sec: float = 0.5
    plus_debounce: bool = False

    def execute_whitebox(self, repos: dict[str, Path], reconcilers: "_Reconcilers") -> None:
        sec = self.sec
        if self.plus_debounce:
            sec += reconcilers.batch_scheduler.debounce.debounce_seconds
        time.sleep(sec)


class FlushMockedTimer(WhiteboxOperation):
    def execute_whitebox(self, repos: dict[str, Path], reconcilers: "_Reconcilers") -> None:
        self.flush_mocked_timer(reconcilers)


class AssertIdle(FlushMockedTimer):
    def execute_whitebox(self, repos: dict[str, Path], reconcilers: "_Reconcilers") -> None:
        status = reconcilers.batch_scheduler.status
        assert status == LocalSyncPathBatchSchedulerStatus.IDLE, f"batch_scheduler should be idle, but {status=}"


# TODO: git_sync so longgggg and flakyyyy whyyyyyy
class WaitForBatchCallbackMessage(WhiteboxOperation):
    padding: float = 5.0
    is_pause_expected: bool = False
    label: str = ""

    def execute_whitebox(self, repos: dict[str, Path], reconcilers: "_Reconcilers") -> None:
        """
        mutagen_sessions = run_blocking(("mutagen", "sync", "list")).stdout
        print("mutagen_sessions")
        print(mutagen_sessions)
        """

        messenger = reconcilers.batch_scheduler._lifecycle_callbacks
        assert isinstance(messenger, SyncUpdateMessageCollector)
        sent_count_at_start = len(messenger.sent_messages)
        debounce = reconcilers.batch_scheduler.debounce
        time_to_wait = self.padding + debounce.debounce_seconds
        self.flush_mocked_timer(reconcilers)
        is_condition_true_within_timeout(
            "batch_sent",
            lambda: len(messenger.sent_messages) > sent_count_at_start,
            timeout_seconds=time_to_wait,
            retry_interval_seconds=0.25,
        )
        assert len(messenger.sent_messages) > sent_count_at_start, (
            f"should have sent at least one batch message within ({time_to_wait} seconds {debounce.total_elapsed_seconds=})"
        )
        message = messenger.sent_messages[-1]
        is_pause_present = isinstance(message, LocalSyncUpdatePausedMessage) and len(message.pause_notices) > 0
        is_pause_expected = self.is_pause_expected
        assert is_pause_present == is_pause_expected, f"{is_pause_expected=} WRT {repr(messenger.sent_messages[-1])}"


class WaitForHeadDivergence(WhiteboxOperation):
    sec: float = 0.5

    def wait_for_head_divergence(self, git_sync: RepoBranchSyncReconciler, timeout_seconds: float) -> bool:
        return is_condition_true_within_timeout(
            label="git_sync.wait_for_head_divergence()",
            condition=lambda: git_sync.is_user_head_different_from_agent_head(),
            timeout_seconds=timeout_seconds,
        )

    def execute_whitebox(self, repos: dict[str, Path], reconcilers: "_Reconcilers") -> None:
        assert self.wait_for_head_divergence(reconcilers.git_sync, timeout_seconds=self.sec), (
            f"repos heads still not diverged after {self.sec}sec"
        )


class WaitForHeadConvergence(WhiteboxOperation):
    sec: float = 0.5

    def wait_for_head_convergence(self, git_sync: RepoBranchSyncReconciler, timeout_seconds: float) -> bool:
        return is_condition_true_within_timeout(
            label="git_sync.wait_for_head_convergence()",
            condition=lambda: not git_sync.is_user_head_different_from_agent_head(),
            timeout_seconds=timeout_seconds,
        )

    def execute_whitebox(self, repos: dict[str, Path], reconcilers: "_Reconcilers") -> None:
        assert self.wait_for_head_convergence(reconcilers.git_sync, timeout_seconds=self.sec), (
            f"repos still not synced after {self.sec}sec"
        )


# TODO: unused?
class ExpectNotices(WhiteboxOperation):
    notices: tuple[LocalSyncNoticeUnion, ...]
    label: str = "expect_notices"
    wait_for_seconds: float = 1.0

    def execute_whitebox(self, repos: dict[str, Path], reconcilers: "_Reconcilers") -> None:
        batch_scheduler = reconcilers.batch_scheduler
        is_exactly_every_notice_seen_in_order_and_no_more = is_condition_true_within_timeout(
            self.label,
            lambda: batch_scheduler._last_seen_notices == self.notices,
            timeout_seconds=self.wait_for_seconds,
            retry_interval_seconds=0.25,
        )
        assert is_exactly_every_notice_seen_in_order_and_no_more, " ".join(
            (
                f"Expected notices {self.label} not found in last_seen_notices while executing {self}.",
                f"Seen reasons: {[i.reason for i in batch_scheduler._last_seen_notices]}",
            )
        )


class GitOperation(Operation):
    in_repo: str

    def execute(self, repos: dict[str, ScenarioWritableGitRepo]) -> None:
        self.execute_git_commands(repos[self.in_repo])

    def execute_git_commands(self, repo: ScenarioWritableGitRepo) -> None:
        raise NotImplementedError()


class ResetHard(GitOperation):
    refspec: str = "HEAD"

    def execute_git_commands(self, repo: ScenarioWritableGitRepo) -> None:
        repo._run_git(["reset", "--hard", self.refspec])


class Commit(GitOperation):
    message: str
    add_all: bool = True
    allow_empty: bool = True

    def execute_git_commands(self, repo: ScenarioWritableGitRepo) -> None:
        if self.add_all:
            repo._run_git(["add", "-A"])
        cmd = ["commit", "-m", self.message]
        if self.allow_empty:
            cmd.append("--allow-empty")
        repo._run_git(cmd)


class Checkout(GitOperation):
    branch: str

    def execute_git_commands(self, repo: ScenarioWritableGitRepo) -> None:
        repo._run_git(["checkout", self.branch])


class CheckCommitLogFor(GitOperation):
    expected_message: str
    should_exist: bool = True

    def execute_git_commands(self, repo: ScenarioWritableGitRepo) -> None:
        log = repo._run_git(["log", "--oneline", "-n", "50"])

        message_found = self.expected_message in log

        if self.should_exist:
            assert message_found, (
                f"Expected commit message '{self.expected_message}' not found in {self.in_repo} repo log. Log:\n{log}"
            )
        else:
            assert not message_found, (
                f"Unexpected commit message '{self.expected_message}' found in {self.in_repo} repo log."
            )


class RepoState(MutableModel):
    """Represents the initial state of a git repository."""

    files: dict[str, str] = FrozenDict({"README.md": "Initial readme"})  # path -> content
    branch: str = "scene-test/sync"
    commits: tuple[tuple[str, dict[str, str]], ...] = ()  # [(msg, {path: content})]


class ExpectedFileTrees(MutableModel):
    """Expected outcome of a scenario."""

    user_filetree: dict[str, str] | None = None
    agent_filetree: dict[str, str] | None = None


class ExpectedOutcome(ExpectedFileTrees):
    """Expected outcome of a scenario."""

    is_filetree_equality_expected: bool = True
    # TODO IDK about this:
    logged_patterns: tuple[str, ...] = ()  # Patterns to find in logs
    message_notice_patterns_in_order: tuple[tuple[LocalSyncNoticeUnion, ...], ...] | None = None
    git_sync_reconciler_calls: int | None = None
    filetree_reconciler_calls: dict[str, int] | None = None  # Repo -> expected call count
    final_state: Literal["STOPPED", "PAUSED", "ACTIVE"] = "ACTIVE"
    batch_remainder_due_to_stop_or_pause: dict[str, set[tuple[Literal["agent"] | Literal["user"], str]]] = Field(
        default_factory=dict
    )  # If observer died, what was left in the buffer

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)
        if not self.is_filetree_equality_expected:
            return
        if self.user_filetree is None or self.agent_filetree is None:
            return
        assert self.user_filetree == self.agent_filetree, (
            "if expecting desync set is_filetree_equality_expected to False"
        )

    def absolutize_batch_remainder(self, under_root: Path) -> dict[str, set[Path]]:
        """Convert relative paths in batch remainder to absolute paths."""
        return {
            tag: {
                (under_root / "user" if location_tag == "user" else ENVIRONMENT_WORKSPACE_DIRECTORY) / path
                for (location_tag, path) in tagged_paths
            }
            for tag, tagged_paths in self.batch_remainder_due_to_stop_or_pause.items()
        }


class ExpectedStartRejection(ExpectedFileTrees):
    # pytest.raises
    rejection: pytest.RaisesExc[BaseException]


class BaseScenario(FrozenModel):
    name: str
    initial_repo_state: RepoState
    operations_before_sync: tuple[Operation, ...] = ()
    debounce_seconds: float = 1.25 or 5 * DEFAULT_LOCAL_SYNC_DEBOUNCE_SECONDS
    sync_branch: str = "scene-test/sync"

    @cached_property
    def session_info(self) -> SyncSessionInfo:
        task_id = TaskID()
        project_id = ProjectID()
        return SyncSessionInfo(
            task_id=task_id,
            project_id=project_id,
            sync_name=f"sculptor-{self.name.replace('_', '-')}",  # mutagen_sync_name_for(project_id, task_id),
            sync_branch=self.sync_branch,
            original_branch="main",
            stash=None,
            # has_stash=False,
            # stash_message=None,
        )


class SyncScenario(BaseScenario):
    """A complete actually-buildable sync scenario specification."""

    operations: tuple[Operation, ...]
    expected_outcome: ExpectedOutcome


class UnstartableSyncScenario(BaseScenario):
    """A sync scenario that will fail to start."""

    expected_outcome: ExpectedStartRejection


class SyncUpdateMessageCollector(LocalSyncUpdateMessengerAPI):
    def __init__(self) -> None:
        self.sent_messages: list[LocalSyncUpdateMessageUnion] = []
        self.setup_steps: list[LocalSyncSetupStep] = []
        self.is_setup_complete_message_sent: bool = False
        self.session_level_shutdown_event: ShutdownEvent = ShutdownEvent.build_root()

    def send_update_message(self, message: LocalSyncUpdateMessageUnion) -> None:
        if isinstance(message, LocalSyncUpdatePendingMessage):
            # FIXME(mjr): After these tests stabilize again from the SSH Sync MR we need to actually test this
            return
        self.sent_messages.append(message)

    def on_setup_update(self, next_step: LocalSyncSetupStep) -> None:
        self.setup_steps.append(next_step)

    def on_setup_complete(self) -> None:
        self.is_setup_complete_message_sent = True

    @property
    def last_sent_message(self) -> LocalSyncUpdateMessage | None:
        return None


class MockEnvironmentRestartHandler(EnvironmentRestartHandler):
    task_id: TaskID = TaskID()
    # TODO: actually rework this module to make mocking less of a pain
    task_service: SkipValidation[TaskService] = cast(TaskService, None)

    def _log(self, message: str) -> None:
        logger.info("LOCAL_SYNC Restart Watcher for task {}: {}", "mock", message)

    def _watch_for_environment_restarts(
        self, session_level_shutdown_event: ShutdownEvent, on_new_environment: Callable[[Environment], None]
    ) -> None:
        """
        Watch for environment messages from which to trigger Session rebuilds via on_new_environment
        """
        self._log("Starting to pretend")
        session_level_shutdown_event.wait()
        self._log("Exiting pretend land")

    def create_background_thread(
        self, session_level_shutdown_event: ShutdownEvent, on_new_environment: Callable[[Environment], None]
    ) -> ObservableThread:
        args = (session_level_shutdown_event, on_new_environment)
        return ObservableThread(
            target=self._watch_for_environment_restarts, name="EnvironmentRestartHandler-mock", args=args
        )


class _Reconcilers:
    def __init__(
        self,
        filetree: MutagenSyncSessionReconciler,
        git_sync: RepoBranchSyncReconciler,
        batch_scheduler: LocalSyncPathBatchScheduler,
        mocker: MockerFixture,
    ) -> None:
        self.filetree = filetree
        self.git_sync = git_sync
        self.batch_scheduler = batch_scheduler

        for reconciler_to_hack_for_patching in (filetree, git_sync):
            reconciler_to_hack_for_patching.model_config["extra"] = "allow"

        self.filetree_spy = mocker.spy(self.filetree, "handle_path_changes")
        self.git_sync_spy = mocker.spy(self.git_sync, "handle_path_changes")

    @property
    def filetree_reconciler_calls(self) -> dict[str, int]:
        user, agent = self.filetree.root_paths
        return {
            "user": self._sum_reconciler_calls_including_root(user),
            "agent": self._sum_reconciler_calls_including_root(agent),
        }

    def _sum_reconciler_calls_including_root(self, root: Path) -> int:
        batches = (call.args[0] for call in self.filetree_spy.call_args_list)
        return sum(1 if is_any_path_under(batch, root) else 0 for batch in batches)


class FSEndState(MutableModel):
    base_path: Path
    user_repo_state: dict[str, str]
    agent_repo_state: dict[str, str]


class ScenarioResults(FSEndState):
    reconcilers: _Reconcilers
    sync_logs: list[str]
    observer_stopped: bool = False
    sent_messages: list[LocalSyncUpdateMessageUnion]


def _show_repo_logs(repos: dict[str, ScenarioWritableGitRepo]) -> str:
    return "\n".join(
        (
            "user log:",
            repos["user"]._run_git(["log", "--oneline", "-n", "50"]),
            "agent log:",
            repos["agent"]._run_git(["log", "--oneline", "-n", "50"]),
        )
    )


def dump_potentially_useful_state_and_logs(
    repos: dict[str, ScenarioWritableGitRepo], batch_scheduler: LocalSyncPathBatchScheduler | None
) -> str:
    """Dump useful state and logs for debugging."""
    mutagen_sessions = run_blocking(("mutagen", "sync", "list")).stdout
    return "\n".join(
        (
            "Potentially useful state and logs:",
            _show_repo_logs(repos),
            batch_scheduler.describe_current_state() if batch_scheduler else "No batch scheduler",
            str(batch_scheduler._lifecycle_callbacks) if batch_scheduler else "No batch scheduler",
            "",
            "threads:",
            *(f"{thread.name}.is_alive() is {thread.is_alive()}" for thread in threading.enumerate()),
            "mutagen sessions:",
            mutagen_sessions,
        )
    )


@pytest.fixture
def temp_scenario_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for scenario testing."""
    if _DEBUGGING_TEST_FOLDER is not None:
        yield Path(_DEBUGGING_TEST_FOLDER) / datetime.now().strftime("%Y%m%d_%H%M%S")
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


def create_repo_from_state(
    repo_path: Path, state: RepoState, concurrency_group: ConcurrencyGroup
) -> ScenarioLocalWritableGitRepo:
    """Create a git repository from a RepoState specification."""
    repo_path.mkdir(parents=True, exist_ok=True)

    repo = LocalGitRepo(repo_path)

    for file_path, content in state.files.items():
        file_full_path = repo_path / file_path
        file_full_path.parent.mkdir(parents=True, exist_ok=True)
        file_full_path.write_text(content)

    repo.configure_git()

    # Create branch if different from main
    if state.branch != "main":
        repo.run_git(["checkout", "-b", state.branch])

    return ScenarioLocalWritableGitRepo(repo_path=repo.base_path, concurrency_group=concurrency_group)


def prepare_scenario(
    scenario: SyncScenario | UnstartableSyncScenario,
    base_path: Path,
    environment: Environment,
    message_collector: SyncUpdateMessageCollector | None = None,
) -> tuple[dict[str, ScenarioWritableGitRepo], LocalSyncSession | None]:
    """Execute a sync scenario and return results."""
    base_path.mkdir(parents=True, exist_ok=True)
    assert base_path.is_dir(), f"Failed to create scenario base path: {base_path}"
    assert not os.listdir(base_path), f"Scenario base path is not fresh: {base_path=} {os.listdir(base_path)=}"
    user_repo: ScenarioWritableGitRepo = create_repo_from_state(
        base_path / "user", scenario.initial_repo_state, environment.concurrency_group
    )
    hard_overwrite_full_agent_workspace(environment, user_repo.repo_path, blindly_sync_everything=True)
    repo_paths = {"user": user_repo, "agent": ScenarioRemoteWritableGitRepo(environment=environment)}

    for operation in scenario.operations_before_sync:
        try:
            operation.execute(repo_paths)
        except Exception as e:
            log_exception(e, "Pre-sync Operation {operation_repr} failed", operation_repr=repr(operation))
            logger.info(dump_potentially_useful_state_and_logs(repo_paths, None))
            raise

    message_collector = message_collector or SyncUpdateMessageCollector()

    start_context = (
        scenario.expected_outcome.rejection if isinstance(scenario, UnstartableSyncScenario) else noop_context()
    )
    reconciler = None
    with start_context:
        reconciler = LocalSyncSession.build_and_start(
            inputs=LocalSyncCommonInputs(
                session_info=scenario.session_info,
                user_repo_path=user_repo.repo_path,
                messenger=message_collector,
                debounce_seconds=scenario.debounce_seconds,
                agent_environment=environment,
            ),
            restart_handler=MockEnvironmentRestartHandler(),
            concurrency_group=environment.concurrency_group.make_concurrency_group(f"{scenario.name}_cg"),
            is_stashing_ok=False,
        )
    return repo_paths, reconciler


# note: setup started messages are sent in service itself
def verify_setup_messages_are_sent(message_collector: SyncUpdateMessageCollector) -> None:
    assert message_collector.setup_steps == [
        LocalSyncSetupStep.VALIDATE_GIT_STATE_SAFETY,
        LocalSyncSetupStep.MIRROR_AGENT_INTO_LOCAL_REPO,
        LocalSyncSetupStep.BEGIN_TWO_WAY_CONTROLLED_SYNC,
    ]
    assert message_collector.is_setup_complete_message_sent, "should have sent setup complete message"


def execute_scenario(
    scenario: SyncScenario,
    base_path: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    environment: Environment,
) -> ScenarioResults:
    """Execute a sync scenario and return results."""
    message_collector = SyncUpdateMessageCollector()
    base_path = base_path.absolute().resolve() / scenario.name
    repo_paths, reconciler = prepare_scenario(scenario, base_path, environment, message_collector)
    assert reconciler is not None
    verify_setup_messages_are_sent(message_collector)
    batch_scheduler = reconciler._strand_bundle.scheduler
    git_sync, filetree = batch_scheduler._batch_reconciler_by_tag.values()
    assert isinstance(git_sync, RepoBranchSyncReconciler), "git_sync should go first"
    assert isinstance(filetree, MutagenSyncSessionReconciler), "filetree should go second"
    try:
        observer = reconciler._strand_bundle.observer
        assert observer.is_alive(), "Observer did not start successfully"
        reconcilers = _Reconcilers(
            filetree=filetree,
            git_sync=git_sync,
            batch_scheduler=batch_scheduler,
            mocker=mocker,
        )
    except Exception:
        # prevent mutagen garbage floating around while iterating
        # handled normally in reconciler.stop()
        filetree.session.terminate(is_skipped_if_uncreated=True)
        raise

    sync_logs = []
    observer_stopped = False
    try:
        caplog.clear()

        # TODO: consider moving watchmedo start above other slow actions in local sync so it has time to "warm up"
        #  this time.sleep is here to make sure that watchmedo is running before we run these tests.
        time.sleep(1)
        for i, operation in enumerate(scenario.operations):
            try:
                if isinstance(operation, WhiteboxOperation):
                    operation.execute_whitebox(repo_paths, reconcilers)
                    continue
                operation.execute(repo_paths)
            except Exception as e:
                log_exception(e, "operation[{i}] {operation_repr} failed", i=i, operation_repr=repr(operation))
                logger.info(dump_potentially_useful_state_and_logs(repo_paths, batch_scheduler))
                raise

        time.sleep(scenario.debounce_seconds * 2)

        # TODO migrate to expect_logged_messages
        for record in caplog.records:
            if "sync_heads" in record.getMessage() or "Syncing" in record.getMessage():
                sync_logs.append(record.getMessage())
    finally:
        # observer should never get stopped
        observer_stopped = not observer.is_alive()
        # If the observer stopped, it should probably leave garbage,
        # if not, it probably won't stop after all outstanding batches is processed
        is_condition_true_within_timeout(
            "is_processing_state_probably_settled",
            lambda: batch_scheduler.status == LocalSyncPathBatchSchedulerStatus.IDLE,
            timeout_seconds=5.0,
        )
        try:
            reconciler.stop()
        except Exception:
            logger.info(dump_potentially_useful_state_and_logs(repo_paths, batch_scheduler))
            raise

    return ScenarioResults(
        base_path=base_path,
        user_repo_state=collect_repo_state(repo_paths["user"]),
        agent_repo_state=collect_repo_state(repo_paths["agent"]),
        reconcilers=reconcilers,
        sync_logs=sync_logs,
        observer_stopped=observer_stopped,
        sent_messages=message_collector.sent_messages,
    )


def fail_to_start_scenario(scenario: UnstartableSyncScenario, base_path: Path, environment: Environment) -> FSEndState:
    base_path = base_path.absolute().resolve() / scenario.name
    trees, no_reconciler = prepare_scenario(scenario, base_path, environment)
    assert no_reconciler is None, "should have failed to construct scenario"
    return FSEndState(
        base_path=base_path,
        user_repo_state=collect_repo_state(trees["user"]),
        agent_repo_state=collect_repo_state(trees["agent"]),
    )


def collect_repo_state(repo: ScenarioWritableGitRepo) -> dict[str, str]:
    final_files = {}
    for file_path in repo.list_matching_files():
        final_files[file_path] = repo.read_file(Path(file_path))
    return final_files


# TODO yes these should be the same obj
def verify_filetree_equality(name: str, expected_trees: ExpectedFileTrees, results: FSEndState) -> None:
    """Verify that scenario results match expected outcomes."""

    if expected_trees.user_filetree is not None:
        assert results.user_repo_state == expected_trees.user_filetree, (
            f"user worktree != expected user worktree in {name}"
        )
    if expected_trees.agent_filetree is not None:
        assert results.agent_repo_state == expected_trees.agent_filetree, (
            f"agent worktree != expected agent worktree in {name}"
        )


def verify_scenario_outcome(scenario: SyncScenario, results: ScenarioResults) -> None:
    """Verify that scenario results match expected outcomes."""
    expected_outcome = scenario.expected_outcome

    verify_filetree_equality(scenario.name, expected_outcome, results)

    if expected_outcome.git_sync_reconciler_calls is not None:
        actual_git_sync_calls = results.reconcilers.git_sync_spy.call_count
        expected_git_sync_calls = expected_outcome.git_sync_reconciler_calls
        assert actual_git_sync_calls == expected_git_sync_calls, (
            f"{actual_git_sync_calls=} != {expected_git_sync_calls=}"
        )
    # TODO CI determinism
    if not _SKIP_DEBOUNCE_MOCK_FOR_HIGH_REALISM_MANUAL_TESTING:
        message = "Skipping filetree call counting because even with a debounce mock the watchdog scheduler timing doesn't even behave consistently in CI"
        logger.info(message)
    elif expected_outcome.filetree_reconciler_calls is not None:
        actual_filetree_calls = results.reconcilers.filetree_reconciler_calls
        expected_filetree_calls = expected_outcome.filetree_reconciler_calls
        assert actual_filetree_calls == expected_filetree_calls, (
            f"{actual_filetree_calls=} != {expected_filetree_calls=}"
        )

    # Check for expected log patterns
    log_iterator = iter(expected_outcome.logged_patterns)
    for i, expected_log_substring in enumerate(expected_outcome.logged_patterns):
        logger.trace("Checking for log pattern: {}", expected_log_substring)
        while log_iterator:
            log_record = next(log_iterator)
            if expected_log_substring in log_record:
                break
        else:
            raise AssertionError(f"expected_logs[{i} : '{expected_log_substring}' not found in sync logs")

    expected_notices = expected_outcome.message_notice_patterns_in_order
    if expected_notices is not None:
        verify_each_expected_notice_group_matches(results.sent_messages, expected_notices)

    verify_final_batch_reconciler_state(
        base_path=results.base_path, batch_scheduler=results.reconcilers.batch_scheduler, scenario=scenario
    )

    assert not results.observer_stopped, "observer should not have stopped for any reason"


def verify_each_expected_notice_group_matches(
    sent_messages: list[LocalSyncUpdateMessageUnion],
    expected_notice_groups: tuple[tuple[LocalSyncNoticeUnion, ...] | LocalSyncNoticeUnion, ...],
) -> None:
    assert len(sent_messages) >= len(expected_notice_groups), f"{len(expected_notice_groups)} < {len(sent_messages)=}"
    # maybe some pause retries at the end, w/e
    sent_and_accounted_for = sent_messages[: len(expected_notice_groups)]
    for i, (actual_message, expected_notices) in enumerate(zip(sent_and_accounted_for, expected_notice_groups)):
        if isinstance(expected_notices, LocalSyncNotice):
            expected_notices = (expected_notices,)
        actual_notices = actual_message.all_notices
        assert len(actual_notices) == len(expected_notices), (
            f"sent_messages[{i}]: {len(actual_notices)=} != {len(expected_notices)=}"
        )
        for j, (actual_notice, expected_notice_pattern) in enumerate(zip(actual_notices, expected_notices)):
            # This is kinda a back-door to let us add .* into reasons for pattern matching,
            # exploiting the fact that the describe() f-string is known
            expected_pattern = re.compile(expected_notice_pattern.describe())
            assert re.match(expected_pattern, actual_notice.describe()), (
                f"sent_messages[{i}].notices[{j}]: {actual_notice=} !~= {expected_notice_pattern=}"
            )


def verify_final_batch_reconciler_state(
    base_path: Path,
    batch_scheduler: LocalSyncPathBatchScheduler,
    scenario: SyncScenario,
) -> None:
    if scenario.expected_outcome.batch_remainder_due_to_stop_or_pause:
        expected_remainder = scenario.expected_outcome.absolutize_batch_remainder(under_root=base_path)
        is_condition_true_within_timeout(
            "is_expected_batch_remainder_leftover_due_to_death",
            lambda: batch_scheduler._batcher.pending_batch_by_tag == expected_remainder,
            timeout_seconds=5,
        )
        assert batch_scheduler._batcher.pending_batch_by_tag == expected_remainder, "\n".join(
            (
                "Death should leave exactly the dangling path batch... I think. Maybe a race condition? Maybe we can ignore it?",
                "Expected remainder:",
                json.dumps(expected_remainder, indent=4, default=str),
                "Actual LocalSyncPathBatchScheduler state:",
                batch_scheduler.describe_current_state(),
            )
        )
    else:
        # Sorry for copy/pasting
        has_finished = is_condition_true_within_timeout(
            "batch_scheduler.status is IDLE",
            lambda: batch_scheduler.status == LocalSyncPathBatchSchedulerStatus.IDLE,
            timeout_seconds=5,
        )
        assert has_finished, (
            "Batched reconciler did not finish processing within timeout. Current state: "
            + batch_scheduler.describe_current_state()
        )


#
# https://imbue-ai.slack.com/archives/C034US10UKY/p1756916879064609
#
OBSERVED_FLAKE = "OBSERVED_FLAKE due to watcher inconsistency or something more nefarious"
POTENTIAL_FLAKE = "POTENTIAL_FLAKE due to watcher inconsistency or something more nefarious"


# > AssertionError: Expected commit message 'user: Update main.py with World' not found in agent repo log.
# > Log: a5127e6 user: Add utils.py 882a9cf user: Add main.py and update README 189beb7 'initial commit' assert False
@integration_test
def test_user_files_and_commits_sync(
    temp_scenario_dir: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    docker_environment: DockerEnvironment,
) -> None:
    """Test file and commit actions on user repo sync to agent."""
    three_noticeless_messages = ((), (), ())
    scenario = SyncScenario(
        name="user_files_and_commits",
        initial_repo_state=RepoState(files={"README.md": "Initial readme"}),
        operations=(
            Create(in_repo="user", path="src/main.py", content="print('Hello')"),
            Append(in_repo="user", path="README.md", content="\nUser update 1"),
            Commit(in_repo="user", message="user: Add main.py and update README"),
            CheckCommitLogFor(in_repo="user", expected_message="user: Add main.py and update README"),
            WaitForBatchCallbackMessage(label="user_first_batch"),
            Create(in_repo="user", path="src/utils.py", content="def helper(): pass"),
            Commit(in_repo="user", message="user: Add utils.py"),
            WaitForBatchCallbackMessage(label="user_second_batch"),
            Append(in_repo="user", path="src/main.py", content="\nprint('World')"),
            Commit(in_repo="user", message="user: Update main.py with World"),
            WaitForBatchCallbackMessage(label="user_third_batch"),
            # Verify commits made it to agent
            CheckCommitLogFor(in_repo="agent", expected_message="user: Add main.py and update README"),
            CheckCommitLogFor(in_repo="agent", expected_message="user: Add utils.py"),
            CheckCommitLogFor(in_repo="agent", expected_message="user: Update main.py with World"),
            # echo
            WaitForBatchCallbackMessage(label="agent_echo"),
        ),
        expected_outcome=ExpectedOutcome(
            user_filetree={
                "README.md": "Initial readme\nUser update 1",
                "src/main.py": "print('Hello')\nprint('World')",
                "src/utils.py": "def helper(): pass",
            },
            agent_filetree={
                "README.md": "Initial readme\nUser update 1",
                "src/main.py": "print('Hello')\nprint('World')",
                "src/utils.py": "def helper(): pass",
            },
            filetree_reconciler_calls={"user": 3, "agent": 0 + 3},
            logged_patterns=("Syncing heads", "user_repo.*changed"),
            message_notice_patterns_in_order=three_noticeless_messages,
        ),
    )

    results = execute_scenario(scenario, temp_scenario_dir, caplog, mocker, docker_environment)
    verify_scenario_outcome(scenario, results)


# > AssertionError: repos still not synced after 1.0sec
# WaitForBatchCallbackMessage(), WaitForHeadConvergence(sec=1.0)
@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_ignored_vs_non_ignored_directories(
    temp_scenario_dir: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    docker_environment: DockerEnvironment,
) -> None:
    """Test file operations in watcher-ignored vs non-ignored directories."""
    scenario = SyncScenario(
        name="ignored_directories",
        initial_repo_state=RepoState(files={"app.py": "# App"}),
        operations=(
            # Agent operations in ignored directories (no triggers)
            Create(in_repo="agent", path=".git/hooks/pre-commit", content="#!/bin/bash\necho 'pre-commit'"),
            Create(in_repo="agent", path="node_modules/package/index.js", content="module.exports = {}"),
            Sleep(sec=0.5, plus_debounce=True),  # Allow time for ignored changes to be processed
            AssertIdle(),
            # User operations in normal directory (user trigger)
            Create(in_repo="user", path="src/feature.py", content="def feature(): return True"),
            WaitForBatchCallbackMessage(),
            # Agent echo, agent trigger
            WaitForBatchCallbackMessage(),
            AssertIdle(),
            # More ignored changes, no triggers
            Create(in_repo="agent", path=".git/config.local", content="# Local config"),
            Create(in_repo="agent", path="node_modules/another/lib.js", content="// Library"),
            Sleep(sec=0.5, plus_debounce=True),  # Allow time for ignored changes to be processed
            AssertIdle(),
            # Commit changes (user git-only trigger)
            Commit(in_repo="user", message="user: Add feature.py"),
            WaitForBatchCallbackMessage(),
            WaitForHeadConvergence(sec=1.0),
        ),
        expected_outcome=ExpectedOutcome(
            filetree_reconciler_calls={
                "user": 1 + 0,
                "agent": 0 + 1,
            },
            logged_patterns=("Syncing heads",),
        ),
    )

    results = execute_scenario(scenario, temp_scenario_dir, caplog, mocker, docker_environment)
    verify_scenario_outcome(scenario, results)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_bidirectional_commits_with_sync(
    temp_scenario_dir: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    docker_environment: DockerEnvironment,
) -> None:
    """Test commits from both user and agent with ample sync time."""
    synced_filetree = {
        "shared.txt": "\n".join(
            [
                "Initial content",
                "User line 1",
                "Agent line 1",
            ]
        ),
        "user_file.py": "\n".join(
            [
                "# User specific",
                "# More user code",
            ]
        ),
        "agent_file.py": "\n".join(
            [
                "# Agent specific",
            ]
        ),
    }
    scenario = SyncScenario(
        name="bidirectional_commits",
        initial_repo_state=RepoState(files={"shared.txt": "Initial content"}),
        operations=(
            # User commits
            Append(in_repo="user", path="shared.txt", content="\nUser line 1"),
            Commit(in_repo="user", message="user: Add first user line"),
            WaitForBatchCallbackMessage(),
            Create(in_repo="user", path="user_file.py", content="# User specific"),
            Commit(in_repo="user", message="user: Add user-specific file"),
            WaitForBatchCallbackMessage(),
            # Agent commits
            Create(in_repo="agent", path="agent_file.py", content="# Agent specific"),
            Commit(in_repo="agent", message="agent: Add agent-specific file"),
            Append(in_repo="agent", path="shared.txt", content="\nAgent line 1"),
            Commit(in_repo="agent", message="agent: Add first agent line"),
            # do these after actions so they get in the next batch
            CheckCommitLogFor(in_repo="agent", expected_message="user: Add first user line"),
            CheckCommitLogFor(in_repo="agent", expected_message="user: Add user-specific file"),
            WaitForBatchCallbackMessage(),
            CheckCommitLogFor(in_repo="user", expected_message="agent: Add agent-specific file"),
            CheckCommitLogFor(in_repo="user", expected_message="agent: Add first agent line"),
            # More user commits
            Append(in_repo="user", path="user_file.py", content="\n# More user code"),
            Commit(in_repo="user", message="user: Update user file"),
            WaitForBatchCallbackMessage(),
            # echo
            WaitForBatchCallbackMessage(),
        ),
        expected_outcome=ExpectedOutcome(
            user_filetree=synced_filetree,
            agent_filetree=synced_filetree,
            logged_patterns=("Syncing heads", "user_repo.*changed", "agent_repo.*changed"),
        ),
    )

    results = execute_scenario(scenario, temp_scenario_dir, caplog, mocker, docker_environment)
    verify_scenario_outcome(scenario, results)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_many_file_changes_both_sides(
    temp_scenario_dir: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    docker_environment: DockerEnvironment,
) -> None:
    """Test many file changes on both repos to verify path change detection."""
    scenario = SyncScenario(
        name="many_file_changes",
        initial_repo_state=RepoState(files={"base.txt": "Base"}),
        operations=(
            # Batch 1: User changes (first user triger)
            Create(in_repo="user", path="user1.txt", content="User file 1"),
            Create(in_repo="user", path="user2.txt", content="User file 2"),
            Create(in_repo="user", path="dir1/user3.txt", content="User file 3"),
            WaitForBatchCallbackMessage(),
            # Batch 2: Agent changes (first agent trigger)
            Create(in_repo="agent", path="agent1.txt", content="Agent file 1"),
            Create(in_repo="agent", path="agent2.txt", content="Agent file 2"),
            Create(in_repo="agent", path="dir2/agent3.txt", content="Agent file 3"),
            WaitForBatchCallbackMessage(),
            # Batch 3: Mixed changes (second user and agent triggers)
            Append(in_repo="user", path="base.txt", content="\nUser append"),
            Create(in_repo="user", path="user4.txt", content="User file 4"),
            Delete(in_repo="user", path="user1.txt"),
            Create(in_repo="agent", path="agent4.txt", content="Agent file 4"),
            Append(in_repo="agent", path="base.txt", content="\nAgent append"),
            WaitForBatchCallbackMessage(),
            # Batch 4: More Mixed changes (third user and agent triggers)
            Move(in_repo="user", source="user2.txt", destination="renamed_user2.txt"),
            Create(in_repo="user", path="dir3/nested/deep.txt", content="Deep file"),
            Move(in_repo="agent", source="agent1.txt", destination="moved/agent1.txt"),
            Delete(in_repo="agent", path="agent2.txt"),
            WaitForBatchCallbackMessage(),
            # echo (fourth user and agent triggers that sync nothing)
            WaitForBatchCallbackMessage(),
        ),
        expected_outcome=ExpectedOutcome(
            filetree_reconciler_calls={
                "user": 3 + 1,
                "agent": 3 + 1,
            },
            is_filetree_equality_expected=True,
        ),
    )

    results = execute_scenario(scenario, temp_scenario_dir, caplog, mocker, docker_environment)
    verify_scenario_outcome(scenario, results)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_conflicting_commits_cause_preemptive_pause(
    temp_scenario_dir: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    docker_environment: DockerEnvironment,
) -> None:
    """Test that rapid conflicting commits without sufficient sleep time cause an exception."""
    scenario = SyncScenario(
        name="conflicting_commits",
        initial_repo_state=RepoState(files={"conflict.txt": "Initial"}),
        operations=(
            # Create conflicting commits rapidly
            Append(in_repo="user", path="conflict.txt", content="\nUser change"),
            Commit(in_repo="user", message="user: Conflicting user commit"),
            Append(in_repo="agent", path="conflict.txt", content="\nAgent change"),
            Commit(in_repo="agent", message="agent: Conflicting agent commit"),
            WaitForHeadDivergence(sec=2.0),
            WaitForBatchCallbackMessage(is_pause_expected=True),
            # should go unprocessed
            Create(in_repo="user", path="trigger.txt", content="Trigger sync"),
            WaitForBatchCallbackMessage(is_pause_expected=True),
        ),
        expected_outcome=ExpectedOutcome(
            is_filetree_equality_expected=False,
            git_sync_reconciler_calls=0,  # pause should be auto-detected and pre-empted
            filetree_reconciler_calls={
                # should always be 0 because git reconciler goes first
                "user": 0,
                "agent": 0,
            },
            batch_remainder_due_to_stop_or_pause={
                LOCAL_GIT_SYNC_TAG: {
                    ("agent", ".git/refs/heads/scene-test/sync"),
                    ("user", ".git/refs/heads/scene-test/sync"),
                },
                LOCAL_FILESYNC_TAG: {
                    ("user", "conflict.txt"),
                    ("user", "trigger.txt"),
                    ("agent", "conflict.txt"),
                },
            },
            message_notice_patterns_in_order=(
                (LocalSyncNoticeOfPause(source_tag=LOCAL_GIT_SYNC_TAG, reason=r".*require manual merging"),),
            ),
        ),
    )

    results = execute_scenario(scenario, temp_scenario_dir, caplog, mocker, docker_environment)
    verify_scenario_outcome(scenario, results)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_pause_is_recoverable(
    temp_scenario_dir: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    docker_environment: DockerEnvironment,
) -> None:
    """Test that conflicting commits don't cause permanent pause."""
    scenario = SyncScenario(
        name="resumes_after_conflict",
        initial_repo_state=RepoState(files={"conflict.txt": "Initial"}),
        operations=(
            # Create conflicting commits rapidly
            Append(in_repo="user", path="conflict.txt", content="\nUser change"),
            Commit(in_repo="user", message="user: Conflicting user commit"),
            Append(in_repo="agent", path="conflict.txt", content="\nAgent change"),
            Commit(in_repo="agent", message="agent: Conflicting agent commit"),
            WaitForBatchCallbackMessage(is_pause_expected=True),
            # should go unprocessed
            Create(in_repo="user", path="trigger.txt", content="Trigger sync"),
            WaitForBatchCallbackMessage(is_pause_expected=True),
            ResetHard(in_repo="agent", refspec="HEAD^"),
            WaitForBatchCallbackMessage(is_pause_expected=False),
            # echo
            WaitForBatchCallbackMessage(),
        ),
        expected_outcome=ExpectedOutcome(
            is_filetree_equality_expected=False,
            git_sync_reconciler_calls=1,
            filetree_reconciler_calls={
                "user": 1,
                "agent": 1 + 1,
            },
            message_notice_patterns_in_order=(
                (LocalSyncNoticeOfPause(source_tag=LOCAL_GIT_SYNC_TAG, reason=r".*require manual merging"),),
            ),
        ),
    )

    results = execute_scenario(scenario, temp_scenario_dir, caplog, mocker, docker_environment)
    verify_scenario_outcome(scenario, results)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_agent_branch_switch_causes_pause(
    temp_scenario_dir: Path,
    caplog: LogCaptureFixture,
    mocker: MockerFixture,
    docker_environment: DockerEnvironment,
) -> None:
    """Test that switching the agent repo to a different branch during sync causes a pause."""
    scenario = SyncScenario(
        name="agent_branch_switch_causes_pause",
        initial_repo_state=RepoState(files={"content.txt": "Initial"}),
        operations=(
            # Sync initially works fine
            Create(in_repo="user", path="file1.txt", content="File 1"),
            WaitForBatchCallbackMessage(is_pause_expected=False),
            # Checkout a different branch in agent repo
            Checkout(in_repo="agent", branch="main"),
            # Now another edit results in a pause notification
            Create(in_repo="user", path="file2.txt", content="File 2"),
            WaitForBatchCallbackMessage(is_pause_expected=True),
        ),
        expected_outcome=ExpectedOutcome(
            is_filetree_equality_expected=False,
            git_sync_reconciler_calls=1,  # initial flush
            filetree_reconciler_calls={
                "user": 1,
                "agent": 0,
            },
            batch_remainder_due_to_stop_or_pause={
                LOCAL_GIT_SYNC_TAG: set(),
                LOCAL_FILESYNC_TAG: {
                    ("agent", "file1.txt"),
                    ("user", "file2.txt"),
                },
            },
            message_notice_patterns_in_order=(
                (),
                (
                    LocalSyncNoticeOfPause(
                        source_tag=LOCAL_GIT_STATE_GUARDIAN_TAG,
                        reason=r".*switched to `main` in the agent repo.*",
                    ),
                ),
            ),
        ),
    )

    results = execute_scenario(scenario, temp_scenario_dir, caplog, mocker, docker_environment)
    verify_scenario_outcome(scenario, results)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_build_fails_in_divergent_start_state(temp_scenario_dir: Path, docker_environment: DockerEnvironment) -> None:
    """Test that we can't start a sync when the branches have already diverged"""
    scenario = UnstartableSyncScenario(
        # TODO find way to use test name for these (ie test_build_fails_in_divergent_start_state.__name__)
        name="build_fails_in_divergent_start_state",
        initial_repo_state=RepoState(files={"conflict.txt": "Initial"}),
        operations_before_sync=(
            Append(in_repo="user", path="conflict.txt", content="\nUser change"),
            Commit(in_repo="user", message="user: Conflicting user commit"),
            Append(in_repo="agent", path="conflict.txt", content="\nAgent change"),
            Commit(in_repo="agent", message="agent: Conflicting agent commit"),
        ),
        expected_outcome=ExpectedStartRejection(
            rejection=pytest.raises(ExpectedSyncStartupError, match=r".*Must merge into agent.*diverged.*"),
            user_filetree={
                "conflict.txt": "Initial\nUser change",
            },
            agent_filetree={
                "conflict.txt": "Initial\nAgent change",
            },
        ),
    )
    failure_state = fail_to_start_scenario(scenario, temp_scenario_dir, docker_environment)
    verify_filetree_equality(scenario.name, scenario.expected_outcome, failure_state)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_build_fails_when_user_ahead_of_agent(temp_scenario_dir: Path, docker_environment: DockerEnvironment) -> None:
    """Test that we can't start a sync when the branches have already diverged"""
    scenario = UnstartableSyncScenario(
        # TODO find way to use test name for these (ie test_build_fails_in_divergent_start_state.__name__)
        name="build_fails_when_user_ahead_of_agent",
        initial_repo_state=RepoState(files={"content.txt": "Initial"}),
        operations_before_sync=(
            Append(in_repo="user", path="content.txt", content="\nUser change"),
            Commit(in_repo="user", message="user:User now ahead commit"),
        ),
        expected_outcome=ExpectedStartRejection(
            rejection=pytest.raises(ExpectedSyncStartupError, match=r".*Must push to agent.*"),
            user_filetree={"content.txt": "Initial\nUser change"},
        ),
    )
    failure_state = fail_to_start_scenario(scenario, temp_scenario_dir, docker_environment)
    verify_filetree_equality(scenario.name, scenario.expected_outcome, failure_state)


CANT_SYNC_WHILE_DIRTY = pytest.raises(
    ExpectedSyncStartupError,
    match=r".*Cannot sync without stashing if local git state is dirty or has untracked files.*",
)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_build_fails_when_tracked_is_modified(temp_scenario_dir: Path, docker_environment: DockerEnvironment) -> None:
    scenario = UnstartableSyncScenario(
        name="build_fails_when_modified_file_is_present",
        initial_repo_state=RepoState(files={"content.txt": "Initial"}),
        operations_before_sync=(Append(in_repo="user", path="content.txt", content="\nUser change"),),
        expected_outcome=ExpectedStartRejection(
            rejection=CANT_SYNC_WHILE_DIRTY,
            user_filetree={
                "content.txt": "Initial\nUser change",
            },
        ),
    )
    failure_state = fail_to_start_scenario(scenario, temp_scenario_dir, docker_environment)
    verify_filetree_equality(scenario.name, scenario.expected_outcome, failure_state)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_build_fails_when_untracked_is_present(temp_scenario_dir: Path, docker_environment: DockerEnvironment) -> None:
    scenario = UnstartableSyncScenario(
        name="build_fails_when_untracked_file_is_present",
        initial_repo_state=RepoState(files={"content.txt": "Initial"}),
        operations_before_sync=(Append(in_repo="user", path="untracked.txt", content="User change"),),
        expected_outcome=ExpectedStartRejection(
            rejection=CANT_SYNC_WHILE_DIRTY,
            user_filetree={
                "content.txt": "Initial",
                "untracked.txt": "User change",
            },
        ),
    )
    failure_state = fail_to_start_scenario(scenario, temp_scenario_dir, docker_environment)
    verify_filetree_equality(scenario.name, scenario.expected_outcome, failure_state)


@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
@integration_test
def test_build_fails_when_agent_on_wrong_branch(
    temp_scenario_dir: Path, docker_environment: DockerEnvironment
) -> None:
    """Test that we can't start a sync when the agent repo is on the wrong branch."""
    scenario = UnstartableSyncScenario(
        name="build_fails_when_agent_on_wrong_branch",
        initial_repo_state=RepoState(files={"content.txt": "Initial"}),
        operations_before_sync=(Checkout(in_repo="agent", branch="main"),),
        expected_outcome=ExpectedStartRejection(
            rejection=pytest.raises(ExpectedSyncStartupError, match=r"Agent's repo must be in .* branch"),
        ),
    )
    failure_state = fail_to_start_scenario(scenario, temp_scenario_dir, docker_environment)
    verify_filetree_equality(scenario.name, scenario.expected_outcome, failure_state)
