# External Integrations

**Analysis Date:** 2026-03-09

## APIs & External Services

**LLM Providers:**
- Claude (Anthropic) - Primary agent backend for devcontainer generation
  - SDK/Client: `anthropic` 0.40.0+
  - Auth: `ANTHROPIC_API_KEY` environment variable
  - Implementation: `keystone/src/keystone/llm_provider/claude.py` - Parses stream-json output from `claude` CLI
  - Usage: Agent execution, evaluation/fixing of failed runs via `keystone/src/keystone/evaluator.py`

- OpenAI Codex - Alternative agent backend
  - SDK/Client: `openai` 1.0.0+
  - Auth: `OPENAI_API_KEY` and `CODEX_API_KEY` environment variables
  - Implementation: `keystone/src/keystone/llm_provider/codex.py` - Parses Codex CLI JSON events
  - Usage: Optional alternative to Claude provider

**Container Execution:**
- Modal - Serverless container platform for sandboxing agent execution
  - SDK/Client: `modal` 1.3.1+
  - Auth: Modal credentials configured via Modal dashboard / CLI
  - Implementation: `keystone/src/keystone/modal/modal_runner.py` - Creates remote Docker containers
  - Usage: Runs `claude` or `codex` CLI in isolated Modal Functions; Docker images built via `keystone/src/keystone/modal/image.py`

## Data Storage

**Databases:**
- SQLite (local, default)
  - Connection: `sqlite:///path` or file path (defaults to `~/.imbue_keystone/log.sqlite`)
  - Client: SQLAlchemy 2.0.0+ with pandas integration
  - Implementation: `keystone/src/keystone/agent_log.py` - `AgentRunLog` class manages cache and analytics
  - Tables: `cli_run` (every CLI invocation), `agent_run` (actual agent executions)

- PostgreSQL (optional, production)
  - Connection: `postgresql://user:pass@host/db` via `--log_db` parameter
  - Client: SQLAlchemy 2.0.0+ with pandas integration
  - Implementation: Same as SQLite via SQLAlchemy abstraction
  - Tables: `cli_run`, `agent_run` (schema agnostic)

**Object Storage:**
- AWS S3 - Repository tarball caching and evaluation results
  - SDK/Client: `boto3` 1.28+ (via `s3fs` / `fsspec`)
  - Auth: AWS credentials (from environment or `~/.aws/credentials`)
  - Implementation: `evals/flow.py` - `_s3_read_bytes()`, `_s3_write_text()`, `_s3_exists()`
  - Usage:
    - Store cloned repository tarballs for reproducible evals (`evals/flow.py:_clone_repo_to_tarball()`)
    - Cache built Docker images
    - Upload eval results as JSON

**File Storage:**
- Local filesystem - Primary output for devcontainer generation
  - Devcontainer files written to `.devcontainer/` directory
  - Test artifacts collected in `--test_artifacts_dir` (required parameter)
  - Log database defaults to `~/.imbue_keystone/log.sqlite`

## Authentication & Identity

**Auth Provider:**
- None (no authentication layer)

**API Keys Required:**
- `ANTHROPIC_API_KEY` - Required for Claude provider (`keystone/src/keystone/llm_provider/claude.py:env_vars()`)
- `OPENAI_API_KEY` - Required only when using Codex provider (`keystone/src/keystone/llm_provider/codex.py:env_vars()`)
- Modal credentials - Required for Modal sandbox execution (configured via Modal CLI)
- AWS credentials (optional) - Required only if using S3 for eval harness

**Implementation:**
- Custom per-provider via `keystone/src/keystone/llm_provider/base.py:AgentProvider.env_vars()` method
- Environment variables passed to sandboxed processes in Modal

## Monitoring & Observability

**Error Tracking:**
- None detected

**Logs:**
- Python logging to stdout/stderr
- Structured logging via `keystone/src/keystone/logging_utils.py`
- Database-backed audit trail: `cli_run` and `agent_run` tables in `--log_db`
- Modal sandbox output streamed and logged in `keystone/src/keystone/modal/modal_runner.py:ManagedProcess`
- Prefect logging integration in `evals/eval_cli.py` and `evals/flow.py`

## CI/CD & Deployment

**Hosting:**
- Modal cloud - Serverless container execution (required)
- Local execution via Docker optional (requires Docker daemon)

**CI Pipeline:**
- None in codebase; test infrastructure in `pytest.ini`
- GitHub Actions workflow templates in `.github/` directory (not examined in detail)

**Package Distribution:**
- PyPI (inferred from `uvx --from` install method in documentation)
- GitHub releases (referenced in README)

## Environment Configuration

**Required env vars (runtime):**
- `ANTHROPIC_API_KEY` - For Claude provider (required)
- `OPENAI_API_KEY` - For Codex provider (optional, only if using Codex)

**Required env vars (development):**
- `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` - Modal API credentials
- AWS credentials (if using eval harness S3)

**Secrets location:**
- Environment variables (`.envrc`, `.envrc.private`)
- AWS credentials in `~/.aws/credentials`
- Modal credentials via Modal CLI

## Webhooks & Callbacks

**Incoming:**
- None detected

**Outgoing:**
- Modal task completion callbacks (implicit via `modal.Function.remote()`)
- Prefect task futures and waits (`evals/flow.py:wait()`)

## Request/Response Patterns

**Claude Provider:**
- Stream: JSON Lines format via `claude` CLI with `--output-format stream-json`
- Events: `"type": "assistant"`, `"type": "result"` containing usage and cost data
- Streaming tokens: `usage.input_tokens`, `usage.output_tokens`, `cached_tokens`, `cache_creation_tokens`
- Implementation: `keystone/src/keystone/llm_provider/claude.py:parse_stdout_line()`

**Codex Provider:**
- Stream: JSON Lines via `codex exec --json`
- Events: `"type": "turn.completed"`, `"type": "item.completed"`, `"type": "error"`
- Implementation: `keystone/src/keystone/llm_provider/codex.py:parse_stdout_line()` and `_parse_item()`

**S3 Operations:**
- Via fsspec abstraction (`evals/flow.py`)
- Read/write tarballs, JSON results, cache files
- No explicit retry logic; relies on boto3 defaults

**Prefect Flows:**
- Task-based execution with futures (`evals/flow.py:eval_flow()`)
- Thread pool task runner for parallelization (`evals/eval_cli.py:ThreadPoolTaskRunner`)

---

*Integration audit: 2026-03-09*
