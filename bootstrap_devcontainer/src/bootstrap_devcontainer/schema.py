from pydantic import BaseModel


class TokenSpending(BaseModel):
    input: int = 0
    cached: int = 0
    output: int = 0
    cache_creation: int = 0


class TestSummary(BaseModel):
    """Generic test summary for any language."""

    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    passed_tests: list[str] = []
    failed_tests: list[str] = []
    skipped_tests: list[str] = []


class BootstrapResult(BaseModel):
    success: bool
    agent_work_time: float
    verification_wall_time: float | None = None
    model: str = ""
    token_spending: TokenSpending
    cost_usd: float
    agent_exit_code: int
    test_summary: TestSummary | None = None
