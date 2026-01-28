import datetime
import os
import time
from pathlib import Path
from queue import Empty
from queue import Queue
from typing import Any
from typing import Callable
from typing import Sequence
from typing import TypeVar
from typing import assert_never

from loguru import logger
from pydantic import AnyUrl
from tenacity import RetryCallState
from tenacity import retry
from tenacity import retry_all
from tenacity import retry_if_exception_type
from tenacity import stop_never
from tenacity import wait_fixed

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import is_live_debugging
from imbue_core.concurrency_group import ConcurrencyExceptionGroup
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.errors import ExpectedError
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.nested_evolver import assign
from imbue_core.nested_evolver import chill
from imbue_core.nested_evolver import evolver
from imbue_core.progress_tracking.progress_models import RootProgress
from imbue_core.progress_tracking.progress_tracking import RootProgressHandle
from imbue_core.sculptor import telemetry
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.state.messages import PersistentAgentMessage
from imbue_core.sculptor.state.messages import PersistentUserMessage
from imbue_core.sculptor.telemetry import send_exception_to_posthog
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.serialization import SerializedException
from sculptor.agents.default.claude_code_sdk.agent_wrapper import ClaudeCodeSDKAgent
from sculptor.agents.default.codex.agent_wrapper import CodexAgent
from sculptor.agents.default.posthog_utils import emit_posthog_event_for_user_message
from sculptor.agents.hello_agent.agent_wrapper import HelloAgent
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Notification
from sculptor.database.models import NotificationID
from sculptor.database.models import NotificationImportance
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import Agent
from sculptor.interfaces.agents.agent import AgentCrashedRunnerMessage
from sculptor.interfaces.agents.agent import AgentSnapshotFailureRunnerMessage
from sculptor.interfaces.agents.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.agents.agent import CheckControlUserMessage
from sculptor.interfaces.agents.agent import ClaudeCodeSDKAgentConfig
from sculptor.interfaces.agents.agent import CodexAgentConfig
from sculptor.interfaces.agents.agent import EnvironmentCrashedRunnerMessage
from sculptor.interfaces.agents.agent import EnvironmentRestartedRunnerMessage
from sculptor.interfaces.agents.agent import HelloAgentConfig
from sculptor.interfaces.agents.agent import KilledAgentRunnerMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdateCompletedMessage
from sculptor.interfaces.agents.agent import ManualSyncMergeIntoAgentAttemptedMessage
from sculptor.interfaces.agents.agent import MessageFeedbackUserMessage
from sculptor.interfaces.agents.agent import MessageTypes
from sculptor.interfaces.agents.agent import PersistentRequestCompleteAgentMessage
from sculptor.interfaces.agents.agent import PersistentRunnerMessageUnion
from sculptor.interfaces.agents.agent import PersistentUserMessageUnion
from sculptor.interfaces.agents.agent import ProgressUpdateRunnerMessage
from sculptor.interfaces.agents.agent import RequestStartedAgentMessage
from sculptor.interfaces.agents.agent import RequestStoppedAgentMessage
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.agent import StartedAgentSnapshotRunnerMessage
from sculptor.interfaces.agents.agent import StopAgentUserMessage
from sculptor.interfaces.agents.agent import SystemMessageUnion
from sculptor.interfaces.agents.agent import UnexpectedErrorRunnerMessage
from sculptor.interfaces.agents.agent import UpdatedArtifactAgentMessage
from sculptor.interfaces.agents.agent import UserMessageUnion
from sculptor.interfaces.agents.agent import WarningRunnerMessage
from sculptor.interfaces.agents.artifacts import FileAgentArtifact
from sculptor.interfaces.agents.checks import Check
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_CLEAN_SHUTDOWN_ON_INTERRUPT
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_FROM_SIGINT
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_FROM_SIGTERM
from sculptor.interfaces.agents.constants import TMUX_OUTPUT_ARTIFACT_NAME
from sculptor.interfaces.agents.errors import AgentClientError
from sculptor.interfaces.agents.errors import AgentCrashed
from sculptor.interfaces.agents.errors import UncleanTerminationAgentError
from sculptor.interfaces.agents.errors import WaitTimeoutAgentError
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import EnvironmentRestartRequired
from sculptor.interfaces.environments.base import ImageTypes
from sculptor.interfaces.environments.constants import AGENT_DATA_PATH
from sculptor.interfaces.environments.errors import EnvironmentFailure
from sculptor.interfaces.environments.errors import EnvironmentNotHealthy
from sculptor.primitives.ids import RequestID
from sculptor.primitives.ids import UserReference
from sculptor.services.config_service.api import ConfigService
from sculptor.services.data_model_service.data_types import DataModelTransaction
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.services.task_service.api import TaskService
from sculptor.services.task_service.data_types import ServiceCollectionForTask
from sculptor.services.task_service.errors import TaskError
from sculptor.services.task_service.errors import UserPausedTaskError
from sculptor.services.task_service.errors import UserStoppedTaskError
from sculptor.tasks.handlers.run_agent.checks.check_process import InfoFromSculptorForChecks
from sculptor.tasks.handlers.run_agent.checks.check_process_controller import CheckProcessController
from sculptor.tasks.handlers.run_agent.checks.output_location import CheckRunOutputLocation
from sculptor.tasks.handlers.run_agent.setup import branch_prediction_context
from sculptor.tasks.handlers.run_agent.setup import environment_setup_context
from sculptor.tasks.handlers.run_agent.setup import finalize_git_setup
from sculptor.tasks.handlers.run_agent.setup import load_initial_task_state
from sculptor.tasks.handlers.run_agent.setup import message_queue_context
from sculptor.tasks.handlers.run_agent.setup import write_telemetry_task_info
from sculptor.utils.model_progress_tracking_handles import RootProgressModelHandle
from sculptor.utils.shutdown import GLOBAL_SHUTDOWN_EVENT
from sculptor.utils.timeout import TIMING_LOG_THRESHOLD_SECONDS
from sculptor.utils.timeout import format_timing_log
from sculptor.utils.timeout import log_runtime

# it will take at most this much time to notice when the process has finished
_POLL_SECONDS: float = 1.0
# how long to wait for the agent to shut down after the user has requested it (before killing it)
_MAX_SOFT_SHUTDOWN_SECONDS: float = 10.0
# how long to wait when hard killing the agent after the soft shutdown has been requested
_MAX_HARD_SHUTDOWN_SECONDS: float = 10.0
# how long to wait after a local sync change before we consider the state "settled" enough to snapshot
_LOCAL_SYNC_CHANGE_DEBOUNCE_SECONDS: float = float(
    os.environ.get("_OVERRIDE_LOCAL_SYNC_CHANGE_DEBOUNCE_SECONDS", "60.0")
)


class AgentTaskFailure(TaskError):
    pass


class AgentHardKilled(ExpectedError):
    pass


class AgentShutdownCleanly(ExpectedError):
    pass


class AgentPaused(AgentShutdownCleanly):
    """
    The agent was paused by the user (typically via ctrl-c) and will be resumed when the process restarts.
    """


class UnknownAgentConfigError(ExpectedError):
    pass


def _log_environment_retry(retry_state: RetryCallState) -> None:
    """This function is used to log the retry when an Environment needs to be restarted."""
    fn_name = getattr(retry_state.fn, "__name__", "unknown")
    sleep_time = retry_state.next_action.sleep if retry_state.next_action is not None else 0
    outcome = retry_state.outcome

    if outcome is not None:
        exception = outcome.exception()
        error_message = type(exception).__name__ + ": " + str(exception)
    else:
        error_message = "unknown"

    logger.debug(
        f"Retrying {fn_name} in {sleep_time:.2f} seconds, attempt {retry_state.attempt_number} due to required environment restart: {error_message}"
    )


retry_for_environment_restart = retry(
    stop=stop_never,
    wait=wait_fixed(0.1),
    retry=retry_all(retry_if_exception_type((EnvironmentRestartRequired,))),
    before_sleep=_log_environment_retry,
)


class _ProgressUpdateCallback:
    def __init__(self, services: ServiceCollectionForTask, task_id: TaskID) -> None:
        self._services = services
        self._task_id = task_id

    def run(self, progress: RootProgress) -> None:
        with self._services.data_model_service.open_task_transaction() as transaction:
            self._services.task_service.create_message(
                message=ProgressUpdateRunnerMessage(
                    progress=progress,
                ),
                task_id=self._task_id,
                transaction=transaction,
            )


def _create_root_progress_handle(
    services: ServiceCollectionForTask,
    task_id: TaskID,
) -> RootProgressModelHandle:
    progress_update_callback = _ProgressUpdateCallback(services, task_id)
    root_progress_handle = RootProgressModelHandle(progress_update_callback.run)
    return root_progress_handle


@retry_for_environment_restart
def run_agent_task_v1(
    task_data: AgentTaskInputsV1,
    task: Task,
    services: ServiceCollectionForTask,
    task_deadline: datetime.datetime | None,
    settings: SculptorSettings,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent,
    on_agent_started: Callable[[], None] | None = None,
) -> Callable[[DataModelTransaction], Any] | None:
    """
    At a high level, the purpose of this task is to run an Agent in an Environment.

    Messages from the user are handled as "requests" to the agent, which may be made in parallel.

    Because of this, agents should emit `PersistentRequestCompleteAgentMessage`s  when they have finished processing a message.
    This enables us to snapshot the state of the agent when all messages have been processed.

    Note that this means there is no guarantee that the agent will be able to snapshot --
    if there are continually many pending messages, the state is never guaranteed to be consistent,
    and thus we will not snapshot it.

    Like all tasks, this task should be idempotent, so it can be restarted at any time.

    This task creates the image if it doesn't exist, then creates an `Environment` and runs the `Agent` inside.
    Really, the purpose is just to get everything to a place where we can call `_run_agent_in_environment`

    `run_agent_task_v1` is responsible for the setup and error handling --
    see `_run_agent_in_environment` for the core event loop of the Agent.
    """
    user_reference = task.user_reference
    task_id = task.object_id

    # TODO(sam): Use the "real" root progress handle here for at least internal builds.
    root_progress_handle = RootProgressHandle()

    try:
        logger.debug("running task {} for user {}", task_id, user_reference)
        setup_start_time = time.monotonic()

        # Load task state and project
        task_state, project = load_initial_task_state(services, task)

        # Set up message queue and get initial messages
        with (
            message_queue_context(task, task_state, services) as (
                input_message_queue,
                re_queued_messages,
                initial_message,
                fork_message,
            ),
            concurrency_group.make_concurrency_group(name=f"run_agent_v1_{task_id}") as environment_concurrency_group,
            branch_prediction_context(
                task,
                task_state,
                initial_message,
                project,
                services,
                settings,
                environment_concurrency_group,
                root_progress_handle,
            ) as (
                title_and_branch_container,
                title_thread,
            ),
        ):
            # Load secrets
            secrets = services.config_service.get_user_secrets(task_data.available_secrets)

            # Set up environment
            with environment_setup_context(
                project,
                task,
                task_data,
                task_state,
                services,
                secrets,
                environment_concurrency_group,
                root_progress_handle,
                shutdown_event,
            ) as (
                environment,
                task_state,
            ):
                telemetry.emit_posthog_event(
                    telemetry.PosthogEventModel(
                        name=SculptorPosthogEvent.AGENT_TASK_ENVIRONMENT_SETUP_FINISHED,
                        component=ProductComponent.AGENT_TASK,
                        task_id=str(task_id),
                    )
                )
                # Handle git initialization and branch setup
                task_state = finalize_git_setup(
                    task=task,
                    task_state=task_state,
                    environment=environment,
                    fork_message=fork_message,
                    title_thread=title_thread,
                    title_and_branch_container=title_and_branch_container,
                    initial_message=initial_message,
                    project=project,
                    task_data=task_data,
                    services=services,
                    root_progress_handle=root_progress_handle,
                )
                write_telemetry_task_info(
                    environment=environment,
                    task=task,
                    project=project,
                )
                telemetry.emit_posthog_event(
                    telemetry.PosthogEventModel(
                        name=SculptorPosthogEvent.AGENT_TASK_GIT_SETUP_FINALIZED,
                        component=ProductComponent.AGENT_TASK,
                        task_id=str(task_id),
                    )
                )
                setup_duration = time.monotonic() - setup_start_time
                if setup_duration >= TIMING_LOG_THRESHOLD_SECONDS:
                    logger.debug(format_timing_log("task setup", setup_duration))

                try:
                    logger.debug("time after restart: {}", time.monotonic())
                    # and run the agent in the environment until it either finishes or the environment dies
                    return _run_agent_in_environment(
                        task=task,
                        task_data=task_data,
                        task_state=task_state,
                        re_queued_messages=re_queued_messages,
                        input_message_queue=input_message_queue,
                        environment=environment,
                        services=services,
                        project=project,
                        settings=settings,
                        shutdown_event=shutdown_event,
                        on_agent_started=on_agent_started,
                    )
                # if we have to restart the environment, emit a message to communicate that fact, then do so
                except EnvironmentRestartRequired as e:
                    with services.data_model_service.open_task_transaction() as transaction:
                        services.task_service.create_message(
                            EnvironmentRestartedRunnerMessage(
                                error=SerializedException.build(e), message=f"Restarting because {e}"
                            ),
                            task_id,
                            transaction,
                        )
                    raise e
    # unwrap single EnvironmentRestartRequired exceptions from ConcurrencyExceptionGroup
    except ConcurrencyExceptionGroup as e:
        if e.only_exception_is_instance_of(EnvironmentRestartRequired):
            raise e.exceptions[0]
        # otherwise, handle it as a general exception
        _on_exception(e, task_id, user_reference, services, shutdown_event)
    # all other exceptions should be handled and turned into task failures
    except Exception as e:
        _on_exception(e, task_id, user_reference, services, shutdown_event)
    return None


# TODO: this design can be fairly easily extended to enable direct tool invocations
#  just send a user message, and treat it as an outstanding request
#  it ought to be possible to request to "stop" an invocation as well,
#  The main design question here is how to handle outputs
#  (plain text vs json, how to show in the UI, etc, since generic tools can return anything)
def _run_agent_in_environment(
    task: Task,
    task_data: AgentTaskInputsV1,
    task_state: AgentTaskStateV1,
    re_queued_messages: tuple[PersistentUserMessageUnion, ...],
    input_message_queue: Queue[UserMessageUnion | SystemMessageUnion | ResumeAgentResponseRunnerMessage],
    environment: Environment,
    services: ServiceCollectionForTask,
    project: Project,
    settings: SculptorSettings,
    shutdown_event: ReadOnlyEvent,
    on_agent_started: Callable[[], None] | None = None,
) -> Callable[[DataModelTransaction], Any] | None:
    """
    The core agent event loop: runs the Agent in the given Environment.

    Think of this sort of like a "main" loop in a game engine:
    - it starts the agent, and then continuously polls for new messages from the agent and the user
    - it handles the agent's output (eg, by sending it to the database)
    - it handles the user messages (eg, by sending them to the agent)
    - it syncs artifacts from the agent's output to the task_service
    """
    telemetry.emit_posthog_event(
        telemetry.PosthogEventModel(
            name=SculptorPosthogEvent.AGENT_TASK_RUNNING_IN_ENVIRONMENT,
            component=ProductComponent.AGENT_TASK,
            task_id=str(task.object_id),
        )
    )
    # state: these variables are changed as the agent runs
    shutdown_started_at: float | None = None
    # we process the user input messages one at a time
    # there are other messages from the user besides PersistentUserMessage, but the other ones are control flow
    # and have nothing to do with snapshotting
    user_input_message_being_processed: PersistentUserMessage | None = None
    queued_user_input_messages: list[PersistentUserMessageUnion] = list(re_queued_messages)
    # track the last message that we handled
    last_processed_input_message_id: AgentMessageID | None = task_state.last_processed_message_id
    # is set below from old messages
    last_user_chat_message_id: AgentMessageID | None = None
    # track the full history of persistent messages we've seen
    persistent_message_history: list[PersistentUserMessage | PersistentAgentMessage] = []
    # has the agent produced at least one token of user-visible output yet?
    received_first_token_from_agent: bool = False
    # TODO(59a2e379-4304-425f-9ce8-75fd49d262a1): load this from devcontainer.json *when we start the task* and stick it into the task_data
    #  then read it from there
    root_data_path = AGENT_DATA_PATH
    # this handles the loading, running, stopping, and restarting of all checks
    check_controller = CheckProcessController(
        checks_info=InfoFromSculptorForChecks(
            task_id=task.object_id,
            project_id=task.project_id,
            source_branch=task_data.initial_branch,
            is_codex=isinstance(task_data.agent_config, CodexAgentConfig),
        ),
        environment=environment,
        services=services,
        root_data_path=root_data_path,
    )
    # we need to update this mapping whenever local sync makes a new snapshot as well
    # technically the input can be None if we are snapshotting before the first message is sent
    # which can happen if you try to update the system prompt before sending any messages
    snapshot_by_user_chat_message_id: dict[AgentMessageID | None, ImageTypes] = {}

    with log_runtime("run_agent_in_environment pre-processing"):
        # figure out what command we need to run (eg, which agent to invoke)
        in_testing = settings.TESTING.INTEGRATION_ENABLED
        with services.data_model_service.open_transaction(RequestID()) as transaction:
            # pyre-fixme[16]: get_all_tasks is only implemented by TaskAndModelTransaction
            all_tasks = transaction.get_all_tasks()
        snapshot_path = _get_snapshot_by_task(task, all_tasks, settings.TESTING.SNAPSHOT_PATH)
        agent_wrapper = _get_agent_wrapper(
            task_data=task_data,
            task_state=task_state,
            environment=environment,
            project=project,
            config_service=services.config_service,
            task_id=task.object_id,
            in_testing=in_testing,
            snapshot_path=snapshot_path,
        )
        secrets = services.config_service.get_user_secrets(task_data.available_secrets)
        # assert anthropic_credentials is not None
        # Start agent
        agent_wrapper.start(secrets, lambda: services.config_service.get_credentials())
        if on_agent_started is not None:
            on_agent_started()

        # make sure that we've synced anything that happened previously
        # this ensures that we reach a consistent state once the task has been resumed
        with services.data_model_service.open_task_transaction() as transaction:
            all_messages = services.task_service.get_saved_messages_for_task(task.object_id, transaction)

        # we need to replay the messages to do a variety of things
        persistent_user_message_by_id: dict[AgentMessageID, PersistentUserMessageUnion] = {}
        # one of those things is to figure out what the last user chat message was that we *started* processing
        # this is in case we never *finished* processing it, so that the agent can resume from where it left off
        initial_in_flight_user_chat_message_id: AgentMessageID | None = None
        for message in all_messages:
            # just remember the last chat message from the user (that the agent started processing)
            if isinstance(message, RequestStartedAgentMessage):
                persistent_message = persistent_user_message_by_id.get(message.request_id)
                if persistent_message is not None:
                    if isinstance(persistent_message, ChatInputUserMessage):
                        last_user_chat_message_id = message.request_id
                        initial_in_flight_user_chat_message_id = message.request_id
                    # add the user message to the history as well
                    persistent_message_history.append(persistent_user_message_by_id[message.request_id])
            if isinstance(message, PersistentRequestCompleteAgentMessage):
                if message.request_id == initial_in_flight_user_chat_message_id:
                    # ok, except it doesn't count if this was from a sigterm
                    was_killed = _get_is_killed_request(message)
                    if not was_killed:
                        initial_in_flight_user_chat_message_id = None
            # build up the mapping of user input message IDs to snapshots so that we can properly re-run checks
            if isinstance(message, AgentSnapshotRunnerMessage):
                if message.image is not None:
                    snapshot_by_user_chat_message_id[message.for_user_message_id] = message.image
            # used above so that we can figure out which user messages started being processed so far
            if isinstance(message, PersistentUserMessage):
                persistent_user_message_by_id[message.message_id] = message
            # remember all messages that have been emitted so far by the agent
            if isinstance(message, PersistentAgentMessage):
                was_killed = _get_is_killed_request(message)
                if not was_killed:
                    persistent_message_history.append(message)
        # if we didn't observe any responses from the agent, reset our initial in-flight message ID
        # this will cause us to resend the message to the agent (but there's no visible wasted work, so that should be ok)
        # note that this whole thing is a little bit racey -- we may not have received some messages that the agent thinks that it sent to us
        initial_in_flight_user_chat_message_id = None

        logger.debug("Initial in-flight user chat message ID: {}", initial_in_flight_user_chat_message_id)
        logger.debug("Last processed message id:              {}", task_state.last_processed_message_id)

    # starts loading any previous check data in a thread, handles cleaning up any check process threads
    # TODO: a key of snapshot_by_user_chat_message_id can be None. should start accept that?
    with check_controller.start(snapshot_by_user_chat_message_id, task.parent_task_id):  # pyre-fixme[6]
        check_controller.run_pending_checks(
            snapshot_by_user_input_message_id=snapshot_by_user_chat_message_id,
            is_next_message_in_progress=False,
            secrets=secrets,
        )

        # track the last time we had a local sync change that modified the filesystem
        last_local_sync_change_time: float | None = None
        output_location_by_check: dict[Check, CheckRunOutputLocation] | None = None
        # this is the core event loop for the agent.
        exit_code: int | None

        # if we start with an existing queue, send the first message
        if len(queued_user_input_messages) > 0:
            user_input_message_being_processed = _send_user_input_message(
                agent_wrapper,
                queued_user_input_messages.pop(0),
                check_controller,
                initial_in_flight_user_chat_message_id,
                services,
                task.object_id,
            )
        while True:
            try:
                environment.raise_if_not_healthy()
            except EnvironmentNotHealthy:
                # A global shutdown means Ctrl+C was sent.
                # In turn, that means that processes inside the docker container got a SIGTERM and have shut down already.
                # In this situation, we don't mind that the environment is unhealthy.
                # HACK: sleep for a little while to make sure that the shutdown event is properly noticed.
                time.sleep(0.2)
                if not GLOBAL_SHUTDOWN_EVENT.is_set():
                    raise
            # if we have been trying to shut down for too long, it is time for more drastic measures.
            if shutdown_started_at is not None and time.monotonic() - shutdown_started_at > _MAX_SOFT_SHUTDOWN_SECONDS:
                # might as well go see where it is hung if we can...
                kill_time_start = time.monotonic()
                try:
                    agent_wrapper.terminate(_MAX_HARD_SHUTDOWN_SECONDS)
                    remaining_shutdown_time = time.monotonic() - kill_time_start
                    if remaining_shutdown_time < 0:
                        raise UncleanTerminationAgentError("No time left to call wait() on agent wrapper")
                    exit_code = agent_wrapper.wait(remaining_shutdown_time)
                except (UncleanTerminationAgentError, WaitTimeoutAgentError) as e:
                    raise AgentHardKilled(
                        f"Agent took longer than {_MAX_SOFT_SHUTDOWN_SECONDS + _MAX_HARD_SHUTDOWN_SECONDS} seconds to shut down"
                    ) from e
                else:
                    is_dirty = _is_dirty_given_either(user_input_message_being_processed, last_local_sync_change_time)
                    return _handle_completed_agent(
                        agent_wrapper,
                        exit_code,
                        task,
                        task_state,
                        project,
                        environment,
                        services,
                        is_dirty,
                        last_user_chat_message_id,
                        settings,
                    )

            # if the process has completed
            exit_code = agent_wrapper.poll()
            if exit_code is not None:
                is_dirty = _is_dirty_given_either(user_input_message_being_processed, last_local_sync_change_time)
                return _handle_completed_agent(
                    agent_wrapper,
                    exit_code,
                    task,
                    task_state,
                    project,
                    environment,
                    services,
                    is_dirty,
                    last_user_chat_message_id,
                    settings,
                )

            # transfer any output from the process
            new_messages = agent_wrapper.pop_messages()
            callbacks = sync_artifacts(
                new_messages, task, project, environment, services.git_repo_service, services.task_service
            )

            # save the new messages off
            _save_messages(task.object_id, services, new_messages, callbacks)

            # add any persistent messages to our history
            for message in new_messages:
                if isinstance(message, PersistentAgentMessage):
                    if not received_first_token_from_agent:
                        telemetry.emit_posthog_event(
                            telemetry.PosthogEventModel(
                                name=SculptorPosthogEvent.AGENT_TASK_RECEIVED_FIRST_TOKEN_FROM_AGENT,
                                component=ProductComponent.AGENT_TASK,
                                task_id=str(task.object_id),
                            )
                        )
                        received_first_token_from_agent = True
                    killed_exit_code = _get_is_killed_request(message)
                    if killed_exit_code:
                        logger.debug("Agent seems like it exited, returning")
                        is_dirty = _is_dirty_given_either(
                            user_input_message_being_processed, last_local_sync_change_time
                        )
                        return _handle_completed_agent(
                            agent_wrapper,
                            killed_exit_code,
                            task,
                            task_state,
                            project,
                            environment,
                            services,
                            is_dirty,
                            last_user_chat_message_id,
                            settings,
                        )
                    else:
                        persistent_message_history.append(message)

            # check if our currently pending user input message has completed
            # this causes "settling", eg, we want to snapshot the state
            is_settled = False
            is_agent_turn_finished = False
            if user_input_message_being_processed is not None:
                for another_message in new_messages:
                    if isinstance(another_message, PersistentRequestCompleteAgentMessage):
                        if another_message.request_id == user_input_message_being_processed.message_id:
                            logger.trace("is_settled because of user message")
                            is_settled = True
                            is_agent_turn_finished = True
                            # we reset this here because it only matters post-agent message response
                            last_local_sync_change_time = None

            # the other way that "settling" can happen is if we're not even processing a message,
            # but local sync ended up causing our state to change
            if last_local_sync_change_time is not None and user_input_message_being_processed is None:
                # we only consider ourselves as "settled" if it's been long enough since we saw a local sync update
                seconds_since_local_sync = time.monotonic() - last_local_sync_change_time
                if seconds_since_local_sync > _LOCAL_SYNC_CHANGE_DEBOUNCE_SECONDS:
                    logger.trace("is_settled because of local sync activity")
                    is_settled = True
                    last_local_sync_change_time = None

            # if the process is settled (all messages have been processed), we can snapshot the state
            if is_settled:
                # update these tracking variables if we've settled because the message finished
                if user_input_message_being_processed is not None:
                    last_processed_input_message_id = user_input_message_being_processed.message_id
                    if isinstance(user_input_message_being_processed, ChatInputUserMessage):
                        last_user_chat_message_id = user_input_message_being_processed.message_id

                output_location_by_check = check_controller.define_checks_for_turn(
                    current_user_message_id=last_user_chat_message_id,
                    persistent_message_history=persistent_message_history,
                )

                # this is where we can actually snapshot the filesystem
                prev_task_state = task_state
                task_state = _update_task_state(
                    # TODO: these both can have a key that's None. should _update_task_state accept that?
                    last_processed_input_message_id=last_processed_input_message_id,  # pyre-fixme[6]
                    last_user_chat_message_id=last_user_chat_message_id,  # pyre-fixme[6]
                    environment=environment,
                    task_id=task.object_id,
                    task_state=task_state,
                    services=services,
                    is_agent_turn_finished=is_agent_turn_finished,
                    settings=settings,
                )
                is_new_snapshot = prev_task_state.image != task_state.image
                # update our mapping so that we can run checks against it in the future
                # last_user_chat_message_id can only be None temporarily (until silly old user_setup.sh goes away)
                if is_new_snapshot and last_user_chat_message_id is not None and task_state.image is not None:
                    snapshot_by_user_chat_message_id[last_user_chat_message_id] = task_state.image
                # send the next message (if there is one waiting)
                if len(queued_user_input_messages) == 0:
                    user_input_message_being_processed = None
                else:
                    user_input_message_being_processed = _send_user_input_message(
                        agent_wrapper,
                        queued_user_input_messages.pop(0),
                        check_controller,
                        initial_in_flight_user_chat_message_id,
                        services,
                        task.object_id,
                    )

            # get any new user message(s)
            user_messages = _get_input_messages(input_message_queue, max_wait_time=_POLL_SECONDS)

            # If the program is shutting down, simply stop the thread.
            if environment.concurrency_group.is_shutting_down():
                # At the moment, stopping implies pausing.
                raise AgentPaused()

            # if we observed a shutdown event, send a stop message to the agent and start the timer
            if shutdown_started_at is None and shutdown_event.is_set():
                logger.debug("Shutdown event observed, sending stop message to agent.")
                agent_wrapper.push_message(StopAgentUserMessage())
                shutdown_started_at = time.monotonic()

            # send the user messages to the process
            is_filesystem_modified_by_local_sync = False
            for message in user_messages:
                if isinstance(message, MessageFeedbackUserMessage):
                    persistent_message_history.append(message)
                # handle input chat user messages one at a time
                elif isinstance(message, PersistentUserMessage):
                    # If the last message is None the first user message hasn't finished processing, but prior to this processing being completed, changes via local sync can result in the check definition method being called.
                    # If we don't have this code, that check definition will fail because there isn't a user message id to associate the checks with.
                    # This approach is a bit weird though because checks prior to the first turn will be lumped in with the ones associated with the first turn, but this seems like the most reasonable approach.
                    if isinstance(message, ChatInputUserMessage) and last_user_chat_message_id is None:
                        last_user_chat_message_id = message.message_id
                    if user_input_message_being_processed is None:
                        user_input_message_being_processed = _send_user_input_message(
                            agent_wrapper,
                            message,
                            check_controller,
                            initial_in_flight_user_chat_message_id,
                            services,
                            task.object_id,
                        )
                    else:
                        queued_user_input_messages.append(message)
                    # add it to the conversation history
                    persistent_message_history.append(message)
                # let the check controller handle its own messages
                elif check_controller.services.settings.IS_CHECKS_ENABLED and isinstance(
                    message, CheckControlUserMessage
                ):
                    emit_posthog_event_for_user_message(task_id=task.object_id, message=message)
                    check_controller.handle_message(
                        message,
                        secrets=secrets,
                        snapshot_by_user_input_message_id=snapshot_by_user_chat_message_id,
                    )
                # otherwise, simply forward the message to the agent and let it figure it out
                elif isinstance(message, (LocalSyncUpdateCompletedMessage, ManualSyncMergeIntoAgentAttemptedMessage)):
                    # note whether we have seen a local sync message that indicates a change to the filesystem
                    is_filesystem_modified_by_local_sync = True
                    last_local_sync_change_time = time.monotonic()
                    logger.trace("local sync activity: {}", last_local_sync_change_time)
                    agent_wrapper.push_message(message)
                else:
                    agent_wrapper.push_message(message)

            # if local sync caused a change to the filesystem, we need to persist the environment
            #  this is a no-op for Docker and the local filesystem, but modal needs to be notified so that it can snapshot.
            if is_filesystem_modified_by_local_sync:
                environment.persist(services.config_service.get_user_config(), task.object_id, settings)

            if is_agent_turn_finished or is_filesystem_modified_by_local_sync:
                if not is_settled:
                    output_location_by_check = check_controller.define_checks_for_turn(
                        current_user_message_id=last_user_chat_message_id,
                        persistent_message_history=persistent_message_history,
                    )
                if output_location_by_check is not None:
                    check_controller.run_checks_for_turn(
                        output_location_by_check=output_location_by_check,
                        is_agent_turn_finished=is_agent_turn_finished,
                        is_next_message_in_progress=user_input_message_being_processed is not None,
                        snapshot=task_state.image,
                        secrets=secrets,
                    )


def _get_is_killed_request(message: Message) -> int:
    if isinstance(message, RequestStoppedAgentMessage):
        causal_error = message.error.construct_instance()
        # sigterm and signint
        if isinstance(causal_error, AgentClientError) and causal_error.exit_code in (
            AGENT_EXIT_CODE_FROM_SIGTERM,
            AGENT_EXIT_CODE_FROM_SIGINT,
        ):
            return causal_error.exit_code
    return 0


InputMessageT = TypeVar(
    "InputMessageT", bound=UserMessageUnion | SystemMessageUnion | ResumeAgentResponseRunnerMessage
)


def _send_user_input_message(
    agent_wrapper: Agent,
    message: InputMessageT,
    check_controller: CheckProcessController,
    initial_in_flight_user_chat_message_id: AgentMessageID | None,
    services: ServiceCollectionForTask,
    task_id: TaskID,
) -> InputMessageT:
    user_input_message_being_processed = message
    if isinstance(message, ChatInputUserMessage):
        check_controller.on_persistent_user_message()
    # if this message was one that we left off on last time,
    # we need to send a special "Please pick up where you left off" message instead of the normal message
    # this allows the agent to use whatever in-flight response it had
    # (which prevents the user from losing a bunch of work if they shut down or sculptor crashed)
    # this is especially important as agents start to have much longer response times
    if user_input_message_being_processed.message_id == initial_in_flight_user_chat_message_id and isinstance(
        user_input_message_being_processed, ChatInputUserMessage
    ):
        resume_message = ResumeAgentResponseRunnerMessage(
            for_user_message_id=user_input_message_being_processed.message_id,
            model_name=user_input_message_being_processed.model_name,
        )
        with services.data_model_service.open_task_transaction() as transaction:
            services.task_service.create_message(message, task_id, transaction)
        agent_wrapper.push_message(resume_message)
    else:
        agent_wrapper.push_message(user_input_message_being_processed)
    return user_input_message_being_processed


def _on_exception(
    e: Exception,
    task_id: TaskID,
    user_reference: UserReference,
    services: ServiceCollectionForTask,
    shutdown_event: ReadOnlyEvent,
) -> None:
    # For simple exceptions that that bubble up wrapped in a ConcurrencyExceptionGroup, unwrap them.
    if isinstance(e, ConcurrencyExceptionGroup) and len(e.exceptions) == 1:
        e = e.exceptions[0]

    # this "exception" is expected in the sense that it was the user telling the task to stop
    # so it doesn't count as success
    if isinstance(e, CancelledByEventError) and (shutdown_event.is_set() or GLOBAL_SHUTDOWN_EVENT.is_set()):
        # Looks like the user cancelled the task even before the agent started.
        raise UserPausedTaskError() from e
    if isinstance(e, AgentPaused):
        raise UserPausedTaskError() from e
    if isinstance(e, AgentShutdownCleanly):
        raise UserStoppedTaskError() from e

    # if the agent has failed, we should notify the user
    is_expected = isinstance(e, ExpectedError)
    if is_expected:
        if "Cannot connect to the Docker daemon" in str(e):
            send_exception_to_posthog(
                SculptorPosthogEvent.AGENT_RUNNER_FAILED_BECAUSE_DOCKER_IS_DOWN,
                e,
                include_traceback=True,
            )
        else:
            log_exception(
                exc=e,
                message="Agent runner failed with expected error",
                priority=ExceptionPriority.LOW_PRIORITY,
            )
    else:
        if is_live_debugging():
            raise
        log_exception(
            exc=e,
            message="Agent runner failed with unexpected error",
            priority=ExceptionPriority.MEDIUM_PRIORITY,
        )

    error = e

    # send a message to the user
    is_worth_notifying = True
    full_output_url = _get_full_output_url(task_id, services.task_service)
    agent_error_message: PersistentRunnerMessageUnion
    match error:
        case AgentHardKilled():
            agent_error_message = KilledAgentRunnerMessage(
                message_id=AgentMessageID(), full_output_url=full_output_url
            )
            # not worth notifying the user about this, they told it to stop
            is_worth_notifying = False
        case AgentCrashed():
            agent_error_message = AgentCrashedRunnerMessage(
                message_id=AgentMessageID(),
                exit_code=error.exit_code,
                full_output_url=full_output_url,
                error=SerializedException.build(error),
            )
        # TODO: we could transparently retry on these errors (at a lower level)
        #  we would still need to handle them here, but it would only be for repeated failures
        case EnvironmentFailure():
            agent_error_message = EnvironmentCrashedRunnerMessage(
                message_id=AgentMessageID(),
                error=SerializedException.build(error),
                full_output_url=full_output_url,
            )
        case _:
            agent_error_message = UnexpectedErrorRunnerMessage(
                message_id=AgentMessageID(),
                error=SerializedException.build(error),
                full_output_url=full_output_url,
            )

    def on_transaction(t: DataModelTransaction) -> None:
        services.task_service.create_message(agent_error_message, task_id, t)

        # and send a notification to the user if necessary
        if is_worth_notifying:
            task_row = services.task_service.get_task(task_id, t)
            assert task_row is not None
            t.insert_notification(
                Notification(
                    user_reference=user_reference,
                    object_id=NotificationID(),
                    message="Agent failed.",
                    importance=NotificationImportance.TIME_SENSITIVE,
                    task_id=task_row.object_id,
                ),
            )

    # raising will ensure that unexpected Exceptions are logged, and that the task is marked as failed
    raise AgentTaskFailure(transaction_callback=on_transaction, is_user_notified=True)


def _get_snapshot_by_task(
    target_task: Task, all_tasks: tuple[Task, ...], snapshot_path: str | None = None
) -> Path | None:
    if snapshot_path is None:
        return None
    for i, task in enumerate([task for task in all_tasks if isinstance(task.input_data, AgentTaskInputsV1)]):
        if task.object_id == target_task.object_id:
            return Path(snapshot_path) / f"task_{i}.llm_cache_db"
    assert False, f"Could not find snapshot for task {target_task}"


def _get_agent_wrapper(
    task_data: AgentTaskInputsV1,
    task_state: AgentTaskStateV1,
    environment: Environment,
    project: Project,
    config_service: ConfigService,
    task_id: TaskID,
    in_testing: bool = False,
    snapshot_path: Path | None = None,
) -> Agent:
    logger.info("Discriminating agent wrapper")
    agent_config = task_data.agent_config
    if isinstance(agent_config, HelloAgentConfig):
        return HelloAgent(
            config=agent_config,
            environment=environment,
            source_branch="",
            task_branch="",
            task_id=task_id,
            system_prompt="",
        )
    elif isinstance(agent_config, ClaudeCodeSDKAgentConfig):
        return ClaudeCodeSDKAgent(
            config=agent_config,
            environment=environment,
            project=project,
            task_id=task_id,
            in_testing=in_testing,
            snapshot_path=snapshot_path,
            source_branch=task_data.initial_branch,
            # TODO: the type checker thinks this could be None, but it actually can't be because we initialize the branch before this.
            # maybe we could use a different type for partially and fully initialized task states?
            task_branch=task_state.branch_name,  # pyre-fixme[6]
            system_prompt=task_data.system_prompt or "",
        )
    elif isinstance(agent_config, CodexAgentConfig):
        logger.info("Selected codex agent")
        return CodexAgent(
            config=agent_config,
            environment=environment,
            task_id=task_id,
            in_testing=in_testing,
            snapshot_path=snapshot_path,
            source_branch=task_data.initial_branch,
            # TODO: the type checker thinks this could be None, but it actually can't be because we initialize the branch before this.
            # maybe we could use a different type for partially and fully initialized task states?
            task_branch=task_state.branch_name,  # pyre-fixme[6]
            system_prompt=task_data.system_prompt or "",
        )
    raise UnknownAgentConfigError(f"Unknown agent config: {agent_config}")


def _handle_completed_agent(
    agent_wrapper: Agent,
    exit_code: int,
    task: Task,
    task_state: AgentTaskStateV1,
    project: Project,
    environment: Environment,
    services: ServiceCollectionForTask,
    is_dirty: bool,
    last_user_chat_message_id: AgentMessageID | None,
    settings: SculptorSettings,
) -> Callable[[DataModelTransaction], None]:
    """
    Call this once the agent has finished with an exit code.

    Raises the appropriate errors and returns a callback to handle the success case.
    """

    # get any final messages
    new_messages = agent_wrapper.pop_messages()

    # and sync any necessary artifacts
    callbacks = sync_artifacts(
        new_messages, task, project, environment, services.git_repo_service, services.task_service
    )

    _save_messages(task.object_id, services, new_messages, callbacks)

    agent_wrapper.wait(10)  # NOTE: if the agent has hit an exception, we will raise it here

    # if dirty, we need to snapshot the environment before shutting down
    # this is only really necessary so that, if we are upgrading sculptor, the user will be able to resume
    # without losing any of their local sync'd work
    if is_dirty and last_user_chat_message_id is not None:
        if exit_code in (AGENT_EXIT_CODE_CLEAN_SHUTDOWN_ON_INTERRUPT, 0):
            snapshot_image = None
            snapshot_failure_exception: SerializedException | None = None
            try:
                snapshot_image = environment.snapshot(
                    services.config_service.get_user_config(), task.object_id, settings
                )
            except EnvironmentRestartRequired as e:
                logger.debug("Ignoring 'restart required' from task because we're already stopping")
                snapshot_image = e.image
            except EnvironmentFailure as e:
                if "Environment needs to be restarted to apply changes to image" in str(e):
                    send_exception_to_posthog(
                        SculptorPosthogEvent.FAILED_TO_SNAPSHOT_IMAGE_DURING_SHUTDOWN,
                        e,
                        include_traceback=True,
                    )
                else:
                    log_exception(
                        e, "Failed to snapshot image during shutdown", priority=ExceptionPriority.LOW_PRIORITY
                    )
                snapshot_failure_exception = SerializedException.build(e)
            finally:
                with services.data_model_service.open_task_transaction() as transaction:
                    if snapshot_image is not None:
                        snapshot_message = AgentSnapshotRunnerMessage(
                            message_id=AgentMessageID(),
                            image=snapshot_image,
                            for_user_message_id=last_user_chat_message_id,
                            is_settled=False,
                        )
                        services.task_service.create_message(snapshot_message, task.object_id, transaction)
                        # Update the task state with the new snapshot image
                        mutable_task_state = evolver(task_state)
                        assign(mutable_task_state.image, lambda: snapshot_image)
                        updated_task_state = chill(mutable_task_state)
                        # Fetch task from DB to ensure we have the latest version before updating
                        # (the in-memory task object may be stale)
                        task_row = transaction.get_task(task.object_id)
                        assert task_row is not None
                        task_row = task_row.evolve(task_row.ref().current_state, updated_task_state.model_dump())
                        transaction.upsert_task(task_row)
                    else:
                        snapshot_failure_message = AgentSnapshotFailureRunnerMessage(
                            message_id=AgentMessageID(),
                            for_user_message_id=last_user_chat_message_id,
                            is_settled=False,
                            failure_reason=snapshot_failure_exception,
                        )
                        services.task_service.create_message(snapshot_failure_message, task.object_id, transaction)

    # if we expected to shut down, and we observed the correct exit code, fine
    if exit_code in (
        AGENT_EXIT_CODE_CLEAN_SHUTDOWN_ON_INTERRUPT,
        AGENT_EXIT_CODE_FROM_SIGINT,
        AGENT_EXIT_CODE_FROM_SIGTERM,
    ):
        raise AgentPaused()
    # if the process was successful, return
    elif exit_code == 0:
        return _on_success(task.object_id, task.user_reference, services.task_service, callbacks)

    # if the process failed
    else:
        raise AgentCrashed(f"Agent died with exit code {exit_code}", exit_code=exit_code)


def _get_full_output_url(task_id: TaskID, task_service: TaskService) -> AnyUrl | None:
    output_url = task_service.get_artifact_file_url(task_id, TMUX_OUTPUT_ARTIFACT_NAME)
    if Path(str(output_url).replace("file://", "")).exists():
        return output_url
    else:
        return None


def _on_success(
    task_id: TaskID, user_reference: UserReference, task_service: TaskService, callbacks: tuple[Callable[[], Any], ...]
) -> Callable[[DataModelTransaction], None]:
    logger.debug("process finished successfully")

    def on_transaction(t: DataModelTransaction) -> None:
        full_output_url = _get_full_output_url(task_id, task_service)

        task_row = task_service.get_task(task_id, t)
        assert task_row is not None
        t.insert_notification(
            Notification(
                user_reference=user_reference,
                object_id=NotificationID(),
                message="Finished running agent.",
                importance=NotificationImportance.ACTIVE,
                task_id=task_row.object_id,
            )
        )
        for callback in callbacks:
            t.add_callback(callback)

    return on_transaction


def sync_artifacts(
    new_messages: Sequence[Message],
    task: Task,
    project: Project,
    environment: Environment,
    git_repo_service: GitRepoService,
    task_service: TaskService,
) -> tuple[Callable[[], Any], ...]:
    # it is important that we pull the messages first --
    # this way we can guarantee that the other artifacts have been written
    # (as long as the agent wrapper does the reverse, not writing the messages until everything else is flushed)
    artifacts_to_sync = [x.artifact for x in new_messages if isinstance(x, UpdatedArtifactAgentMessage)]
    # this is used to ensure that we don't sync the same artifact multiple times
    artifact_names_seen = set()
    callbacks: list[Callable[[], Any]] = []
    for artifact in reversed(artifacts_to_sync):
        if artifact.name in artifact_names_seen:
            logger.trace("skipping artifact {} as it has already been synced", artifact.name)
            continue
        else:
            artifact_names_seen.add(artifact.name)
        match artifact:
            case FileAgentArtifact():
                if artifact.url is None:
                    logger.debug("skipping artifact {} as it has no url", artifact.name)
                    continue
                logger.debug("syncing artifact: {}", artifact.url)
                remote_path = str(artifact.url).replace("file://", "")
                if not environment.exists(remote_path):
                    err = Exception(f"Artifact {artifact.name} does not exist at {remote_path}")
                    # TODO: in theory, we should not hit this code path, but let's not make it a hard error just in case
                    log_exception(err, "Artifact does not exist", priority=ExceptionPriority.MEDIUM_PRIORITY)
                    if is_live_debugging():
                        raise err
                    continue
                contents = environment.read_file(remote_path)
                callbacks.append(
                    lambda name=artifact.name, data=contents: task_service.set_artifact_file_data(
                        task.object_id, name, data
                    )
                )
                logger.debug("synced file artifact: {}", remote_path)
            case _ as unreachable:
                assert_never(unreachable)

    return tuple(callbacks)


def _update_task_state(
    last_processed_input_message_id: AgentMessageID,
    last_user_chat_message_id: AgentMessageID,
    environment: Environment,
    task_id: TaskID,
    task_state: AgentTaskStateV1,
    services: ServiceCollectionForTask,
    is_agent_turn_finished: bool,
    settings: SculptorSettings,
) -> AgentTaskStateV1:
    """Update the task state with the message ID that was processed successfully."""
    if is_agent_turn_finished and task_state.last_processed_message_id == last_processed_input_message_id:
        return task_state

    # send a message that we are starting the snapshot (makes it easier to see if we get stuck here)
    with services.data_model_service.open_task_transaction() as transaction:
        start_snapshot_message = StartedAgentSnapshotRunnerMessage(for_user_message_id=last_user_chat_message_id)
        services.task_service.create_message(start_snapshot_message, task_id, transaction)

    snapshot_image: ImageTypes | None = None

    restart_exception: EnvironmentRestartRequired | None = None
    snapshot_failure_exception: SerializedException | None = None
    if settings.IS_SNAPSHOTTING_ENABLED:
        logger.trace("IS_SNAPSHOTTING_ENABLED=true, snapshotting")
        try:
            snapshot_image = environment.snapshot(services.config_service.get_user_config(), task_id, settings)
        except EnvironmentRestartRequired as e:
            # sigh. We made a snapshot that is too large, so we need to restart this container.
            restart_exception = e
            snapshot_image = e.image
        except EnvironmentFailure as e:
            log_exception(e, "Failed to snapshot image during agent run", priority=ExceptionPriority.LOW_PRIORITY)
            with services.data_model_service.open_task_transaction() as transaction:
                serialized_error = SerializedException.build(e) if e is not None else None
                warning_message = WarningRunnerMessage(
                    message="Failed to snapshot image - this means your latest changes may not be saved.",
                    error=serialized_error,
                )
                services.task_service.create_message(warning_message, task_id, transaction)
            snapshot_failure_exception = SerializedException.build(e)
            snapshot_image = None

        logger.debug("Finished snapshotting image: {}", snapshot_image)
    else:
        logger.debug("IS_SNAPSHOTTING_ENABLED=false, skipping snapshotting")

    logger.debug(
        f"Updating last processed message ID from {task_state.last_processed_message_id} to {last_processed_input_message_id}"
    )
    with services.data_model_service.open_task_transaction() as transaction:
        if snapshot_image is not None:
            snapshot_message = AgentSnapshotRunnerMessage(
                message_id=AgentMessageID(),
                image=snapshot_image,
                for_user_message_id=last_user_chat_message_id,
                is_settled=is_agent_turn_finished,
            )
            services.task_service.create_message(snapshot_message, task_id, transaction)
        else:
            snapshot_failure_message = AgentSnapshotFailureRunnerMessage(
                message_id=AgentMessageID(),
                for_user_message_id=last_user_chat_message_id,
                is_settled=is_agent_turn_finished,
                failure_reason=snapshot_failure_exception,
            )
            services.task_service.create_message(snapshot_failure_message, task_id, transaction)
        task_row = transaction.get_task(task_id)
        mutable_task_state = evolver(task_state)
        assign(mutable_task_state.last_processed_message_id, lambda: last_processed_input_message_id)
        if snapshot_image is not None:
            assign(mutable_task_state.image, lambda: snapshot_image)
        # only keep the environment ID if we successfully made a snapshot
        if restart_exception is None:
            assign(mutable_task_state.environment_id, lambda: environment.environment_id)
        else:
            assign(mutable_task_state.environment_id, lambda: None)
        updated_task_state = chill(mutable_task_state)
        assert task_row is not None
        task_row = task_row.evolve(task_row.ref().current_state, updated_task_state.model_dump())
        _task_row = transaction.upsert_task(task_row)

    # if we ran into an error, we need to raise it now that we've saved the snapshot message
    if restart_exception is not None:
        # TODO: technically this could be done out of band to increase performance, but we can wait until later to optimize that
        # destroy the environment, since it is no longer valid
        logger.debug("time before restart: {}", time.monotonic())
        environment.destroy(is_killing=True)
        # then raise this exception so that we restart the container from the fresh image
        raise restart_exception

    return updated_task_state


def _save_messages(
    task_id: TaskID,
    services: ServiceCollectionForTask,
    new_messages: Sequence[MessageTypes],
    callbacks: tuple[Callable[[], Any], ...],
) -> None:
    if not new_messages and not callbacks:
        return

    with services.data_model_service.open_task_transaction() as transaction:
        for message in new_messages:
            services.task_service.create_message(message, task_id, transaction)
        for callback in callbacks:
            transaction.add_callback(callback)


MessageT = TypeVar("MessageT")


def _get_input_messages(message_queue: Queue[MessageT], max_wait_time: float) -> list[MessageT]:
    """
    Get user messages from the queue, waiting for up to `max_wait_time` seconds.

    Returns a list of messages.
    """
    messages = []
    while message_queue.qsize() > 0:
        message = message_queue.get(block=False)
        messages.append(message)
    try:
        message = message_queue.get(timeout=max_wait_time)
    except Empty:
        pass
    else:
        messages.append(message)
    return messages


def _is_dirty_given_either(
    user_input_message_being_processed: Any | None, last_local_sync_change_time: float | None
) -> bool:
    snapshot_info = ""
    if user_input_message_being_processed is not None:
        snapshot_info = f"{(user_input_message_being_processed is not None)=}"
    elif last_local_sync_change_time:
        snapshot_info = f"none taken since {last_local_sync_change_time=}"
    else:
        return False
    logger.debug("agent is_dirty and wants to snapshot: {}", snapshot_info)
    return True
