"""Configuration schemas for the eval harness."""

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class LLMModel(str, Enum):
    """LLM model choices for the agent (Claude, Codex, and OpenCode)."""

    # Claude models
    HAIKU = "claude-haiku-4-5-20251001"
    OPUS = "claude-opus-4-6"
    # Codex models
    CODEX_MINI = "gpt-5.1-codex-mini"
    CODEX = "gpt-5.2-codex"
    # OpenCode models (provider/model format — same backends, routed through OpenCode)
    OPENCODE_HAIKU = "anthropic/claude-haiku-4-5"
    OPENCODE_OPUS = "anthropic/claude-opus-4-6"
    OPENCODE_CODEX_MINI = "openai/gpt-5.1-codex-mini"
    OPENCODE_CODEX = "openai/gpt-5.2-codex"


class RepoEntry(BaseModel):
    """A single entry from the repo_list JSONL file."""

    id: str = Field(..., description="Unique short identifier (e.g. 'requests')")
    repo: str = Field(..., description="Git URL or local path to the repository")
    # Optional metadata (preserved from input, not used by eval)
    rank: int | None = None
    language: str | None = None
    build_system: str | None = None
    tests: str | None = None
    difficulty: str | None = None
    notes: str | None = None
    # Pinned after clone
    commit_hash: str | None = Field(
        default=None, description="Git commit hash (populated after clone)"
    )


class AgentConfig(BaseModel):
    """Configuration for the agent execution."""

    max_budget_usd: float = Field(default=1.0, description="Maximum budget per repo")
    timeout_minutes: int = Field(default=30, description="Timeout per repo in minutes")

    # Provider and agent command
    provider: str = Field(
        default="claude", description="LLM provider name (claude, codex, or opencode)"
    )
    agent_cmd: str | None = Field(
        default=None, description="Agent command override (default: inferred from provider)"
    )

    # Log database (shared with CLI)
    log_db: str | None = Field(
        default=None,
        description="Database for logging/caching. SQLite path or postgresql:// URL",
    )

    # Cache settings
    require_cache_hit: bool = Field(
        default=False, description="Fail if cache miss (for CI/testing)"
    )
    no_cache_replay: bool = Field(
        default=False, description="Skip cache lookup, force fresh execution"
    )

    # Model selection
    model: LLMModel | None = Field(
        default=None,
        description="LLM model to use (claude-haiku-4-5-20251001, claude-opus-4-6, gpt-5.1-codex-mini, gpt-5.2-codex)",
    )

    # When True, run the agent locally instead of on Modal
    run_agent_locally: bool = Field(
        default=False,
        description="Run agent locally with --run_agent_locally_with_dangerously_skip_permissions",
    )

    # Docker build cache (Modal secret name)
    docker_cache_secret: str = Field(
        default="keystone-docker-registry-config",
        description="Modal secret name with DOCKER_BUILD_CACHE_REGISTRY_{URL,USERNAME,PASSWORD}",
    )


class EvalConfig(BaseModel):
    """Top-level eval configuration."""

    # Optional human-readable name for this eval configuration
    name: str | None = Field(default=None, description="Name for this eval configuration")

    agent_config: AgentConfig = Field(default_factory=AgentConfig)
    max_workers: int = Field(default=4, description="Max parallel workers")
    trials_per_repo: int = Field(
        default=1,
        description="Number of trials per repo. When >1, caching is automatically disabled.",
    )
    # These are computed from EvalRunConfig globals; not set directly in config files.
    s3_output_prefix: str = Field(
        default="",
        description="S3 prefix for per-repo results (set by EvalRunConfig, not manually).",
    )
    s3_repo_cache_prefix: str = Field(
        default="",
        description="S3 prefix for cached repo tarballs (set by EvalRunConfig, not manually).",
    )


class RepoResult(BaseModel):
    """Result from processing a single repo."""

    repo_entry: RepoEntry
    success: bool
    error_message: str | None = None
    bootstrap_result: dict[str, Any] | None = None
    agent_config: AgentConfig | None = None
    trial_index: int | None = None


class EvalOutput(BaseModel):
    """Output of the entire eval run."""

    keystone_version: dict[str, Any]
    repos: list[RepoEntry]  # Input repos with commit_hash pinned
    results: list[RepoResult]


class EvalRunConfig(BaseModel):
    """Top-level configuration file supporting multiple eval configurations.

    When running from a config file, this is the root object.
    Each entry in ``configs`` is an independent eval that will be run.

    ``s3_output_prefix`` and ``s3_repo_cache_prefix`` set defaults for all
    configs.  Per-config ``s3_output_prefix`` is derived by appending the
    config name (e.g. ``s3://bucket/evals/run1/`` + ``claude-opus/``).
    Per-config ``s3_repo_cache_prefix`` is shared across all configs (repos
    are agent-independent).  Individual configs can still override either.
    """

    repo_list_path: str = Field(..., description="Path to repo_list.jsonl")
    configs: list[EvalConfig] = Field(..., description="List of eval configurations to run")
    limit: int | None = Field(default=None, description="Limit to first N repos")
    s3_output_prefix: str = Field(
        ...,
        description="Global S3 output prefix. Each config gets a subdirectory named after the config.",
    )
    s3_repo_cache_prefix: str = Field(
        default="s3://int8-datasets/keystone/evals/repo-tarballs/",
        description="Global S3 prefix for cached repo tarballs (shared across all configs).",
    )

    def resolve_config(self, eval_config: EvalConfig, index: int) -> EvalConfig:
        """Return a copy with s3 prefixes built from the global values."""
        name = eval_config.name or f"config-{index}"
        base = self.s3_output_prefix.rstrip("/")
        return eval_config.model_copy(
            update={
                "s3_output_prefix": f"{base}/{name}/",
                "s3_repo_cache_prefix": self.s3_repo_cache_prefix,
            }
        )


def resolve_path(path: str | Path) -> Path:
    """Resolve a path, expanding ~ and making absolute."""
    return Path(path).expanduser().resolve()
