"""Base types for LLM provider abstraction.

Defines the event types that all providers emit and the abstract AgentProvider interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

# ── Agent events (common output types) ────────────────────────────────


class AgentEvent(BaseModel):
    """Base class for all agent output events."""


class AgentTextEvent(AgentEvent):
    """Agent emitted human-readable text (assistant message content)."""

    text: str


class AgentToolCallEvent(AgentEvent):
    """Agent invoked a tool."""

    name: str
    input: dict[str, Any]


class AgentToolResultEvent(AgentEvent):
    """A tool returned a result."""

    tool_name: str
    output: str
    exit_code: int | None = None


class AgentCostEvent(AgentEvent):
    """Cost and token usage update.

    Claude provides cumulative ``cost_usd``; Codex does not (leaves it None).
    Token fields are *deltas* for this turn.
    """

    cost_usd: float | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0


class AgentErrorEvent(AgentEvent):
    """Agent reported an error."""

    message: str


# ── Abstract provider ─────────────────────────────────────────────────


class AgentProvider(ABC):
    """Interface that each LLM backend must implement."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. ``'claude'``, ``'codex'``)."""
        ...

    @property
    @abstractmethod
    def default_cmd(self) -> str:
        """Default CLI executable when the user doesn't override ``--agent_cmd``."""
        ...

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> list[str]:
        """Return the full argv list to execute the agent."""
        ...

    @abstractmethod
    def parse_stdout_line(self, line: str) -> list[AgentEvent]:
        """Parse one stdout line into zero or more typed events.

        Returns an empty list for unparseable / noise lines.
        A single line may produce multiple events (e.g. Claude assistant message
        with text + tool_use, or Codex item.completed with multiple content blocks).
        """
        ...

    def env_vars(self) -> dict[str, str]:
        """Extra environment variables required by this provider (e.g. API keys).

        Returns an empty dict by default.
        """
        return {}

    def required_env_var_names(self) -> list[str]:
        """Names of environment variables this provider needs (e.g. ``['OPENAI_API_KEY']``).

        Used by the Modal runner to read secret values from the sandbox when the
        host environment doesn't have them.  Returns an empty list by default.
        """
        return []
