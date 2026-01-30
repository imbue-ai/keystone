from datetime import datetime

from pydantic import BaseModel


class AgentConfig(BaseModel):
    """Configuration for how the agent is run.

    This is part of the cache key - changing any field invalidates the cache.
    """

    agent_cmd: str
    max_budget_usd: float
    agent_time_limit_secs: int
    agent_in_modal: bool

    def to_cache_key_json(self) -> str:
        """Stable JSON representation for cache key computation."""
        return self.model_dump_json(indent=None)


class VerifyResult(BaseModel):
    """Result of running verification tests."""

    success: bool
    error_message: str | None = None
    image_build_seconds: float | None = None
    test_execution_seconds: float | None = None


class TokenSpending(BaseModel):
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


class TestSummary(BaseModel):
    """Test summary for a single test framework."""

    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    passed_tests: list[str] = []
    failed_tests: list[str] = []
    skipped_tests: list[str] = []


class VerificationResult(BaseModel):
    """Result of verification phase including image build and test execution."""

    success: bool
    error_message: str | None = None

    image_build_seconds: float | None = None
    test_execution_seconds: float | None = None

    # Per-language test summaries - each is populated only if that report format was found
    pytest_summary: TestSummary | None = None
    go_test_summary: TestSummary | None = None
    node_test_summary: TestSummary | None = None
    cargo_test_summary: TestSummary | None = None


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


class BootstrapResult(BaseModel):
    success: bool
    error_message: str | None = None

    agent: AgentExecution

    verification: VerificationResult | None = None
