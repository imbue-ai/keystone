from pydantic import BaseModel


class TokenSpending(BaseModel):
    input: int = 0
    cached: int = 0
    output: int = 0
    cache_creation: int = 0


class BootstrapResult(BaseModel):
    success: bool
    total_time: float
    model: str = ""
    token_spending: TokenSpending
    cost_usd: float
    agent_exit_code: int
