"""Configuration schemas for the eval harness.

Classes are organized into two groups:

1. **Input / configuration** — describe *what* to run:
   - RepoEntry, KeystoneConfig, EvalConfig, EvalRunConfig

2. **Output / results** — describe *what happened*:
   - KeystoneRepoResult, EvalResult
"""

import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from keystone.schema import BootstrapResult, VersionInfo

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LLMModel(str, Enum):
    """LLM model choices for the agent (Claude, Codex, and OpenCode)."""

    # Claude models
    HAIKU = "claude-haiku-4-5-20251001"
    OPUS = "claude-opus-4-6"
    # Codex models
    CODEX_MINI = "gpt-5.1-codex-mini"
    CODEX = "gpt-5.2-codex"
    CODEX_53 = "gpt-5.3-codex"
    # OpenCode models (provider/model format — same backends, routed through OpenCode)
    OPENCODE_HAIKU = "anthropic/claude-haiku-4-5"
    OPENCODE_OPUS = "anthropic/claude-opus-4-6"
    OPENCODE_CODEX_MINI = "openai/gpt-5.1-codex-mini"
    OPENCODE_CODEX = "openai/gpt-5.2-codex"


# ---------------------------------------------------------------------------
# Input: repo specification
# ---------------------------------------------------------------------------


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
    # Pinned commit — the clone checks out this exact commit for reproducibility
    commit_hash: str = Field(
        ...,
        description="Git commit hash. Required for reproducible evals. "
        "Use evals/scripts/populate_commit_hashes.py to populate.",
    )


# ---------------------------------------------------------------------------
# Input: keystone agent configuration
# ---------------------------------------------------------------------------


# FIXME: Move this into src/keystone/schema.py, and having Keystone's CLI args be converted into this type before being passed onwards to the rest of the Keystone app.  Make sure there's not already a similar/overlapping/conficting type like this.  LLMModel would have to move as well -- I thought we already had one like that.  In fact we do.
class KeystoneConfig(BaseModel):
    """Configuration for a single Keystone CLI invocation.

    These fields map directly to Keystone CLI flags.  Every field that
    materially affects the agent run is required (no implicit defaults)
    so that config files are self-documenting.
    """

    max_budget_usd: float = Field(..., description="Maximum budget per repo")
    timeout_minutes: int = Field(..., description="Timeout per repo in minutes")

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

    # Provider and agent command.
    provider: str = Field(
        default="claude", description="LLM provider name (claude, codex, or opencode)"
    )
    agent_cmd: str | None = Field(
        default=None, description="Agent command override (default: inferred from provider)"
    )

    # Model selection.
    model: LLMModel | None = Field(
        default=None,
        description="LLM model to use (claude-haiku-4-5-20251001, claude-opus-4-6, gpt-5.1-codex-mini, gpt-5.2-codex)",
    )

    # When True, run the agent locally instead of on Modal.
    run_agent_locally: bool = Field(
        default=False,
        description="Run agent locally with --run_agent_locally_with_dangerously_skip_permissions",
    )

    # Feature toggles — all required so config files are explicit.
    evaluator: bool = Field(
        ...,
        description="Enable or disable the LLM evaluator fix-up pass.",
    )
    guardrail: bool = Field(..., description="Enable or disable guardrail structural checks")
    use_agents_md: bool = Field(
        ...,
        description="Use AGENTS.md file + short CLI prompt instead of full inline prompt",
    )


# ---------------------------------------------------------------------------
# Input: single eval configuration (one KeystoneConfig + trial settings)
# ---------------------------------------------------------------------------


class EvalConfig(BaseModel):
    """One eval configuration: a KeystoneConfig plus trial/output settings.

    ``s3_output_prefix`` and ``s3_repo_cache_prefix`` are **not** meant to
    be set in config files.  They are populated by
    ``EvalRunConfig.resolve_config`` from the run-level S3 prefixes.
    """

    # Human-readable name — required so output directories are meaningful.
    name: str | None = Field(..., description="Name for this eval configuration")

    keystone_config: KeystoneConfig = Field(
        ..., description="Keystone agent configuration for this eval."
    )

    trials_per_repo: int = Field(
        default=1,
        description="Number of trials per repo. When >1, caching is automatically disabled.",
    )

    # Resolved by EvalRunConfig.resolve_config — not set directly in config files.
    s3_output_prefix: str = Field(
        default="",
        description="S3 prefix for per-repo results (set by EvalRunConfig, not manually).",
    )
    s3_repo_cache_prefix: str = Field(
        default="",
        description="S3 prefix for cached repo tarballs (set by EvalRunConfig, not manually).",
    )


# ---------------------------------------------------------------------------
# Input: top-level run configuration
# ---------------------------------------------------------------------------


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

    description: str = Field(
        ...,
        description="Free-text summary of what this whole experiment is about.",
    )
    repo_list_path: str = Field(..., description="Path to repo_list.jsonl")
    configs: list[EvalConfig] = Field(..., description="List of eval configurations to run")
    limit_to_first_n_repos: int | None = Field(default=None, description="Limit to first N repos")
    s3_output_prefix: str = Field(
        ...,
        description="Global S3 output prefix. Each config gets a subdirectory named after the config.",
    )
    s3_repo_cache_prefix: str = Field(
        default="s3://int8-datasets/keystone/evals/repo-tarballs/",
        description="Global S3 prefix for cached repo tarballs (shared across all configs).",
    )

    # Docker Hub mirror for pull-through caching.
    docker_registry_mirror: str = Field(
        default_factory=lambda: os.environ.get("DOCKER_REGISTRY_MIRROR", ""),
        description=(
            "URL of Docker Hub pull-through cache mirror.  "
            "Set the DOCKER_REGISTRY_MIRROR environment variable or pass explicitly."
        ),
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


# ---------------------------------------------------------------------------
# Output: per-repo result
# ---------------------------------------------------------------------------


class KeystoneRepoResult(BaseModel):
    """Result from one trial: a single application of Keystone to a single repo.

    ``success`` is False when:
    - The keystone CLI process exits with a non-zero return code, OR
    - An infrastructure error prevents keystone from running (e.g. tarball
      download failure, Prefect task crash).
    """

    repo_entry: RepoEntry
    keystone_config: KeystoneConfig | None = None
    trial_index: int | None = None

    success: bool
    error_message: str | None = None

    bootstrap_result: BootstrapResult | None = None

    def __init__(self, **data: Any) -> None:
        # Accept raw dict for bootstrap_result (e.g. from JSON deserialization).
        br = data.get("bootstrap_result")
        if isinstance(br, dict):
            data["bootstrap_result"] = BootstrapResult(**br)
        super().__init__(**data)


# ---------------------------------------------------------------------------
# Output: full eval run result
# ---------------------------------------------------------------------------


class EvalResult(BaseModel):
    """Output of the entire eval run.

    This type is less important for analysis because we want to inspect
    partially completed runs before this global summary is available.
    Individual ``KeystoneRepoResult`` files uploaded per-repo are the
    primary source of truth.
    """

    keystone_version: VersionInfo
    eval_config: EvalConfig | None = Field(
        default=None,
        description="Snapshot of the EvalConfig used for this run.",
    )
    results: list[KeystoneRepoResult]

    def __init__(self, **data: Any) -> None:
        # Accept raw dict for keystone_version (e.g. from JSON deserialization).
        kv = data.get("keystone_version")
        if isinstance(kv, dict):
            data["keystone_version"] = VersionInfo(**kv)
        ec = data.get("eval_config")
        if isinstance(ec, dict):
            data["eval_config"] = EvalConfig(**ec)
        super().__init__(**data)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def resolve_path(path: str | Path) -> Path:
    """Resolve a path, expanding ~ and making absolute."""
    return Path(path).expanduser().resolve()
