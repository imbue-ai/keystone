from datetime import datetime

from pydantic import BaseModel


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


class BootstrapResult(BaseModel):
    success: bool
    error_message: str | None = None
    agent_timed_out: bool = False

    start_time: datetime
    end_time: datetime
    model: str = ""
    agent_exit_code: int
    agent_work_seconds: float
    agent_summary: AgentStatusMessage | None = None
    status_messages: list[AgentStatusMessage] = []
    cost: InferenceCost

    verification: VerificationResult | None = None
