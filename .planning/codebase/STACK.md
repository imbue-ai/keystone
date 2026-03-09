# Technology Stack

**Analysis Date:** 2026-03-09

## Languages

**Primary:**
- Python 3.12 - Core application and agent infrastructure (specified in `pyproject.toml` and `.python-version`)

**Secondary:**
- TypeScript/JavaScript - Minimal usage; only `package.json` present (empty config)
- Bash - Guardrail scripts and Docker initialization scripts (`keystone/src/keystone/guardrail.sh`, `keystone/src/keystone/modal/start_dockerd.sh`)

## Runtime

**Environment:**
- Python 3.12 (via `.python-version`)
- uv package manager with workspace support (`tool.uv.workspace` in `pyproject.toml`)

**Package Manager:**
- uv - Modern Python package manager with lockfile support
- Lockfile: `package-lock.json` present (minimal npm dependency)

## Frameworks

**Core:**
- Typer 0.21.1+ - CLI framework for `keystone_cli.py` and `eval_cli.py`
- Pydantic 2.0.0+ - Data validation and schema definition (used throughout for config, events, results)

**Orchestration & Execution:**
- Modal 1.3.1+ - Serverless container execution for sandboxing agent runs (`keystone/src/keystone/modal/modal_runner.py`)
- Prefect 3.0+ - Workflow orchestration and distributed task execution (`evals/flow.py`)

**LLM Integrations:**
- Anthropic SDK 0.40.0+ - Claude Code integration via Claude API (`keystone/src/keystone/llm_provider/claude.py`)
- OpenAI SDK 1.0.0+ - OpenAI integration support (Codex provider, cost evaluation)

**Data & Analysis:**
- Pandas 2.0.0+ - Data manipulation and SQL operations (`keystone/src/keystone/agent_log.py`)
- SQLAlchemy 2.0.0+ - ORM for database operations (SQLite and PostgreSQL support)
- PyArrow 14.0+ - Columnar data format for evaluation results
- fsspec 2024.0+ - Filesystem abstraction for S3 access (`evals/flow.py`)
- s3fs 2024.0+ - S3 filesystem implementation via fsspec
- boto3 1.28+ - AWS SDK (transitively via s3fs/fsspec)

**Testing & Quality:**
- Pytest 9.0.2+ - Test framework with markers for different test types
- pytest-xdist 3.0+ - Distributed/parallel test execution
- Syrupy 4.0.0+ - Snapshot testing for regression detection
- Ruff 0.9.0+ - Python linter and formatter
- Pyright 1.1.390+ - Static type checker
- Pre-commit 4.0.0+ - Git hook framework for quality gates

**Visualization & Notebooks:**
- IPython/ipykernel 7.1.0+ - Interactive Python kernel
- nbformat 4.2.0+ - Jupyter notebook handling
- Plotly 6.5.2+ - Interactive data visualization
- ipywidgets 8.1.8+ - Interactive widgets for Jupyter
- anywidget 0.9.21+ - Framework for custom widgets
- ipyaggrid 0.5.4+ - Data grid widget

**Utilities:**
- Rich 14.0.0+ - Terminal formatting and tables
- JUnit Parser 3.0.0+ - JUnit XML test report parsing
- json5 0.9+ - JSON5 format support for configs

## Key Dependencies

**Critical:**
- anthropic 0.40.0+ - Why it matters: Primary LLM provider for agent execution in devcontainer generation
- modal 1.3.1+ - Why it matters: Provides secure sandboxing for agent execution with Docker support
- typer 0.21.1+ - Why it matters: CLI framework for `keystone` command-line tool
- pydantic 2.0.0+ - Why it matters: Schema validation for all configs, events, and results

**Infrastructure:**
- sqlalchemy 2.0.0+ - Database abstraction for caching and analytics
- pandas 2.0.0+ - Data analysis and database operations
- fsspec 2024.0+ - Cloud storage abstraction (required for S3 integration)
- s3fs 2024.0+ - S3 filesystem layer
- boto3 1.28+ - AWS SDK for cloud operations

## Configuration

**Environment:**
- `.envrc` - Direnv configuration for development environment setup
- `.envrc.private` - Private environment overrides
- `pyrightconfig.json` - Pyright type checking configuration (`keystone`, `evals`, `modal_registry` included)
- `.python-version` - Python version pinning (3.12)

**Build:**
- `pyproject.toml` - Python package metadata and dependencies (workspace root)
- `keystone/pyproject.toml` - Keystone package (entry point: `keystone = "keystone.keystone_cli:main"`)
- `evals/pyproject.toml` - Evals harness package (entry point: `eval-harness = "eval_cli:app"`)
- `modal_registry/pyproject.toml` - Modal registry cache service
- `.pre-commit-config.yaml` - Pre-commit hooks configuration
- `pytest.ini` - Pytest configuration for root tests

## Platform Requirements

**Development:**
- Python 3.12
- Modal account and credentials (for sandbox execution)
- Anthropic API key (`ANTHROPIC_API_KEY` environment variable)
- OpenAI API key (`OPENAI_API_KEY` environment variable, optional for Codex provider)
- direnv (for `.envrc` support)

**Production:**
- Modal cloud platform (required for agent execution)
- Optional: PostgreSQL or SQLite database for logging/caching (`--log_db` parameter)
- Optional: AWS S3 credentials (for evaluation harness)
- Anthropic API key for inference (billed per token)

---

*Stack analysis: 2026-03-09*
