from __future__ import annotations

from enum import StrEnum
from typing import Any

from imbue_core.errors import ExpectedError


class AgentCrashed(ExpectedError):
    def __init__(self, message: str, exit_code: int | None, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message, exit_code, metadata)
        self.exit_code = exit_code
        self.metadata = metadata


class UncleanTerminationAgentError(ExpectedError):
    pass


class IllegalOperationError(ExpectedError):
    pass


class WaitTimeoutAgentError(ExpectedError):
    pass


class AgentClientError(AgentCrashed):
    """
    This error is raised when the agent's client encounters an error.
    """


class AgentTransientError(AgentClientError):
    """
    This error is raised when the Claude client encounters a transient error (ex. internal server error)
    """


class AgentOutputDecodeError(ExpectedError):
    """
    This error is raised when the agent output is not decodable.
    """


class ErrorType(StrEnum):
    PROCESS_CRASHED = "PROCESS_CRASHED"
    TMUX_SESSION_DIED = "TMUX_SESSION_DIED"
    NONZERO_EXIT_CODE = "NONZERO_EXIT_CODE"
    RESPONSE_INCOMPLETE = "RESPONSE_INCOMPLETE"
