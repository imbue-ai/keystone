"""Configuration schemas for the eval harness."""

from typing import Literal

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for the agent execution."""

    # Model configuration
    model: str = Field(default="claude-sonnet-4-20250514", description="Model to use")
    max_budget_usd: float = Field(default=1.0, description="Maximum budget per repo")

    # Git source for bootstrap_devcontainer
    bootstrap_git_url: str = Field(
        default="https://github.com/imbue-ai/bootstrap_devcontainer",
        description="Git URL for bootstrap_devcontainer",
    )
    bootstrap_git_ref: str | None = Field(
        default=None,
        description="Git ref (branch, tag, or commit hash) to use. "
        "If None, auto-resolves: uses current commit hash when running from the "
        "bootstrap_devcontainer repo (requires clean tree), otherwise 'prod'.",
    )

    # Execution settings
    timeout_minutes: int = Field(default=30, description="Timeout per repo in minutes")

    # Cache settings
    sqlite_cache_dir: str | None = Field(
        default="~/.cache/bootstrap_devcontainer",
        description="SQLite cache directory (None to disable caching)",
    )

    # Log database (for testing, use DEFAULT_TESTING_LOG_PATH)
    log_db: str | None = Field(
        default=None,
        description="Database for logging/caching. SQLite path or postgresql:// URL",
    )

    # Modal execution
    agent_in_modal: bool = Field(
        default=True,
        description="Run agent in Modal sandbox (default) or locally",
    )


class RepoEntry(BaseModel):
    """A single entry from the repo_list JSONL file."""

    s3_repo_tarball: str = Field(..., description="S3 URI to the repo tarball")


class WorkerResult(BaseModel):
    """Result from processing a single repo."""

    # Input reference
    s3_repo_tarball: str

    # Success/failure
    success: bool
    error_message: str | None = None

    # BootstrapResult data (if successful)
    bootstrap_result: dict | None = None

    # Output artifacts (S3 URIs)
    devcontainer_tarball_s3: str | None = None
    session_jsonl_s3: str | None = None


class EvalConfig(BaseModel):
    """Top-level eval configuration."""

    agent_config: AgentConfig = Field(default_factory=AgentConfig)

    # Execution mode - determines which Prefect task runner to use
    # "local" = ThreadPoolTaskRunner (default)
    # "process" = ProcessPoolTaskRunner (parallel processes)
    # "dask" = DaskTaskRunner (distributed, requires prefect-dask)
    execution_mode: Literal["local", "process", "dask"] = Field(
        default="local",
        description="Task runner mode: local (threads), process (parallel), dask (distributed)",
    )

    # Output settings
    output_s3_prefix: str = Field(
        default="s3://int8-datasets/eval-results/", description="S3 prefix for output artifacts"
    )

    # Parallelism
    max_workers: int = Field(default=4, description="Max parallel workers")
