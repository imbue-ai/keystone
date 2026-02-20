"""Configuration schemas for the eval harness."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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

    # Agent command (use fake_agent.py for testing)
    agent_cmd: str = Field(default="claude", description="Agent command to run")

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

    # Docker build cache (Modal secret name)
    docker_cache_secret: str = Field(
        default="keystone-docker-registry-config",
        description="Modal secret name with DOCKER_BUILD_CACHE_REGISTRY_{URL,USERNAME,PASSWORD}",
    )


class EvalConfig(BaseModel):
    """Top-level eval configuration."""

    agent_config: AgentConfig = Field(default_factory=AgentConfig)
    max_workers: int = Field(default=4, description="Max parallel workers")
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


def resolve_path(path: str | Path) -> Path:
    """Resolve a path, expanding ~ and making absolute."""
    return Path(path).expanduser().resolve()
