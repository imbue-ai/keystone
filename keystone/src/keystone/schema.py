"""Schemas for the Keystone CLI."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field


def _ensure_iso_string(v: object) -> str:
    """Convert datetime objects to ISO strings; pass through strings."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, str):
        return v
    raise TypeError(f"Expected datetime or str, got {type(v)}")


ISOTimestamp = Annotated[str, BeforeValidator(_ensure_iso_string)]


class StreamType(StrEnum):
    """Type of output stream from the agent process."""

    STDOUT = "stdout"
    STDERR = "stderr"


class StreamEvent(BaseModel):
    """A single event from the agent's output stream."""

    stream: StreamType
    line: str


class LLMModel(StrEnum):
    """LLM model choices for the agent (Claude, Codex, and OpenCode)."""

    # Claude models
    HAIKU = "claude-haiku-4-5-20251001"
    OPUS = "claude-opus-4-6"
    # Codex models
    CODEX_MINI = "gpt-5.1-codex-mini"
    CODEX = "gpt-5.2-codex"
    CODEX_53 = "gpt-5.3-codex"
    GPT_54 = "gpt-5.4"
    FAKE_ERROR = "fake-error-model"
    # OpenCode models (provider/model format — same backends, routed through OpenCode)
    OPENCODE_HAIKU = "anthropic/claude-haiku-4-5"
    OPENCODE_OPUS = "anthropic/claude-opus-4-6"
    OPENCODE_CODEX_MINI = "openai/gpt-5.1-codex-mini"
    OPENCODE_CODEX = "openai/gpt-5.2-codex"
    OPENCODE_GPT_54 = "openai/gpt-5.4"


class AgentConfig(BaseModel):
    """Configuration for how the agent is run.

    This is part of the cache key — changing any field invalidates the cache.
    """

    max_budget_usd: float
    agent_time_limit_seconds: int
    agent_in_modal: bool

    # Provider and agent command.
    provider: str = Field(
        default="claude", description="LLM provider name (claude, codex, or opencode)"
    )
    # model picks which LLM to use (passed as --model to the agent CLI).
    # agent_cmd overrides the agent binary/path; None means use the provider's default command.
    # Both are optional: omitting model lets the provider use its own default, omitting agent_cmd
    # means we infer the command from the provider (e.g. "claude", "codex").
    model: LLMModel | None = None
    agent_cmd: str | None = None

    # Reasoning level — provider-specific, exactly one must be set for the active provider.
    claude_reasoning_level: str | None = Field(
        default=None,
        description="Reasoning level for Claude (e.g. 'low', 'medium', 'high'). Required when provider is 'claude'.",
    )
    codex_reasoning_level: str | None = Field(
        default=None,
        description="Reasoning level for Codex (e.g. 'low', 'medium', 'high'). Required when provider is 'codex'.",
    )

    # Cost monitoring — poll ccusage every N seconds while the agent runs.
    # Set to 0 to disable mid-run cost monitoring.
    cost_poll_interval_seconds: int = Field(
        ...,
        description="How often (seconds) to poll ccusage and enforce max_budget_usd. 0 disables.",
    )

    # Feature toggles — all required so config files are explicit.
    guardrail: bool = Field(..., description="Enable or disable guardrail structural checks")
    use_agents_md: bool = Field(
        ...,
        description="Use AGENTS.md file + short CLI prompt instead of full inline prompt",
    )

    def to_cache_key_json(self) -> str:
        """Stable JSON representation for cache key computation."""
        return self.model_dump_json(indent=None)


class KeystoneConfig(BaseModel):
    """Configuration for a single Keystone CLI invocation.

    ``agent_config`` holds the fields that affect the agent's behavior
    (and therefore the cache key).  Everything else on this class is
    infrastructure / orchestration knobs that don't change the result.
    """

    agent_config: AgentConfig = Field(..., description="Agent behavioral configuration (cache key)")

    # Log database.
    log_db: str | None = Field(
        default=None,
        description="Database for logging/caching. SQLite path or postgresql:// URL",
    )

    # Cache settings.
    require_cache_hit: bool = Field(
        default=False, description="Fail if cache miss (for CI/testing)"
    )
    no_cache_replay: bool = Field(
        default=False, description="Skip cache lookup, force fresh execution"
    )


class TokenSpending(BaseModel):
    """Used to track this resource usage by the agent."""

    input: int = 0
    cached: int = 0
    output: int = 0
    cache_creation: int = 0


class InferenceCost(BaseModel):
    """Cumulative inference cost at a point in time."""

    cost_usd: float = 0.0  # As reported by ccusage (or 0 for local/cached runs)
    cost_usd_computed: float = 0.0  # Deprecated: kept for schema compat, always 0
    token_spending: TokenSpending = TokenSpending()
    ccusage_raw: dict[str, object] | None = None  # Raw ccusage session JSON if available


class AgentStatusMessage(BaseModel):
    """A status message from the agent with timestamp."""

    timestamp: ISOTimestamp
    message: str


class TestResult(BaseModel):
    """Result of a single test case, parsed from JUnit XML."""

    name: str  # Full test name (e.g., "classname::test_name")
    passed: bool
    skipped: bool = False


class VerificationResult(BaseModel):
    """Result of verification phase including image build and test execution."""

    success: bool
    error_message: str | None = None

    image_build_seconds: float | None = None
    test_execution_seconds: float | None = None

    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0

    # All test results from JUnit XML reports
    test_results: list[TestResult] = []


class AgentExecution(BaseModel):
    """Details about the agent's execution."""

    start_time: ISOTimestamp
    end_time: ISOTimestamp
    duration_seconds: float
    exit_code: int
    timed_out: bool = False
    cost_limit_exceeded: bool = False

    summary: AgentStatusMessage | None = None
    status_messages: list[AgentStatusMessage] = []
    error_messages: list[str] = []

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

    # CLI arguments used to invoke keystone, for reproducibility.
    cli_args: list[str] | None = None

    # Broken-commit re-verification results (mutation-augmented eval).
    broken_commit_verifications: dict[str, VerificationResult] = {}
    post_broken_commits_verification: VerificationResult | None = None
    unexpected_broken_commit_passes: int = 0


class VersionInfo(BaseModel):
    """Version information for the current codebase."""

    branch: str | None
    commit_count: int
    commit_timestamp: str | None  # ISO format, None when unavailable
    git_hash: str | None
    is_dirty: bool
