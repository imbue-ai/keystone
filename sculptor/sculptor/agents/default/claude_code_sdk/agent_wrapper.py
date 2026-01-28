"""Uses the Anthropic Claude Code SDK to run a Claude Code agent.

Particularly, headless mode: https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-headless
"""

from __future__ import annotations

from typing import assert_never

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import Message
from sculptor.agents.default.agent_wrapper import DefaultAgentWrapper
from sculptor.agents.default.claude_code_sdk.process_manager import ClaudeProcessManager
from sculptor.constants import PROXY_CACHE_PATH
from sculptor.database.models import Project
from sculptor.interfaces.agents.agent import ClaudeCodeSDKAgentConfig
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.agents.agent import CompactTaskUserMessage
from sculptor.interfaces.agents.agent import InterruptProcessUserMessage
from sculptor.interfaces.agents.agent import RequestSkippedAgentMessage
from sculptor.interfaces.agents.agent import ResumeAgentResponseRunnerMessage
from sculptor.interfaces.agents.constants import AGENT_EXIT_CODE_SHUTDOWN_DUE_TO_EXCEPTION
from sculptor.services.config_service.data_types import AWSBedrockApiKey
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import ClaudeOauthCredentials
from sculptor.services.config_service.data_types import Credentials


class ClaudeCodeSDKAgent(DefaultAgentWrapper):
    config: ClaudeCodeSDKAgentConfig
    project: Project
    _claude_process_manager: ClaudeProcessManager | None = PrivateAttr(default=None)

    def _terminate(self, force_kill_seconds: float = 5.0) -> None:
        assert self._claude_process_manager is not None, "Claude process manager must be set"
        self._claude_process_manager.stop(force_kill_seconds, is_waiting=False)

    def poll(self) -> int | None:
        assert self._claude_process_manager is not None, "Claude process manager must be set"
        if self._claude_process_manager.get_exception_if_exists() is not None:
            self._exit_code = AGENT_EXIT_CODE_SHUTDOWN_DUE_TO_EXCEPTION
        return super().poll()

    def wait(self, timeout: float) -> int:
        assert self._claude_process_manager is not None, "Claude process manager must be set"
        self._claude_process_manager.stop(timeout, is_waiting=True)

        assert self._exit_code is not None, (
            "The wait method will only ever terminate if the agent is stopped or if there is an exception"
        )
        return self._exit_code

    def _start(self) -> None:
        # Initialize the Claude process manager
        self._claude_process_manager = ClaudeProcessManager(
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

    def _refresh_settings(self, credentials: Credentials | None = None) -> None:
        # make sure we've updated the credentials
        if credentials is None and self._get_credentials is not None:
            credentials = self._get_credentials()
        assert credentials is not None
        self._load_secrets(credentials=credentials)
        anthropic_credentials = credentials.anthropic
        assert anthropic_credentials is not None

    def _push_message(self, message: Message) -> bool:
        match message:
            case CommandInputUserMessage() | ChatInputUserMessage() | ResumeAgentResponseRunnerMessage():
                if message.message_id.suffix in self._removed_message_ids:
                    logger.info("Skipping message {} as it has been removed", message.message_id)
                    self._output_messages.put(
                        # TODO: pyre doesn't understand pydantic
                        RequestSkippedAgentMessage(request_id=message.message_id)  # pyre-fixme[28]
                    )
                else:
                    assert self._claude_process_manager is not None, "Claude process manager must be set"
                    self._refresh_settings()
                    self._claude_process_manager.process_input_message(message=message)  # pyre-ignore[16]
            case CompactTaskUserMessage():
                assert self._claude_process_manager is not None, "Claude process manager must be set"
                self._refresh_settings()
                self._claude_process_manager.process_compact_message(message=message)  # pyre-ignore[16]
            case InterruptProcessUserMessage():
                assert self._claude_process_manager is not None, "Claude process manager must be set"
                self._claude_process_manager.interrupt_current_message(message=message)
            case _:
                return False
        return True

    def _initialize_for_testing(self, credentials: Credentials) -> None:
        assert self.in_testing, "setup_testing should only be called when in testing"
        assert "ANTHROPIC_API_KEY" not in self._secrets
        # This setup for testing is slightly tricky.
        # We inject a (valid) Anthropic API key for testing, but we don't want Claude Code to actually use it;
        # instead, we want the proxy to use it, and Claude Code to use the proxy.
        #
        # 1. We extract the actual Anthropic credentials,
        #    which must be an API key because the proxy is only set up to accept that.
        #
        # 2. We re-assign anthropic_credentials to a fake credential so that Claude Code can't see the real one.
        #    This isn't strictly necessary, but it makes sure that Claude Code can't access Anthropic API directly,
        #    just in case the ANTHROPIC_BASE_URL override is somehow not set up properly.
        #
        # TODO: Clean this up so that it's less confusing.
        anthropic_credentials = credentials.anthropic
        assert isinstance(anthropic_credentials, AnthropicApiKey)
        anthropic_api_key = anthropic_credentials.anthropic_api_key
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
            proxy_secrets["ANTHROPIC_API_KEY"] = anthropic_api_key
        logger.info("proxy secrets: {}", proxy_secrets)

        # Thad: I do not completely understand the reason, but this just does not work if you run the proxy as root.
        # Integration tests just stall trying to talk to it.
        self.environment.run_process_in_background(
            ["/imbue/.venv/bin/python", "/imbue/claude_code_proxy.py"], secrets=proxy_secrets, run_as_root=False
        )

        if self._secrets.get("ANTHROPIC_BASE_URL"):
            raise Exception(
                "In testing but ANTHROPIC_BASE_URL was set, this should not happen. The tests override this variable to implement LLM caching."
            )
        logger.debug("Forcing an override of ANTHROPIC_BASE_URL to localhost for testing")
        self._secrets["ANTHROPIC_BASE_URL"] = "http://localhost:8082"

    def _load_secrets(self, credentials: Credentials) -> None:
        """This loads the anthropic creds into the secrets dictionary."""
        anthropic_credentials = credentials.anthropic
        assert anthropic_credentials is not None, "Anthropic credentials must be set"
        match anthropic_credentials:
            case AnthropicApiKey(anthropic_api_key=anthropic_api_key):
                self._secrets["ANTHROPIC_API_KEY"] = anthropic_api_key
                self._secrets.pop("IMBUE_ANTHROPIC_AUTH_TOKEN", None)
            case AWSBedrockApiKey(bedrock_api_key=bedrock_api_key):
                self._secrets["AWS_BEARER_TOKEN_BEDROCK"] = bedrock_api_key
                self._secrets["CLAUDE_CODE_USE_BEDROCK"] = "1"
                self._secrets.pop("IMBUE_ANTHROPIC_AUTH_TOKEN", None)
            case ClaudeOauthCredentials():
                # Claude Code prioritizes ANTHROPIC_API_KEY over OAuth credentials,
                # so we have to remove it.
                self._secrets.pop("ANTHROPIC_API_KEY", None)
                self._secrets.pop("AWS_BEARER_TOKEN_BEDROCK", None)
                # Not used by Claude Code itself, but by imbue_verify.
                # Search for IMBUE_ANTHROPIC_AUTH_TOKEN in imbue_core to see where it's used.
                self._secrets["IMBUE_ANTHROPIC_AUTH_TOKEN"] = anthropic_credentials.access_token
            case _ as unreachable:
                # pyre doesn't understand the matching here
                assert_never(unreachable)  # pyre-ignore[6]
