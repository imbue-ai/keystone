"""Configuration schemas for the eval harness."""

import os
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
    CODEX_53 = "gpt-5.3-codex"
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
    # Pinned commit — the clone checks out this exact commit for reproducibility
    commit_hash: str = Field(
        ...,
        description="Git commit hash. Required for reproducible evals. "
        "Use evals/scripts/populate_commit_hashes.py to populate.",
    )


# FIXME: Would it make sense to call this KeystoneConfig?  These are the options that feed the Keystone CLI, I believe.
class AgentConfig(BaseModel):
    """Configuration for the agent execution."""

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

    # Provider and agent command
    provider: str = Field(
        default="claude", description="LLM provider name (claude, codex, or opencode)"
    )
    agent_cmd: str | None = Field(
        default=None, description="Agent command override (default: inferred from provider)"
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

    # Feature toggles
    evaluator: bool = Field(
        ...,
        description="Enable or disable the LLM evaluator fix-up pass.",
    )
    guardrail: bool = Field(..., description="Enable or disable guardrail structural checks")
    use_agents_md: bool = Field(
        ...,
        description="Use AGENTS.md file + short CLI prompt instead of full inline prompt",
    )

    # FIXME: This should be specified at the level of EvalRunConfig.  Whenever we're launching Keystone, we should propagate those "wider-scoped" configs down into the relevant code so that they're avaiable to configure parameters.
    # Docker Hub mirror for pull-through caching
    docker_registry_mirror: str = Field(
        default_factory=lambda: os.environ.get("DOCKER_REGISTRY_MIRROR", ""),
        description=(
            "URL of Docker Hub pull-through cache mirror.  "
            "Set the DOCKER_REGISTRY_MIRROR environment variable or pass explicitly."
        ),
    )


# FIXME: This is the EvalConfig only for a single AgentConfig (which might be renamed to KeystoneConfig) -- maybe this should be called EvalConfiguration?
class EvalConfig(BaseModel):
    """Top-level eval configuration."""

    # Optional human-readable name for this eval configuration
    name: str | None = Field(..., description="Name for this eval configuration")

    # FIXME: Maybe should be called KeystoneConfig?
    agent_config: AgentConfig = Field(...)

    trials_per_repo: int = Field(
        default=1,
        description="Number of trials per repo. When >1, caching is automatically disabled.",
    )
    # FIXME: It doesn't make sense to have this value here.
    # These are computed from EvalRunConfig globals; not set directly in config files.
    s3_output_prefix: str = Field(
        ...,
        description="S3 prefix for per-repo results (set by EvalRunConfig, not manually).",
    )
    s3_repo_cache_prefix: str = Field(
        ...,
        description="S3 prefix for cached repo tarballs (set by EvalRunConfig, not manually).",
    )


# FIXME: rename to KeystoneRepoResult
class RepoResult(BaseModel):
    """Result from one trial: a single application of Keystone with a particular configuration to a single repo."""

    repo_entry: RepoEntry
    agent_config: AgentConfig | None = None
    trial_index: int | None = None

    # Differs from agent_config.success? -- Keystone can succeed but we can fail to package the result?
    # FIXME: Actually, document what causes this to be False.
    success: bool

    error_message: str | None = None

    # FIXME: Use the proper type here: BootstrapResult
    bootstrap_result: dict[str, Any] | None = None


# FIXME: Let's be consistent with naming and use a Result suffix instead of Output.
class EvalOutput(BaseModel):
    """Output of the entire eval run.

    This type is less important because we want to be able to analyze a partially completed run before this global summary is available.
    """

    # FIXME: Let's use a proper type here: VersionInfo
    keystone_version: dict[str, Any]

    # FIXME: This doesn't make sense to use a dict here.  Use a proper type.  I think this should be EvalRunConfig.  It shouldn't ever be None.
    eval_config: dict[str, Any] | None = Field(
        default=None,
        description="Snapshot of the EvalConfig used for this run.",
    )

    # FIXME: We don't need repos here anymore -- the versions are already pre-pinned.  Delete this field.
    repos: list[RepoEntry]  # Input repos with commit_hash pinned

    results: list[RepoResult]


# FIXME: Move all the input/config classes above all of the output/result classes.
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
