"""Schemas for the Keystone CLI."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class StreamType(str, Enum):
    """Type of output stream from the agent process."""

    STDOUT = "stdout"
    STDERR = "stderr"


class ClaudeModel(str, Enum):
    """Model choices for the agent.

    Despite the name (kept for backwards compatibility), this enum includes
    models from all supported providers.
    """

    SONNET = "sonnet"
    OPUS = "opus"
    HAIKU = "haiku"
    OPUSPLAN = "opusplan"
    # Codex models
    GPT_5_CODEX = "gpt-5-codex"
    O3 = "o3"


class StreamEvent(BaseModel):
    """A single event from the agent's output stream."""

    stream: Literal["stdout", "stderr"]
    line: str


class AgentConfig(BaseModel):
    """Configuration for how the agent is run.

    This is part of the cache key - changing any field invalidates the cache.
    """

    agent_cmd: str
    max_budget_usd: float
    agent_time_limit_seconds: int
    agent_in_modal: bool
    model: ClaudeModel | None = None

    def to_cache_key_json(self) -> str:
        """Stable JSON representation for cache key computation."""
        return self.model_dump_json(indent=None)


class TokenSpending(BaseModel):
    """Used to track this resource usage by the agent."""

    input: int = 0
    cached: int = 0
    output: int = 0
    cache_creation: int = 0


class InferenceCost(BaseModel):
    """Cumulative inference cost at a point in time."""

    cost_usd: float = 0.0
    token_spending: TokenSpending = TokenSpending()


class AgentStatusMessage(BaseModel):
    """A status message from the agent with timestamp."""

    timestamp: datetime
    message: str


class TestResult(BaseModel):
    """Result of a single test case."""

    name: str  # Full test name (e.g., "classname::test_name")
    passed: bool
    skipped: bool = False


class VerificationResult(BaseModel):
    """Result of verification phase including image build and test execution."""

    success: bool
    error_message: str | None = None

    image_build_seconds: float | None = None
    test_execution_seconds: float | None = None

    # All test results from JUnit XML reports
    test_results: list[TestResult] = []


class AgentExecution(BaseModel):
    """Details about the agent's execution."""

    start_time: datetime
    end_time: datetime
    duration_seconds: float
    exit_code: int
    timed_out: bool = False
    model: str = ""
    summary: AgentStatusMessage | None = None
    status_messages: list[AgentStatusMessage] = []
    cost: InferenceCost


class GeneratedFiles(BaseModel):
    """Contents of the generated devcontainer files."""

    devcontainer_json: str | None = None
    dockerfile: str | None = None
    run_all_tests_sh: str | None = None


class BootstrapResult(BaseModel):
    """The final result of the entire bootstrap process."""

    success: bool
    error_message: str | None = None

    agent: AgentExecution

    verification: VerificationResult | None = None

    generated_files: GeneratedFiles | None = None
