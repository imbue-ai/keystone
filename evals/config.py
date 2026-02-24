"""Configuration schemas for the eval harness."""

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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

    # Agent command (use fake_claude_agent.py / fake_codex_agent.py for testing)
    agent_cmd: str = Field(default="claude", description="Agent command to run")

    # LLM provider name (must match keystone.llm_provider.PROVIDER_REGISTRY)
    provider: str = Field(
        default="claude", description="LLM provider name (e.g. 'claude', 'codex')"
    )

    # When True, run the agent locally instead of on Modal
    run_agent_locally: bool = Field(
        default=False,
        description="Run agent locally with --run_agent_locally_with_dangerously_skip_permissions",
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
    model: ClaudeModel | None = Field(
        default=None,
        description="Claude model to use (sonnet, opus, haiku, opusplan)",
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
    s3_output_prefix: str = Field(
        ...,
        description="S3 prefix for per-repo results (e.g. s3://bucket/evals/2026-02-20/)",
    )
    s3_repo_cache_prefix: str = Field(
        default="s3://int8-datasets/keystone/evals/repo-tarballs/",
        description="S3 prefix for cached repo tarballs",
    )


class RepoResult(BaseModel):
    """Result from processing a single repo."""

    repo_entry: RepoEntry
    success: bool
    error_message: str | None = None
    bootstrap_result: dict[str, Any] | None = None


class EvalOutput(BaseModel):
    """Output of the entire eval run."""

    keystone_version: dict[str, Any]
    repos: list[RepoEntry]  # Input repos with commit_hash pinned
    results: list[RepoResult]


class EvalRunConfig(BaseModel):
    """Top-level configuration file supporting multiple eval configurations.

    When running from a config file, this is the root object.
    Each entry in ``configs`` is an independent eval that will be run.
    """

    repo_list_path: str = Field(..., description="Path to repo_list.jsonl")
    configs: list[EvalConfig] = Field(..., description="List of eval configurations to run")
    limit: int | None = Field(default=None, description="Limit to first N repos")


def resolve_path(path: str | Path) -> Path:
    """Resolve a path, expanding ~ and making absolute."""
    return Path(path).expanduser().resolve()
