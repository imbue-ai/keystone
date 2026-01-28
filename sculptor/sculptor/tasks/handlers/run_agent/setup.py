import shlex
import tempfile
import time
from contextlib import contextmanager
from contextlib import nullcontext
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Callable
from typing import Generator
from typing import Iterator
from typing import Mapping
from typing import Sequence
from typing import cast
from urllib.parse import urlparse
from urllib.parse import urlunparse

import coolname
from loguru import logger
from typing_extensions import override

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import is_running_within_a_pytest_tree
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.git import get_repo_url_from_folder
from imbue_core.itertools import only
from imbue_core.nested_evolver import assign
from imbue_core.nested_evolver import chill
from imbue_core.nested_evolver import evolver
from imbue_core.processes.local_process import RunningProcess
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.progress_tracking.progress_tracking import RootProgressHandle
from imbue_core.progress_tracking.progress_tracking import SubprocessHandle
from imbue_core.progress_tracking.progress_tracking import start_finish_context
from imbue_core.sculptor import telemetry
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import TELEMETRY_TASK_INFO_JSON_STATE_FILE
from imbue_core.sculptor.telemetry import TelemetryProjectInfo
from imbue_core.sculptor.telemetry import TelemetryTaskInfo
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.secrets_utils import Secret
from imbue_core.serialization import SerializedException
from imbue_core.subprocess_utils import ProcessError
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import DockerEnvironment
from sculptor.interfaces.agents.agent import EnvironmentCreatedRunnerMessage
from sculptor.interfaces.agents.agent import EnvironmentStoppedRunnerMessage
from sculptor.interfaces.agents.agent import EnvironmentTypes
from sculptor.interfaces.agents.agent import ForkAgentSystemMessage
from sculptor.interfaces.agents.agent import PersistentUserMessageUnion
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.agent import StopAgentUserMessage
from sculptor.interfaces.agents.agent import SystemMessageUnion
from sculptor.interfaces.agents.agent import UserMessageUnion
from sculptor.interfaces.agents.agent import WarningRunnerMessage
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import ImageConfigTypes
from sculptor.interfaces.environments.base import ImageTypes
from sculptor.interfaces.environments.base import SSHD_SERVER_NAME
from sculptor.interfaces.environments.constants import ENVIRONMENT_WORKSPACE_DIRECTORY
from sculptor.interfaces.environments.errors import EnvironmentConfigurationChangedError
from sculptor.interfaces.environments.errors import EnvironmentNotFoundError
from sculptor.primitives.constants import USER_FACING_LOG_TYPE
from sculptor.server.llm_content_generation import generate_title_and_branch_from_initial_prompt
from sculptor.server.llm_content_generation import generate_title_only_from_initial_prompt
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.telemetry_info import get_telemetry_info
from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.providers.docker.docker_host_config import get_docker_host
from sculptor.services.environment_service.tool_readiness import ToolReadinessBlocker
from sculptor.services.environment_service.tool_readiness import ToolReadinessManager
from sculptor.services.git_repo_service.default_implementation import TRACKED_CHANGES_PATCHFILE_NAME
from sculptor.services.git_repo_service.default_implementation import UNTRACKED_FILES_TARBALL_NAME
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.task_service.data_types import ServiceCollectionForTask
from sculptor.tasks.handlers.run_agent.git import run_git_command_in_environment
from sculptor.utils.build import get_sculptor_folder
from sculptor.utils.timeout import log_runtime
from sculptor.utils.timeout import timeout_monitor
from sculptor.utils.type_utils import extract_leaf_types


def sanitize_git_url_robust(url: str | None) -> str | None:
    if not isinstance(url, str) or not url:
        return None

    try:
        url = url.strip()

        if url.startswith("file://"):
            return url

        if url.startswith("git@"):
            return _convert_ssh_to_https(url)

        if url.startswith("ssh://"):
            return _convert_ssh_protocol_to_https(url)

        parsed = urlparse(url)
        if parsed.hostname:
            new_netloc = parsed.hostname
            if parsed.port:
                new_netloc += f":{parsed.port}"

            clean_parts = parsed._replace(netloc=new_netloc)
            return str(urlunparse(clean_parts))
    except ValueError:
        return None
    return None


def _convert_ssh_to_https(ssh_url: str) -> str | None:
    if not ssh_url.startswith("git@"):
        return None

    try:
        parts = ssh_url.split("@", 1)
        if len(parts) != 2:
            return None

        host_path = parts[1]
        if ":" in host_path:
            host, path = host_path.split(":", 1)
            if path.endswith(".git"):
                path = path[:-4]
            return f"https://{host}/{path}"

        return None
    except Exception:
        return None


def _convert_ssh_protocol_to_https(ssh_url: str) -> str | None:
    if not ssh_url.startswith("ssh://"):
        return None

    try:
        ssh_url = ssh_url[6:]  # Remove "ssh://"

        if "@" in ssh_url:
            credentials, rest = ssh_url.split("@", 1)
            host_path = rest
        else:
            host_path = ssh_url

        if "/" in host_path:
            host, path = host_path.split("/", 1)
            if path.endswith(".git"):
                path = path[:-4]
            return f"https://{host}/{path}"

        return None
    except Exception:
        return None


# it will take at most this much time to notice when the process has finished
_POLL_SECONDS: float = 1.0
# if it takes longer than this, we give up waiting for the title and branch name to be predicted
_TITLE_NAME_TIMEOUT_SECONDS: float = 10.0
_FIXED_BRANCH_NAME_COUNTER_FOR_TESTING = 0

_ENVIRONMENT_CREATION_TIMEOUT_SECONDS: float = 300.0
_IMAGE_CREATION_TIMEOUT_SECONDS: float = 300.0


def _run_local_command_with_timeout_and_progress(
    concurrency_group: ConcurrencyGroup,
    command: Sequence[str],
    cwd: Path,
    process_timeout: float,
    warning_timeout: float,
    progress_handle: SubprocessHandle,
    on_timeout: Callable[[float], None],
) -> None:
    progress_handle.report_command(shlex.join(command))
    process: RunningProcess = concurrency_group.run_process_in_background(
        command,
        cwd=cwd,
        is_checked_by_group=True,
        on_output=progress_handle.report_output_line,
    )
    with timeout_monitor(
        concurrency_group,
        timeout=warning_timeout,
        on_timeout=on_timeout,
    ):
        process.wait(timeout=process_timeout)
    if process.returncode is not None:
        progress_handle.report_return_code(process.returncode)


def _run_command_in_environment_with_progress(
    environment: Environment,
    command: Sequence[str],
    secrets: Mapping[str, str | Secret],
    progress_handle: SubprocessHandle,
    cwd: str | None = None,
) -> None:
    progress_handle.report_command(shlex.join(command))
    process = environment.run_process_to_completion(
        command,
        secrets,
        cwd=cwd,
        on_output=progress_handle.report_output_line,
    )
    if process.returncode is not None:
        progress_handle.report_return_code(process.returncode)


def hard_overwrite_full_agent_workspace(
    environment: Environment,
    user_repo_path: Path,
    task_id: TaskID | None = None,
    services: ServiceCollectionForTask | None = None,
    task_id_if_keep_uncommitted: TaskID | None = None,
    blindly_sync_everything: bool = False,
    progress_handle: ProgressHandle | None = None,
) -> None:
    if progress_handle is None:
        progress_handle = ProgressHandle()
    concurrency_group = environment.concurrency_group
    with tempfile.NamedTemporaryFile(mode="w") as f:
        with (
            environment.get_snapshot_guard().shared_lock()
            if isinstance(environment, DockerEnvironment)
            else nullcontext()
        ):
            with log_runtime("rsyncing in-container repo with user repo"):
                sshd_hostname = get_docker_host()
                server_port_by_name = environment.server_port_by_name
                # TODO: only DockerEnvironment has server_port_by_name, not every Environment
                assert server_port_by_name is not None
                with start_finish_context(
                    progress_handle.track_subprocess("Syncing user repo to environment")
                ) as subprocess_handle:
                    _run_local_command_with_timeout_and_progress(
                        concurrency_group,
                        [
                            "rsync",
                            "-r",
                            "--no-D",
                            "--rsync-path=/imbue/nix_bin/rsync",
                            "--exclude=hooks/",
                            "--exclude=worktrees/",
                            "--exclude=index.lock",
                            "-e",
                            f"{get_sculptor_folder() / 'ssh' / 'ssh'} -p {server_port_by_name[SSHD_SERVER_NAME]}",
                            f"{user_repo_path}" + ("/.git/" if not blindly_sync_everything else "/"),
                            f"{environment.get_container_user()}@{sshd_hostname}:{str(ENVIRONMENT_WORKSPACE_DIRECTORY).rstrip('/')}"
                            + ("/.git/" if not blindly_sync_everything else "/"),
                        ]
                        + (["--delete"] if blindly_sync_everything else []),
                        cwd=user_repo_path,
                        warning_timeout=30,
                        process_timeout=300,
                        progress_handle=subprocess_handle,
                        on_timeout=lambda timeout: _send_warning_message(
                            task_id,
                            f"Rsyncing in-container repo with user repo is taking longer than expected ({timeout}s)",
                            services,
                        )
                        if task_id is not None and services is not None
                        else None,
                    )

                # At this point, we have an overly up-to-date .git, which may include things from after the user
                # clicked "Start Task".
                # if we're keeping uncommitted, then we need to sync from the special folder
                if task_id_if_keep_uncommitted is not None and not blindly_sync_everything:
                    copy_of_user_repo_path = (
                        get_sculptor_folder() / "user_repo_copies" / str(task_id_if_keep_uncommitted)
                    )
                    with start_finish_context(
                        progress_handle.track_subprocess("Copy uncommitted changes to environment")
                    ) as subprocess_handle:
                        _run_local_command_with_timeout_and_progress(
                            concurrency_group,
                            [
                                "rsync",
                                "-r",
                                "--no-D",
                                "--rsync-path=/imbue/nix_bin/rsync",
                                "--exclude=hooks/",
                                "--exclude=worktrees/",
                                "--exclude=objects/",
                                "--delete",
                                "-e",
                                f"{get_sculptor_folder() / 'ssh' / 'ssh'} -p {server_port_by_name[SSHD_SERVER_NAME]}",
                                f"{str(copy_of_user_repo_path).rstrip('/')}/.git/",
                                f"{environment.get_container_user()}@{sshd_hostname}:{str(ENVIRONMENT_WORKSPACE_DIRECTORY).rstrip('/')}/.git/",
                            ],
                            cwd=copy_of_user_repo_path,
                            warning_timeout=30,
                            process_timeout=300,
                            progress_handle=subprocess_handle,
                            on_timeout=lambda timeout: _send_warning_message(
                                task_id,
                                f"Rsyncing (part 2) in-container repo with user repo is taking longer than expected ({timeout}s)",
                                services,
                            )
                            if task_id is not None and services is not None
                            else None,
                        )

                    # Now the .git is "just right" except for some extra stuff in objects.
                    # We git clean to get rid of untracked files in the cached tarball.
                    # TODO(sam): Figure out a better mechanism of formatting the environment in description strings.
                    with start_finish_context(
                        progress_handle.track_subprocess("Cleaning untracked files in environment")
                    ) as subprocess_handle:
                        _run_command_in_environment_with_progress(
                            environment,
                            [
                                "bash",
                                "-c",
                                "((git diff --name-only | git restore --pathspec-from-file=-) || true) && git clean -fd",
                            ],
                            secrets={},
                            progress_handle=subprocess_handle,
                            cwd=str(ENVIRONMENT_WORKSPACE_DIRECTORY),
                        )

                    with timeout_monitor(
                        environment.concurrency_group,
                        timeout=30,
                        on_timeout=lambda timeout: _send_warning_message(
                            task_id,
                            f"Rsyncing (part 2) in-container repo with user repo is taking longer than expected ({timeout}s)",
                            services,
                        )
                        if task_id is not None and services is not None
                        else None,
                    ):
                        # At this point the worktree matches the .git at the time we clicked "Start Task",
                        # so now we need to transfer in all the uncommitted changes
                        environment.copy_from_local(
                            copy_of_user_repo_path / TRACKED_CHANGES_PATCHFILE_NAME,
                            "/imbue_addons/" + TRACKED_CHANGES_PATCHFILE_NAME,
                        )
                        # TODO(sam): Figure out a better mechanism of formatting the environment in description strings.
                        with start_finish_context(
                            progress_handle.track_subprocess("Applying tracked changes patch in environment")
                        ) as subprocess_handle:
                            _run_command_in_environment_with_progress(
                                environment,
                                [
                                    "git",
                                    "apply",
                                    "--allow-empty",
                                    "/imbue_addons/" + TRACKED_CHANGES_PATCHFILE_NAME,
                                ],
                                {},
                                subprocess_handle,
                            )

                        environment.copy_from_local(
                            copy_of_user_repo_path / UNTRACKED_FILES_TARBALL_NAME,
                            "/imbue_addons/" + UNTRACKED_FILES_TARBALL_NAME,
                        )

                        # TODO(sam): Figure out a better mechanism of formatting the environment in description strings.
                        with start_finish_context(
                            progress_handle.track_subprocess("Extracting untracked files tarball in environment")
                        ) as subprocess_handle:
                            _run_command_in_environment_with_progress(
                                environment,
                                ["tar", "-xf", "/imbue_addons/" + UNTRACKED_FILES_TARBALL_NAME],
                                {},
                                subprocess_handle,
                            )


@contextmanager
def message_queue_context(
    task: Task, task_state: AgentTaskStateV1, services: ServiceCollectionForTask
) -> Generator[
    tuple[
        Queue[UserMessageUnion | SystemMessageUnion | ResumeAgentResponseRunnerMessage],
        tuple[PersistentUserMessageUnion, ...],
        ChatInputUserMessage,
        ForkAgentSystemMessage | None,
    ],
    None,
    None,
]:
    """Subscribe to messages and wait for initial/fork messages."""
    with services.task_service.subscribe_to_user_and_sculptor_system_messages(task.object_id) as input_message_queue:
        # Wait for the initial user message
        initial_message = _wait_for_initial_user_message(
            user_message_queue=(cast(Queue[Message], input_message_queue)), task_id=task.object_id
        )

        # Handle fork message if this is a forked task
        parent_id = task.parent_task_id
        if parent_id is None:
            fork_message = None
        else:
            fork_message = _wait_for_fork_message(parent_id, cast(Queue[Message], input_message_queue))

        # Wait for the initial user message
        initial_message = _wait_for_initial_user_message(
            user_message_queue=cast(Queue[Message], input_message_queue), task_id=task.object_id
        )

        # Discard already processed messages
        _, re_queued_messages = _drop_already_processed_messages(
            task_state.last_processed_message_id, cast(Queue[Message], input_message_queue)
        )

        leaf_persistent_user_message_types = extract_leaf_types(PersistentUserMessageUnion)
        assert all(isinstance(message, leaf_persistent_user_message_types) for message in re_queued_messages)
        assert isinstance(re_queued_messages, tuple)
        # after the above checks, this cast is now safe
        re_queued_messages = cast(tuple[PersistentUserMessageUnion, ...], re_queued_messages)

        yield input_message_queue, re_queued_messages, initial_message, fork_message


@contextmanager
def branch_prediction_context(
    task: Task,
    task_state: AgentTaskStateV1,
    initial_message: ChatInputUserMessage,
    project: Project,
    services: ServiceCollectionForTask,
    settings: SculptorSettings,
    concurrency_group: ConcurrencyGroup,
    root_progress_handle: RootProgressHandle,
) -> Iterator[tuple[list[tuple[str, str]], Thread | None]]:
    """Start branch name prediction thread if needed."""
    title_and_branch_container: list[tuple[str, str]] = []
    title_thread = None

    if task_state.title is None or task_state.branch_name is None:
        with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
            branches_in_user_repo = repo.get_all_branches()

        existing_branches = sorted(branches_in_user_repo)

        credentials = services.config_service.get_credentials()
        title_thread = concurrency_group.start_new_thread(
            target=_predict_branch_name,
            args=(
                initial_message.text,
                existing_branches,
                title_and_branch_container,
                settings,
                credentials,
                root_progress_handle,
            ),
        )

    try:
        yield title_and_branch_container, title_thread
    finally:
        # Ensure thread is cleaned up if still running
        if title_thread and title_thread.is_alive():
            title_thread.join()


class SetupTaskSpecificContext(TaskSpecificContext):
    def __init__(self, task_id: TaskID, services: ServiceCollectionForTask) -> None:
        self.task_id = task_id
        self.services = services

    @override
    def emit_warning(self, message: str) -> None:
        _send_warning_message(self.task_id, message, self.services)


class EnvironmentReuseFailedPayload(telemetry.PosthogEventPayload):
    """PostHog event data for environment reuse failure."""

    reason: str = telemetry.with_consent(telemetry.ConsentLevel.ERROR_REPORTING)


@contextmanager
def environment_setup_context(
    project: Project,
    task: Task,
    task_data: AgentTaskInputsV1,
    task_state: AgentTaskStateV1,
    services: ServiceCollectionForTask,
    secrets: Mapping[str, str | Secret],
    concurrency_group: ConcurrencyGroup,
    root_progress_handle: RootProgressHandle,
    # TODO: Document why the below arg isn't just: shutdown_event=concurrency_group.shutdown_event
    shutdown_event: ReadOnlyEvent,
) -> Iterator[tuple[Environment, AgentTaskStateV1]]:
    """Set up the environment with the appropriate image."""
    # if we have an existing environment, try to reuse it
    environment: Environment | None = None
    used_old_env = False
    container_name = str(task.object_id)

    try:
        if task_state.environment_id is None:
            raise EnvironmentNotFoundError()

        environment = services.environment_service.create_environment(
            task_state.environment_id,
            config=task_data.environment_config,
            name=container_name,
            project_id=project.object_id,
            concurrency_group=concurrency_group,
            task_id=task.object_id,
        )
        used_old_env = True
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_REUSED_EXISTING_ENVIRONMENT,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=container_name,
            )
        )
    except (EnvironmentNotFoundError, EnvironmentConfigurationChangedError) as e:
        if isinstance(e, EnvironmentNotFoundError):
            logger.debug("Unable to start previous container because env was not found: {}", e)
        elif isinstance(e, EnvironmentConfigurationChangedError):
            logger.debug("Unable to start previous container because env config changed: {}", e)
        else:
            logger.debug("Unable to start previous container because: {}", e)
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_FAILED_TO_REUSE_EXISTING_ENVIRONMENT,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=container_name,
                payload=EnvironmentReuseFailedPayload(reason=repr(e)[:200]),
            )
        )

        with start_finish_context(root_progress_handle.track_image_build()) as image_build_handle:
            # otherwise, ensure we have an image
            image, task_state = _ensure_image(
                secrets,
                services,
                task_data.image_config,
                task.object_id,
                project,
                task_state,
                concurrency_group,
                shutdown_event,
                image_build_handle,
            )
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_IMAGE_ENSURED,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=container_name,
            )
        )

        # Create the environment
        with (
            timeout_monitor(
                concurrency_group,
                timeout=_ENVIRONMENT_CREATION_TIMEOUT_SECONDS,
                on_timeout=lambda timeout: _send_warning_message(
                    task.object_id,
                    f"Environment creation is taking longer than expected ({timeout}s)",
                    services,
                ),
            ),
            start_finish_context(root_progress_handle.track_container_setup(container_name)) as container_setup_handle,
        ):
            environment = services.environment_service.create_environment(
                image,
                config=task_data.environment_config,
                name=container_name,
                project_id=project.object_id,
                concurrency_group=concurrency_group,
                task_id=task.object_id,
                shutdown_event=shutdown_event,
                container_setup_handle=container_setup_handle,
            )
            # Makes debugging easier.
            environment.write_file("/imbue_addons/sculptor_task_id.txt", str(task.object_id))
    # just for pycharm, sigh
    assert isinstance(environment, extract_leaf_types(EnvironmentTypes))
    # cast is safe now
    environment = cast(EnvironmentTypes, environment)

    # Configure tool readiness hook and manager to block tool execution until setup completes
    tool_readiness = ToolReadinessManager(environment, task.object_id)
    # Remove any existing marker file to start from clean state
    # This handles environment reuse, forking, and resume scenarios
    tool_readiness.remove_ready_marker()

    # Only add blockers if we're actually going to do setup work
    # When reusing an environment, we skip repo sync so don't block on it
    will_sync_repo = task_state.last_processed_message_id is None and not used_old_env
    if will_sync_repo:
        tool_readiness.add_blockers(
            ToolReadinessBlocker.REPO_SYNCED,
        )
    else:
        # Environment is already set up, immediately mark as ready
        logger.debug("Skipping tool readiness blocking - environment already set up")
        tool_readiness.mark_ready()

    is_create_message_sent = False
    try:
        if will_sync_repo:
            task_id_if_keep_uncommitted = None if task_data.is_git_state_clean else task.object_id
            # TODO: project.user_git_repo_url can't actually be None, since the project must be initialized,
            # but perhaps we can get pyre to understand this
            user_git_repo_url = project.user_git_repo_url
            assert user_git_repo_url is not None
            with start_finish_context(
                root_progress_handle.track_snapshot_uncommitted_changes()
            ) as uncommitted_changes_progress_handle:
                hard_overwrite_full_agent_workspace(
                    environment=environment,
                    user_repo_path=Path(urlparse(user_git_repo_url).path),
                    task_id=task.object_id,
                    services=services,
                    task_id_if_keep_uncommitted=task_id_if_keep_uncommitted,
                    progress_handle=uncommitted_changes_progress_handle,
                )
            telemetry.emit_posthog_event(
                telemetry.PosthogEventModel(
                    name=SculptorPosthogEvent.ENVIRONMENT_SETUP_HARD_OVERWROTE_WORKSPACE,
                    component=ProductComponent.ENVIRONMENT_SETUP,
                    task_id=container_name,
                )
            )

            # Clear repo sync blocker
            tool_readiness.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)
        with services.data_model_service.open_task_transaction() as transaction:
            # emit a message
            services.task_service.create_message(
                EnvironmentCreatedRunnerMessage(environment=environment), task.object_id, transaction
            )
            # save the environment into the task state so we can resume
            if task_state.environment_id != environment.environment_id:
                task_state = task_state.evolve(task_state.ref().environment_id, environment.environment_id)
                latest_task = transaction.get_task(task.object_id)
                assert latest_task is not None
                task = task.evolve(latest_task.ref().current_state, task_state.model_dump())
                task = transaction.upsert_task(task)
        is_create_message_sent = True

        services.config_service.start_synchronizing_environment(project, task.object_id, environment)
        with logger.contextualize(environment=environment.get_extra_logger_context()):
            logger.debug("created environment")
            yield environment, task_state
    finally:
        services.config_service.stop_synchronizing_environment(project, task.object_id)
        should_destroy = False
        should_cleanup_images = False
        with services.data_model_service.open_task_transaction() as transaction:
            updated_task = transaction.get_task(task.object_id)
            if updated_task is not None:
                assert isinstance(updated_task.current_state, AgentTaskStateV1)
                if updated_task.current_state.environment_id != environment.environment_id:
                    should_destroy = True
                if updated_task.is_deleted or updated_task.is_deleting:
                    should_destroy = True
                    should_cleanup_images = True
            if is_create_message_sent:
                services.task_service.create_message(EnvironmentStoppedRunnerMessage(), task.object_id, transaction)
        environment.close()
        # if the task is no longer tied to this environment, there's no reason to keep this environment around
        # because it will never be reused. This could come about as a result of failure to persist the environment
        if should_destroy:
            environment.destroy()

        if should_cleanup_images:
            services.environment_service.remove_stale_images()


def finalize_git_setup(
    task: Task,
    task_state: AgentTaskStateV1,
    environment: Environment,
    fork_message: ForkAgentSystemMessage | None,
    title_thread: Thread | None,
    title_and_branch_container: list[tuple[str, str]],
    initial_message: ChatInputUserMessage,
    project: Project,
    task_data: AgentTaskInputsV1,
    services: ServiceCollectionForTask,
    root_progress_handle: RootProgressHandle,
    bare_repo_path: Path | None = None,
) -> AgentTaskStateV1:
    """Handle the final git setup steps after environment is ready."""
    if title_thread is None:
        with start_finish_context(root_progress_handle.track_agent_branch_checkout()) as agent_branch_checkout_handle:
            # Branch name already exists
            assert task_state.branch_name is not None
            full_branch_name = task_state.branch_name

            # Handle forked task branch setup
            if fork_message is not None:
                logger.debug("Ensuring that we are on the right branch for a forked task")
                _, stdout, _ = run_git_command_in_environment(
                    environment,
                    ["/imbue/nix_bin/git", "rev-parse", "--abbrev-ref", "HEAD"],
                    {},
                    check_output=True,
                )
                current_branch = stdout.strip()
                if current_branch != full_branch_name:
                    logger.debug("Checking out the right branch: {}", full_branch_name)
                    run_git_command_in_environment(
                        environment,
                        ["git", "checkout", "-b", full_branch_name],
                        {},
                        check_output=True,
                        is_retry_safe=True,
                    )
    else:
        # Initialize git if needed
        user_config = services.config_service.get_user_config()
        # Use user config values if available, otherwise fall back to default
        if user_config and user_config.user_email:
            email = user_config.user_email
            username = user_config.user_git_username
        else:
            email = "sculptor@imbue.com"
            username = "Sculptor"

        git_config_command = [
            "bash",
            "-c",
            f"git config --global user.email {email} && git config --global user.name '{username}'",
        ]
        run_git_command_in_environment(environment, git_config_command, {}, check_output=True)

        # Resolve branch prediction and checkout
        full_branch_name, task_state = _resolve_branch_name_prediction_thread_and_checkout_branch(
            title_and_branch_container=title_and_branch_container,
            title_thread=title_thread,
            task_id=task.object_id,
            project=project,
            task_state=task_state,
            initial_message=initial_message,
            environment=environment,
            git_hash=task_data.git_hash,
            services=services,
            keep_uncommitted=not task_data.is_git_state_clean,
            root_progress_handle=root_progress_handle,
        )

    return task_state


def write_telemetry_task_info(environment: Environment, task: Task, project: Project) -> None:
    telemetry_task_info_contents = _get_telemetry_task_info_contents(task.object_id, project)
    if telemetry_task_info_contents is not None:
        environment.write_file(
            str(environment.get_state_path() / TELEMETRY_TASK_INFO_JSON_STATE_FILE), telemetry_task_info_contents
        )


# TODO(PROD-1416): Is there a test that tests this handoff from Sculptor to container?
def _get_telemetry_task_info_contents(task_id: TaskID, project: Project) -> str | None:
    telemetry_info = get_telemetry_info()
    if telemetry_info is not None:
        original_git_repo_url = None
        if project.user_git_repo_url and project.user_git_repo_url.startswith("file://"):
            try:
                repo_path = Path(project.user_git_repo_url.replace("file://", ""))
                original_git_repo_url = get_repo_url_from_folder(repo_path)
                original_git_repo_url = sanitize_git_url_robust(original_git_repo_url)
            except Exception as e:
                logger.info("Failed to get upstream URL for {}: {}", project.user_git_repo_url, e)
                original_git_repo_url = sanitize_git_url_robust(project.user_git_repo_url)
        else:
            original_git_repo_url = sanitize_git_url_robust(project.user_git_repo_url)

        telemetry_project_info = TelemetryProjectInfo(
            telemetry_info=telemetry_info,
            project_id=str(project.object_id),
            gitlab_mirror_repo_url=project.our_git_repo_url,
            original_git_repo_url=original_git_repo_url,
        )
        telemetry_task_info = TelemetryTaskInfo(telemetry_project_info=telemetry_project_info, task_id=task_id)
        logger.info("Providing telemetry task info: model_dump={}", telemetry_task_info.model_dump())
        return telemetry_task_info.model_dump_json()
    return None


def _resolve_branch_name_prediction_thread_and_checkout_branch(
    project: Project,
    title_thread: Thread,
    title_and_branch_container: list[tuple[str, str]],
    task_id: TaskID,
    task_state: AgentTaskStateV1,
    initial_message: ChatInputUserMessage,
    environment: Environment,
    git_hash: str,
    services: ServiceCollectionForTask,
    keep_uncommitted: bool,
    root_progress_handle: RootProgressHandle,
) -> tuple[str, AgentTaskStateV1]:
    """
    Waits (a little while) for the title prediction thread to finish,
    then saves the title and branch name to the database.
    """
    title_thread.join(timeout=_TITLE_NAME_TIMEOUT_SECONDS)
    if title_thread.is_alive():
        branch_suffix = _get_random_branch_name()
        logger.warning("Title prediction thread did not finish in time, using defaults")
        title, full_branch_name = initial_message.text, f"sculptor/{branch_suffix}"
    else:
        title, full_branch_name = only(title_and_branch_container)
    with (
        services.git_repo_service.open_local_user_git_repo_for_write(project) as user_repo,
        start_finish_context(root_progress_handle.track_agent_branch_checkout()) as agent_branch_checkout_handle,
    ):
        # first make sure this branch exists in the user's repo
        logger.info("Attempting to create branch on user's repo: {}", full_branch_name)

        try:
            with start_finish_context(agent_branch_checkout_handle.track_subtask("Creating branch on user's repo")):
                user_repo.create_branch(full_branch_name, git_hash)
        except GitRepoError as e:
            # Check if the error is because the branch already exists
            if e.stderr and "already exists" in str(e.stderr):
                # this branch name is already taken, so we need to get a new one
                full_branch_name = f"sculptor/{_get_random_branch_name()}"
                # Try again with the random name - let any error propagate
                user_repo.create_branch(full_branch_name, git_hash)
                logger.info("Branch name already taken, using new one: {}", full_branch_name)
            else:
                # Re-raise any other error (invalid git_hash, permissions, etc.)
                raise

        # now get the agent to be up-to-date
        if keep_uncommitted:
            # will already have been synced in earlier, so we should be at the exact state that we want
            # however, logically we want the agent to be on a particular branch
            # thus, the easy thing to do is simply run git checkout -b <branch_name>
            # however, this can fail if you are in the middle of a merge/rebase/etc
            # in such cases, we *allow* this command to fail,
            # and simply tell the agent to remember that it's supposed to be on "sculptor/" prefixed branch names
            try:
                with start_finish_context(
                    agent_branch_checkout_handle.track_subtask(
                        "Checking out branch in container, with uncommitted changes"
                    )
                ):
                    environment.run_process_to_completion(
                        ["git", "checkout", "-b", full_branch_name],
                        secrets={},
                        cwd=str(ENVIRONMENT_WORKSPACE_DIRECTORY),
                    )
            except ProcessError as e:
                if e.returncode is not None:
                    # any exit code is fine, we tried our best
                    logger.debug("Failed to checkout branch with uncommitted changes, proceeding anyway: {}", e)
                else:
                    raise
        else:
            # Fetch the branch from the user's repo to the environment
            logger.info("Fetching branch from user's repo to environment: {}", full_branch_name)
            environment.push_into_environment_repo(user_repo, full_branch_name, full_branch_name)
            # now, we need to hard reset the environment to this branch
            with start_finish_context(
                agent_branch_checkout_handle.track_subtask(
                    "Checking out branch in container, without uncommitted changes"
                )
            ):
                environment.run_process_to_completion(
                    ["bash", "-c", f"git reset --hard && git clean -fd && git checkout {full_branch_name}"],
                    secrets={},
                    cwd=str(ENVIRONMENT_WORKSPACE_DIRECTORY),
                )
        logger.info("Done fetching branch from user's repo to environment")

    mutable_task_state = evolver(task_state)
    assign(mutable_task_state.title, lambda: title)
    task_repo_path = ENVIRONMENT_WORKSPACE_DIRECTORY
    assign(mutable_task_state.task_repo_path, lambda: task_repo_path)
    assign(mutable_task_state.branch_name, lambda: full_branch_name)
    task_state = chill(mutable_task_state)

    # might as well commit our progress
    with services.data_model_service.open_task_transaction() as transaction:
        task_row = transaction.get_task(task_id)
        assert task_row is not None
        task_row = task_row.evolve(task_row.ref().current_state, task_state.model_dump())
        _task_row = transaction.upsert_task(task_row)
    return full_branch_name, task_state


def _predict_branch_name(
    initial_prompt: str,
    existing_branches: Sequence[str],
    title_and_branch_container: list[tuple[str, str]],
    settings: SculptorSettings,
    credentials: Credentials,
    root_progress_handle: RootProgressHandle,
) -> None:
    with start_finish_context(
        root_progress_handle.track_branch_name_and_task_title_generation(
            existing_branches[0] if existing_branches else "<unknown>"
        )
    ) as branch_name_and_task_title_handler:
        if settings.TESTING.INTEGRATION_ENABLED:
            title_and_branch_container.append(_generate_fixed_title_and_branch_for_testing())
            branch_name_and_task_title_handler.report_generated_branch_name(
                title_and_branch_container[-1][1], title_and_branch_container[-1][0]
            )
            return
        try:
            logger.debug("Found {} existing branches in repository", len(existing_branches))
            logger.info("Generating title and branch name for task...")
            title_and_branch = generate_title_and_branch_from_initial_prompt(
                initial_prompt,
                existing_branches,
                credentials,
            )
            title = title_and_branch.title
            branch_suffix = title_and_branch.branch_name
            if branch_suffix in existing_branches:
                branch_suffix = _get_random_branch_name()
            full_branch_name = f"sculptor/{branch_suffix}"
            logger.info("Generated title: '{}' and branch: '{}'", title, full_branch_name)
            title_and_branch_container.append((title, full_branch_name))
            emit_posthog_event(
                PosthogEventModel(
                    name=SculptorPosthogEvent.TASK_PREDICT_BRANCH_NAME,
                    component=ProductComponent.TASK,
                    payload=title_and_branch,
                )
            )
        except Exception as e:
            log_exception(
                e,
                "Failed to generate title and branch name",
                priority=ExceptionPriority.LOW_PRIORITY,
            )
            title = generate_title_only_from_initial_prompt(
                prompt=initial_prompt,
                existing_branches=existing_branches,
                credentials=credentials,
            )
            logger.info("Generated fallback title: '{}'", title)
            full_branch_name = f"sculptor/{_get_random_branch_name()}"
            title_and_branch_container.append((title, full_branch_name))
        finally:
            branch_name_and_task_title_handler.report_generated_branch_name(
                title_and_branch_container[-1][1], title_and_branch_container[-1][0]
            )


def _generate_fixed_title_and_branch_for_testing() -> tuple[str, str]:
    global _FIXED_BRANCH_NAME_COUNTER_FOR_TESTING
    _FIXED_BRANCH_NAME_COUNTER_FOR_TESTING += 1
    return (
        f"Task {_FIXED_BRANCH_NAME_COUNTER_FOR_TESTING}",
        f"branch_{_FIXED_BRANCH_NAME_COUNTER_FOR_TESTING}",
    )


def _get_random_branch_name() -> str:
    return coolname.generate_slug(3)


def load_initial_task_state(services: ServiceCollectionForTask, task: Task) -> tuple[AgentTaskStateV1, Project]:
    logger.info("loading initial task state (if any)")
    with services.data_model_service.open_task_transaction() as transaction:
        task_row = transaction.get_task(task.object_id)
        assert task_row is not None, "Task must exist in the database"
        if task_row.current_state is None:
            logger.debug("no current state found, creating a new one")
            task_state = AgentTaskStateV1()
        else:
            logger.debug("found existing task state, loading it...")
            task_state = AgentTaskStateV1.model_validate(task_row.current_state)
        # load the project so that we can figure out the repo path as well
        project = transaction.get_project(task.project_id)
        assert project is not None, "Project must exist in the database"
    return task_state, project


def _send_warning_message(
    task_id: TaskID,
    message: str,
    services: ServiceCollectionForTask,
    error: Exception | None = None,
) -> None:
    with services.data_model_service.open_task_transaction() as transaction:
        logger.warning(message, exc_info=error)
        serialized_error = SerializedException.build(error) if error is not None else None
        warning_message = WarningRunnerMessage(message=message, error=serialized_error)
        if not is_running_within_a_pytest_tree():
            services.task_service.create_message(warning_message, task_id, transaction)


def _ensure_image(
    secrets: Mapping[str, str | Secret],
    services: ServiceCollectionForTask,
    image_config: "ImageConfigTypes",
    task_id: TaskID,
    project: Project,
    task_state: AgentTaskStateV1,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent,
    progress_handle: ProgressHandle,
) -> tuple[ImageTypes, AgentTaskStateV1]:
    image = task_state.image

    if image is None:
        with logger.contextualize(log_type=USER_FACING_LOG_TYPE, task_id=task_id):
            logger.debug("creating image")
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_IMAGE_CREATION_STARTED,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=str(task_id),
            )
        )
        with timeout_monitor(
            concurrency_group,
            timeout=_IMAGE_CREATION_TIMEOUT_SECONDS,
            on_timeout=lambda timeout: _send_warning_message(
                task_id,
                f"Image creation is taking longer than expected ({timeout}s)",
                services,
            ),
        ):
            # TODO: project.user_git_repo_url can't actually be None, since the project must be initialized,
            # but pyre doesn't know that; ideally we can fix this by changing the initialization of project or splitting it into two types
            # where one always includes the user_git_repo_url
            git_repo_url = project.user_git_repo_url
            assert git_repo_url is not None
            active_repo_path = Path(urlparse(git_repo_url).path)
            cached_repo_path = project.get_cached_repo_path()
            # FIXME: it seems like this might be fragile if tasks are allowed different configs/secrets since we cache on project id elsewhere
            image = services.environment_service.ensure_image(
                config=image_config,
                active_repo_path=active_repo_path,
                cached_repo_path=cached_repo_path,
                secrets=secrets,
                project_id=project.object_id,
                task_specific_context=SetupTaskSpecificContext(task_id, services),
                image_metadata=ImageMetadataV1.from_task(task_id=task_id, sequence_number=0),
                shutdown_event=shutdown_event,
                progress_handle=progress_handle,
            )

        with logger.contextualize(log_type=USER_FACING_LOG_TYPE, task_id=task_id):
            logger.debug("created image: {}", image)
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_IMAGE_CREATION_FINISHED,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=str(task_id),
            )
        )

        task_state = task_state.evolve(task_state.ref().image, image)
        with services.data_model_service.open_task_transaction() as transaction:
            task_row = transaction.get_task(task_id)
            assert task_row is not None
            task_row = task_row.evolve(task_row.ref().current_state, task_state)
            _updated_task_row = transaction.upsert_task(task_row)
    else:
        with logger.contextualize(log_type=USER_FACING_LOG_TYPE, task_id=task_id):
            logger.debug("using existing image: {}", image)
    return image, task_state


def _drop_already_processed_messages(
    last_processed_input_message_id: AgentMessageID | None,
    user_message_queue: Queue[Message],
) -> tuple[tuple[Message, ...], tuple[Message, ...]]:
    """
    Drops all user messages that have already been processed by the agent.
    Return the dropped messages as well as the messages that will be re-queued.
    """
    # catch up, if necessary, to where we were last time
    dropped_messages: list[Message] = []
    found_last_processed_input_message = False
    if last_processed_input_message_id is not None:
        # Consume all messages up to the last processed one
        while not user_message_queue.empty():
            message = user_message_queue.get()
            dropped_messages.append(message)
            if message.message_id == last_processed_input_message_id:
                found_last_processed_input_message = True
                break
        if not found_last_processed_input_message:
            raise Exception(f"Unable to find last processed message in queue: {last_processed_input_message_id}")

        # And then consume all ephemeral messages until the next message that needs to be processed
        while not user_message_queue.empty():
            if user_message_queue.queue and user_message_queue.queue[0].is_ephemeral:
                dropped_message = user_message_queue.get()
                dropped_messages.append(dropped_message)
                logger.debug(f"Dropping ephemeral message after restart: {dropped_message}")
            else:
                break

    # remove all ephemeral messages up to the last stop agent user message
    last_stop_agent_user_message_id = None
    for message in reversed(user_message_queue.queue):
        if isinstance(message, StopAgentUserMessage):
            last_stop_agent_user_message_id = message.message_id
            break

    re_queued_messages: list[Message] = []
    if last_stop_agent_user_message_id is not None:
        while not user_message_queue.empty():
            message = user_message_queue.get()
            if message.is_ephemeral:
                dropped_messages.append(message)
            else:
                re_queued_messages.append(message)
            if message.message_id == last_stop_agent_user_message_id:
                break
    return tuple(dropped_messages), tuple(re_queued_messages)


def _wait_for_initial_user_message(user_message_queue: Queue[Message], task_id: TaskID) -> ChatInputUserMessage:
    """
    Waits for the first user message AFTER the most recent fork message if it exists OR the start of the task.
    """
    while True:
        user_input_message: ChatInputUserMessage | None = None
        for i in range(user_message_queue.qsize() - 1, -1, -1):
            message = user_message_queue.queue[i]
            if isinstance(message, ForkAgentSystemMessage):
                # Ensure that this is a forked *from* message
                if message.child_task_id != task_id:
                    continue
                if user_input_message is not None:
                    return user_input_message
                break
            elif isinstance(message, ChatInputUserMessage):
                user_input_message = message
        if user_input_message is not None:
            return user_input_message
        time.sleep(_POLL_SECONDS)


def _wait_for_fork_message(parent_id: TaskID, user_message_queue: Queue[Message]) -> ForkAgentSystemMessage:
    while True:
        for i in range(0, user_message_queue.qsize()):
            message = user_message_queue.queue[i]
            if isinstance(message, ForkAgentSystemMessage):
                if message.parent_task_id == parent_id:
                    return message
        time.sleep(_POLL_SECONDS)
