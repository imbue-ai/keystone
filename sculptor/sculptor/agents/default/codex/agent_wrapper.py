"""Uses the Codex scripting mode to run a Codex agent."""

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import Message
from sculptor.agents.default.agent_wrapper import DefaultAgentWrapper
from sculptor.agents.default.codex.process_manager import CodexProcessManager
from sculptor.agents.default.codex.utils import populate_codex_settings
from sculptor.constants import PROXY_CACHE_PATH
from sculptor.interfaces.agents.agent import CodexAgentConfig
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.agents.agent import CompactTaskUserMessage
from sculptor.interfaces.agents.agent import InterruptProcessUserMessage
from sculptor.interfaces.agents.agent import RequestSkippedAgentMessage
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_SHUTDOWN_DUE_TO_EXCEPTION
from sculptor.services.config_service.data_types import Credentials


class CodexAgent(DefaultAgentWrapper):
    config: CodexAgentConfig
    task_id: TaskID
    _codex_process_manager: CodexProcessManager | None = PrivateAttr(default=None)

    def _push_message(self, message: Message) -> bool:
        match message:
            case CommandInputUserMessage() | ChatInputUserMessage() | ResumeAgentResponseRunnerMessage():
                if message.message_id.suffix in self._removed_message_ids:
                    logger.info("Skipping message {} as it has been removed", message.message_id)
                    self._output_messages.put(
                        RequestSkippedAgentMessage(request_id=message.message_id)  # pyre-fixme[28]
                    )
                else:
                    assert self._codex_process_manager is not None, "Codex process manager must be set"
                    self._codex_process_manager.process_input_message(message=message)
            case CompactTaskUserMessage():
                assert self._codex_process_manager is not None, "Codex process manager must be set"
                self._codex_process_manager.process_compact_message(message=message)
            case InterruptProcessUserMessage():
                assert self._codex_process_manager is not None, "Codex process manager must be set"
                self._codex_process_manager.interrupt_current_message(message=message)
            case _:
                return False
        return True

    def terminate(self, force_kill_seconds: float = 5.0) -> None:
        # Stop the terminal manager first
        if self._terminal_manager:
            self._terminal_manager.stop()

        assert self._codex_process_manager is not None, "Codex process manager must be set"
        self._codex_process_manager.stop(force_kill_seconds, is_waiting=False)

    def poll(self) -> int | None:
        assert self._codex_process_manager is not None, "Codex process manager must be set"
        if self._codex_process_manager.get_exception_if_exists() is not None:
            self._exit_code = AGENT_EXIT_CODE_SHUTDOWN_DUE_TO_EXCEPTION
        return super().poll()

    def wait(self, timeout: float) -> int:
        assert self._codex_process_manager is not None, "Codex process manager must be set"
        self._codex_process_manager.stop(timeout, is_waiting=True)

        assert self._exit_code is not None, (
            "The wait method will only ever terminate if the agent is stopped or if there is an exception"
        )
        return self._exit_code

    def _start(self) -> None:
        # Initialize the Codex process manager
        self._codex_process_manager = CodexProcessManager(
            environment=self.environment,
            in_testing=self.in_testing,
            secrets=self._secrets,
            task_id=self.task_id,
            output_message_queue=self._output_messages,
            handle_user_message_callback=self._handle_user_message,
            system_prompt=self.system_prompt,
            source_branch=self.source_branch,
            task_branch=self.task_branch,
        )

    # TODO CODEX: it's a little weird that this is called from both inside and outside, on both codex and claude
    def _refresh_settings(self, credentials: Credentials | None = None) -> None:
        if credentials is None and self._get_credentials is not None:
            credentials = self._get_credentials()
        populate_codex_settings(environment=self.environment, credentials=credentials)

    def _initialize_for_testing(self, credentials: Credentials) -> None:
        assert self.in_testing, "setup_testing should only be called when in testing"
        assert "OPENAI_API_KEY" not in self._secrets
        # TODO: Clean this up so that it's less confusing.
        openai_credentials = credentials.openai
        assert openai_credentials is not None
        openai_api_key = openai_credentials.openai_api_key
        proxy_secrets = dict(self._secrets)

        snapshot_path = self.snapshot_path
        if snapshot_path is not None:
            proxy_secrets["SNAPSHOT_PATH"] = PROXY_CACHE_PATH
            try:
                self.environment.copy_from_local(snapshot_path, PROXY_CACHE_PATH, recursive=True)
            except FileNotFoundError:
                logger.error("Missing snapshot file {} for test", snapshot_path)
                raise
        else:
            proxy_secrets["OPENAI_API_KEY"] = openai_api_key
        logger.info("proxy secrets: {}", proxy_secrets)

        # Thad (copied from claude_code_sdk/agent_wrapper.py): I do not completely understand the reason, but this just does not work if you run the proxy as root.
        # Integration tests just stall trying to talk to it.
        self.environment.run_process_in_background(
            ["/imbue/.venv/bin/python", "/imbue/codex_proxy.py"], secrets=proxy_secrets, run_as_root=False
        )

        if self._secrets.get("OPENAI_BASE_URL"):
            raise Exception(
                "In testing but OPENAI_BASE_URL was set, this should not happen. The tests override this variable to implement LLM caching."
            )
        logger.debug("Forcing an override of OPENAI_BASE_URL to localhost for testing")
        self._secrets["OPENAI_BASE_URL"] = "http://0.0.0.0:8082"
