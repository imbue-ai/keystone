from __future__ import annotations

import json
import shlex
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from typing import Callable
from typing import Generator
from typing import Mapping

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.gitlab_management import GITLAB_TOKEN_NAME
from imbue_core.processes.local_process import RunningProcess
from imbue_core.sculptor.state.messages import Message
from imbue_core.sculptor.telemetry import send_exception_to_posthog
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.secrets_utils import Secret
from sculptor.agents.default.artifact_creation import get_file_artifact_messages
from sculptor.agents.default.claude_code_sdk.utils import get_state_file_contents
from sculptor.agents.default.constants import DEFAULT_WAIT_TIMEOUT
from sculptor.agents.default.constants import GITLAB_PROJECT_URL_STATE_FILE
from sculptor.agents.default.constants import GITLAB_TOKEN_STATE_FILE
from sculptor.agents.default.constants import REMOVED_MESSAGE_IDS_STATE_FILE
from sculptor.agents.default.posthog_utils import emit_posthog_event_for_user_message
from sculptor.agents.default.terminal_manager import TerminalManager
from sculptor.agents.default.utils import is_user_message
from sculptor.agents.default.utils import on_git_user_message
from sculptor.agents.default.utils import serialize_agent_wrapper_error
from sculptor.agents.default.utils import stream_token_and_cost_info
from sculptor.interfaces.agents.agent import Agent
from sculptor.interfaces.agents.agent import GitCommitAndPushUserMessage
from sculptor.interfaces.agents.agent import GitPullUserMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdateCompletedMessage
from sculptor.interfaces.agents.agent import ManualSyncMergeIntoAgentAttemptedMessage
from sculptor.interfaces.agents.agent import MessageFeedbackUserMessage
from sculptor.interfaces.agents.agent import MessageTypes
from sculptor.interfaces.agents.agent import RemoveQueuedMessageAgentMessage
from sculptor.interfaces.agents.agent import RemoveQueuedMessageUserMessage
from sculptor.interfaces.agents.agent import RequestStartedAgentMessage
from sculptor.interfaces.agents.agent import RequestSuccessAgentMessage
from sculptor.interfaces.agents.agent import SetUserConfigurationDataUserMessage
from sculptor.interfaces.agents.agent import StopAgentUserMessage
from sculptor.interfaces.agents.agent import UserMessageUnion
from sculptor.interfaces.agents.artifacts import ArtifactType
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_CLEAN_SHUTDOWN_ON_INTERRUPT
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_FROM_SIGINT
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_FROM_SIGTERM
from sculptor.interfaces.agents.errors import AgentClientError
from sculptor.interfaces.agents.errors import AgentTransientError
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import TTYD_SERVER_NAME
from sculptor.services.config_service.data_types import Credentials


class DefaultAgentWrapper(Agent):
    """
    The default class for all AgentWrappers. Holds common logic and fields between all agents and interacts with
    the agent runner to manage the inner agent.
    """

    environment: Environment
    task_id: TaskID
    in_testing: bool = False
    snapshot_path: Path | None = None
    _removed_message_ids: set[str] = PrivateAttr(default_factory=set)
    _secrets: dict[str, str | Secret] = PrivateAttr(default_factory=dict)
    _output_messages: Queue[Message] = PrivateAttr(default_factory=Queue)
    _exception: BaseException | None = PrivateAttr(default=None)
    _process: RunningProcess | None = PrivateAttr(default=None)
    _exit_code: int | None = PrivateAttr(default=None)
    _is_stopping: bool = PrivateAttr(default=False)
    _get_credentials: Callable[[], Credentials | None] | None = PrivateAttr(default=None)

    system_prompt: str
    source_branch: str
    task_branch: str

    _terminal_manager: TerminalManager | None = PrivateAttr(default=None)

    def start(
        self,
        secrets: Mapping[str, str | Secret],
        get_credentials: Callable[[], Credentials],
    ) -> None:
        # Load secrets
        self._secrets = dict(secrets)
        gitlab_token_from_state = get_state_file_contents(self.environment, GITLAB_TOKEN_STATE_FILE)
        if gitlab_token_from_state:
            self._secrets[GITLAB_TOKEN_NAME] = gitlab_token_from_state

        gitlab_url_from_state = get_state_file_contents(self.environment, GITLAB_PROJECT_URL_STATE_FILE)
        if gitlab_url_from_state:
            self._secrets["GITLAB_PROJECT_URL"] = gitlab_url_from_state
        self._get_credentials = get_credentials
        credentials = get_credentials()
        if self.in_testing:
            self._initialize_for_testing(credentials=credentials)
        self._refresh_settings()

        self._removed_message_ids = set(
            json.loads(get_state_file_contents(self.environment, REMOVED_MESSAGE_IDS_STATE_FILE) or "[]")
        )

        # Load cumulative token state
        stream_token_and_cost_info(
            environment=self.environment,
            source_branch=self.source_branch,
            output_message_queue=self._output_messages,
            task_id=self.task_id,
        )

        logger.info("Starting a default agent, updating artifacts")
        messages_to_send = get_file_artifact_messages(
            artifact_name=ArtifactType.DIFF,
            environment=self.environment,
            source_branch=self.source_branch,
            task_id=self.task_id,
        )
        for message in messages_to_send:
            self._output_messages.put(message)

        # Start the terminal manager to handle tmux and ttyd
        self._terminal_manager = TerminalManager(
            environment=self.environment,
            secrets=self._secrets,
            server_name=TTYD_SERVER_NAME,
            output_message_queue=self._output_messages,
        )

        # Perform any agent-specific initialization
        self._start()

    def pop_messages(self) -> list[MessageTypes]:
        new_logs = []
        while self._output_messages.qsize() > 0:
            message = self._output_messages.get_nowait()
            new_logs.append(message)
        return new_logs

    def push_message(self, message: Message) -> None:
        if is_user_message(message=message):
            emit_posthog_event_for_user_message(
                task_id=self.task_id,
                message=message,  # pyre-fixme[6]: this must be correct after above, but pyre doesn't recognize that
            )

        # Perform agent-specific message handling
        is_message_handled = self._push_message(message=message)
        if is_message_handled:
            return
        # If the message is not handled by the agent-specific message handling, perform generic handling
        # This is to prevent a message from being handled twice, which would split the message-handling logic
        match message:
            case RemoveQueuedMessageUserMessage():
                with self._handle_user_message(message):
                    self._removed_message_ids.add(message.target_message_id.suffix)
                    self.environment.write_file(
                        str(self.environment.get_state_path() / REMOVED_MESSAGE_IDS_STATE_FILE),
                        json.dumps(list(self._removed_message_ids)),
                    )
                    logger.info("Removed message id: {}", message.target_message_id)
                    self._output_messages.put(
                        RemoveQueuedMessageAgentMessage(removed_message_id=message.target_message_id)
                    )
            case LocalSyncUpdateCompletedMessage() | ManualSyncMergeIntoAgentAttemptedMessage():
                logger.info("Received local sync update message, updating artifacts")
                messages_to_send = get_file_artifact_messages(
                    artifact_name=ArtifactType.DIFF,
                    environment=self.environment,
                    source_branch=self.source_branch,
                    task_id=self.task_id,
                )
                for artifact_message in messages_to_send:
                    self._output_messages.put(artifact_message)
            # TODO: eventually just make this GitCommitUserMessage
            case GitCommitAndPushUserMessage():
                with self._handle_user_message(message):
                    commit_message = shlex.quote(message.commit_message)
                    task_branch = shlex.quote(self.task_branch)
                    commit_and_push_command_string = f"if [ \"$(git branch --show-current)\" != {task_branch} ]; then echo 'Error: Current branch is not {task_branch}'; exit 1; fi && git add . && git commit -m {commit_message} --trailer 'Co-authored-by: Sculptor <sculptor@imbue.com>'"
                    # when settings.IS_NEW_MANUAL_SYNC_ENABLED is true, we do not want to push
                    if message.is_pushing:
                        commit_and_push_command_string += " && git push sculptor"
                    on_git_user_message(
                        environment=self.environment,
                        command=["bash", "-c", commit_and_push_command_string],
                        source_branch=self.source_branch,
                        output_message_queue=self._output_messages,
                        task_id=self.task_id,
                    )
            case GitPullUserMessage():
                with self._handle_user_message(message):
                    on_git_user_message(
                        environment=self.environment,
                        command=["git", "pull"],
                        source_branch=self.source_branch,
                        output_message_queue=self._output_messages,
                        task_id=self.task_id,
                    )
            # FIXME: make an error message for local sync
            case StopAgentUserMessage():
                logger.info("Stopping agent")
                with self._handle_user_message(message):
                    self.terminate(DEFAULT_WAIT_TIMEOUT)
                    self._exit_code = AGENT_EXIT_CODE_CLEAN_SHUTDOWN_ON_INTERRUPT
                logger.info("Finished stopping agent")
            case SetUserConfigurationDataUserMessage():
                logger.info("User configuration message received")
                credentials = message.credentials
                if credentials is not None:
                    self._refresh_settings(credentials=credentials)
            case MessageFeedbackUserMessage():
                logger.info("Message feedback received for message {}", message.feedback_message_id)

    def poll(self) -> int | None:
        return self._exit_code

    def terminate(self, force_kill_seconds: float = 5.0) -> None:
        # Stop the terminal manager first
        if self._terminal_manager:
            self._terminal_manager.stop()

        self._terminate(force_kill_seconds=force_kill_seconds)

    def _start(self) -> None: ...

    def _push_message(self, message: Message) -> bool:
        return False

    def _terminate(self, force_kill_seconds: float) -> None: ...

    def _initialize_for_testing(self, credentials: Credentials) -> None: ...

    def _refresh_settings(self, credentials: Credentials | None = None) -> None: ...

    @property
    def concurrency_group(self) -> ConcurrencyGroup:
        return self.environment.concurrency_group

    @contextmanager
    def _handle_user_message(self, message: UserMessageUnion) -> Generator[None, None, None]:
        self._output_messages.put(
            RequestStartedAgentMessage(
                message_id=AgentMessageID(),
                request_id=message.message_id,
            )
        )
        try:
            yield
        # if it is a claude client error, let's report it and allow the user to retry or continue
        # otherwise, let's raise it out of the agent wrapper to be handled by the caller
        except AgentClientError as e:
            # if we got a sigterm, it's likely because we are shutting down in tests, so, probably worth bailing
            # also in this case it doesn't matter what kind of AgentClientError it is
            if e.exit_code == AGENT_EXIT_CODE_FROM_SIGTERM:
                is_stopping = True
                self._exit_code = AGENT_EXIT_CODE_FROM_SIGTERM
                logger.info("Received SIGTERM, likely due to shutdown, no need to log further")
            elif e.exit_code == AGENT_EXIT_CODE_FROM_SIGINT:
                is_stopping = True
                self._exit_code = AGENT_EXIT_CODE_FROM_SIGINT
                logger.info("Received SIGINT, likely due to controlled shutdown, no need to log further")
            # if it wasn't a shutdown, we need to know if it was transient (and hence expected)
            # so we can choose whether to record it in posthog or sentry
            elif isinstance(e, AgentTransientError):
                # TODO: this is set because otherwise is_stopping won't be defined, but it's unclear if it has the right semantics
                # (but it doesn't really matter since is_stopping is not actually used right now)
                is_stopping = False
                maybe_task_id = getattr(self, "task_id", None)
                send_exception_to_posthog(
                    SculptorPosthogEvent.CLAUDE_TRANSIENT_ERROR,
                    e,
                    component=ProductComponent.CLAUDE_CODE,
                    task_id=maybe_task_id,
                )
            else:
                # TODO: this is set because otherwise is_stopping won't be defined, but it's unclear if it has the right semantics
                # (but it doesn't really matter since is_stopping is not actually used right now)
                is_stopping = False
                log_exception(
                    e,
                    "Non-transient AgentClientError with exit code {exit_code} handling user message '{user_message}'",
                    exit_code=e.exit_code,
                    user_message=message,
                    # Lower priority of transient LLM API errors
                    priority=ExceptionPriority.LOW_PRIORITY,
                )
            self._output_messages.put(serialize_agent_wrapper_error(e=e, message=message, is_stopping=is_stopping))
        except Exception as e:
            log_exception(
                e,
                "Error handling user message: {user_message}",
                user_message=message,
            )
            self._output_messages.put(serialize_agent_wrapper_error(e=e, message=message, is_stopping=False))
            # since it's not a claude client error, raise it out of the agent wrapper
            raise
        else:
            # yay no errors
            if not self._is_stopping:
                self._output_messages.put(
                    # TODO: make pyre understand inheritance in pydantic so it understands that request_id exists
                    RequestSuccessAgentMessage(  # pyre-fixme[28]
                        message_id=AgentMessageID(),
                        request_id=message.message_id,
                        error=None,
                    )
                )
