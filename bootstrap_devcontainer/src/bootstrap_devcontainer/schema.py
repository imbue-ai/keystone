from pydantic import BaseModel


class TokenSpending(BaseModel):
    input: int = 0
    cached: int = 0
    output: int = 0
    cache_creation: int = 0


class TestSummary(BaseModel):
    """Test summary for a single test framework."""

    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    passed_tests: list[str] = []
    failed_tests: list[str] = []
    skipped_tests: list[str] = []


class BootstrapResult(BaseModel):
    success: bool
    error_message: str | None = None
    agent_summary: str | None = None

    model: str = ""
    agent_exit_code: int
    agent_work_seconds: float
    cost_usd: float
    token_spending: TokenSpending

    verification_seconds: float | None = None
    # Per-language test summaries - each is populated only if that report format was found
    pytest_summary: TestSummary | None = None
    go_test_summary: TestSummary | None = None
    node_test_summary: TestSummary | None = None
    cargo_test_summary: TestSummary | None = None
