# Architecture

**Analysis Date:** 2026-03-09

## Pattern Overview

**Overall:** Multi-layered agent orchestration with modular LLM provider abstraction.

**Key Characteristics:**
- **CLI-driven execution**: Entry point (`keystone_cli.py`) orchestrates entire workflow
- **Pluggable LLM providers**: Abstract provider interface with Claude, Codex, and OpenCode implementations
- **Runner abstraction**: Local and Modal execution strategies, both implementing common interface
- **Transparent caching**: Cache layer wraps any runner without caller awareness
- **Streaming event model**: All agent output normalized to typed events (text, tool calls, cost, errors)
- **Multi-stage evaluation**: Initial generation → verification → repair (if needed)

## Layers

**CLI & Orchestration:**
- Purpose: User entry point, argument parsing, workflow sequencing
- Location: `keystone/src/keystone/keystone_cli.py`
- Contains: Typer command definitions, prompt assembly, cache initialization
- Depends on: All other layers
- Used by: Direct user invocation via `keystone` command

**Agent Runners:**
- Purpose: Execute agent code in isolated environment (local process or Modal sandbox)
- Location: `keystone/src/keystone/agent_runner.py` (abstract), `keystone/src/keystone/modal/modal_runner.py` (Modal implementation)
- Contains: Process spawning, devcontainer tarball generation, verification orchestration
- Depends on: LLM providers, process utilities, schema types
- Used by: Cached runner wrapper, CLI

**Cache Layer:**
- Purpose: Transparent cache lookup and storage using SQLite/PostgreSQL backend
- Location: `keystone/src/keystone/cached_runner.py`, `keystone/src/keystone/agent_log.py`
- Contains: Cache key computation, database operations, event replay
- Depends on: Agent runners, schema types
- Used by: CLI (wraps any runner)

**LLM Provider Abstraction:**
- Purpose: Normalize different LLM backends (Claude, Codex, OpenCode) to common interface
- Location: `keystone/src/keystone/llm_provider/` (base.py, registry.py, and implementations)
- Contains: Abstract AgentProvider interface, provider registry, backend-specific implementations
- Depends on: Anthropic SDK, OpenAI SDK
- Used by: Runners, evaluator, CLI

**Evaluation & Repair:**
- Purpose: Verify generated devcontainer works; if failed, call cheap LLM to fix issues
- Location: `keystone/src/keystone/evaluator.py`
- Contains: Error detection, fixer prompt assembly, file repair logic
- Depends on: Anthropic SDK, OpenAI SDK, schema types
- Used by: CLI after verification step

**Data Schemas & Models:**
- Purpose: Type-safe configurations and output representations
- Location: `keystone/src/keystone/schema.py`
- Contains: AgentConfig, KeystoneConfig, StreamEvent, LLMModel enums, result types
- Depends on: Pydantic
- Used by: All layers

**Evals Harness:**
- Purpose: Distributed test framework for running Keystone on multiple repositories
- Location: `evals/flow.py` (Prefect-based orchestration), `evals/eval_schema.py` (config/result types)
- Contains: Repository enumeration, S3 caching, per-repo result aggregation
- Depends on: Keystone library, Prefect, fsspec/S3
- Used by: Evaluation CLI (`evals/eval_cli.py`)

## Data Flow

**Primary Agent Generation Flow:**

1. User calls `keystone --project_root /path ...` (CLI entry)
2. CLI parses arguments → builds `KeystoneConfig` with agent configuration
3. Computes cache key from git tree hash, prompt hash, and agent config
4. Wraps runner in `CachedAgentRunner` for transparent cache lookup
5. Cache hit? → Replays stored events + restores devcontainer tarball
6. Cache miss? → Delegates to actual runner:
   - Extracts project archive (git archive)
   - Selects LLM provider from registry
   - Provider builds agent command with budget/model args
   - Runner spawns agent process (local or Modal)
   - Agent process runs in isolated container with devcontainer scaffolding
   - Streams stdout/stderr back to runner
7. Provider parses each stdout line into typed events (text, tool calls, cost)
8. Runner captures events and builds devcontainer tarball from agent's output
9. CLI records all events in database (if log_db provided)
10. Returns exit code and devcontainer tarball to caller

**Verification & Repair Flow:**

1. After agent completes, CLI calls `evaluate_agent_work()` (evaluator.py)
2. Evaluator builds docker image from generated Dockerfile
3. Runs test suite in container, captures results
4. If all tests pass → Success, cache the result
5. If tests fail/timeout/missing files → Calls evaluator LLM:
   - Assembles fixer prompt with error context + generated files + project context
   - Cheap LLM (Haiku or gpt-4o-mini) reads error + attempts one-shot fix
   - Fixed files written back to filesystem
   - Re-runs verification
6. Returns `VerificationResult` with pass/fail status

**Evals Harness Flow:**

1. Load `EvalRunConfig` from JSON file
2. Enumerate repositories from JSONL list
3. For each repo per trial:
   - Clone to temp directory (checkout pinned commit)
   - Compress to tarball, upload to S3
   - Submit Keystone task to Prefect
   - Monitor status, aggregate results
4. Collect per-repo results from S3
5. Compute summary metrics (pass rate, cost, time)

**State Management:**

- **Immutable execution context**: CLI captures all config in `KeystoneConfig` before execution
- **Cache key components**: Git tree hash, prompt MD5, agent config JSON, version string
- **Event stream**: All agent I/O normalized to `StreamEvent(stream: StreamType, line: str)`
- **Database persistence**: CLI run records every invocation; agent runs stored only on cache miss
- **Tarball format**: `.devcontainer/` directory compressed for caching and distribution

## Key Abstractions

**AgentProvider (LLM Backend Abstraction):**
- Purpose: Normalize Claude, Codex, OpenCode to common interface
- Examples: `keystone/src/keystone/llm_provider/claude.py`, `keystone/src/keystone/llm_provider/codex.py`, `keystone/src/keystone/llm_provider/opencode.py`
- Pattern: Each implements `build_command()` (assemble CLI args), `parse_stdout_line()` (decode events), and cost estimation
- Allows provider-agnostic agent spawning while accommodating backend differences

**AgentRunner (Execution Environment Abstraction):**
- Purpose: Support local process and Modal cloud sandbox interchangeably
- Examples: `LocalAgentRunner`, `ModalAgentRunner`
- Pattern: Both inherit from `AgentRunner` abstract class, implement `run()`, `verify()`, `get_devcontainer_tarball()`
- Enables same CLI code to work with local Docker or remote Modal infrastructure

**StreamEvent (Unified Agent Output):**
- Purpose: Normalize different LLM backends' output formats
- Examples: `AgentTextEvent`, `AgentToolCallEvent`, `AgentToolResultEvent`, `AgentCostEvent`, `AgentErrorEvent`
- Pattern: Providers parse their backend's native output into events; CLI consumes uniform stream
- Supports event-driven UI (streamed output), cost tracking, and logging

**Cache Key:**
- Purpose: Content-addressable storage (same input always produces same cache entry)
- Composed of: git tree hash + prompt MD5 + agent config JSON + optional version string
- Pattern: If any input changes, cache key changes; failed runs never cached (only exit_code==0)

## Entry Points

**Keystone CLI Command:**
- Location: `keystone/src/keystone/keystone_cli.py` `bootstrap()` command
- Triggers: User runs `keystone --project_root /path ...`
- Responsibilities:
  1. Parse arguments (project root, LLM provider/model, budget, feature toggles)
  2. Load/create project context (git info, cache key)
  3. Build and apply prompt
  4. Execute agent (possibly via cache)
  5. Run verification
  6. Optionally fix failures via evaluator
  7. Output results and exit

**Eval CLI Command:**
- Location: `evals/eval_cli.py`
- Triggers: User runs `eval-harness` or Python script execution
- Responsibilities:
  1. Load EvalRunConfig from JSON
  2. Enumerate repos from JSONL
  3. Create Prefect flow for distributed execution
  4. Submit per-repo Keystone tasks
  5. Aggregate results, upload to S3

## Error Handling

**Strategy:** Three-tier fallback with explicit error events.

**Patterns:**

1. **Agent Runtime Errors**: If agent process exits non-zero, exit code propagated. Provider parses error lines as `AgentErrorEvent`. Cached runner replays cached error state if retry requested.

2. **Verification Failures**: If docker build fails or tests fail, evaluator attempts repair. If repair fails, returns `VerificationResult` with `exit_code != 0`. CLI reports failure.

3. **Cost Budget Exceeded**: Provider tracks cumulative cost. If exceeds `max_budget_usd`, agent terminates with `AgentErrorEvent`. Runner catches and propagates.

4. **Timeout**: Process-level timeout enforced by `timeout` command. Exit code 124 detected, `timed_out` flag set.

5. **Provider Lookup Failure**: Invalid provider name raises `ValueError` with available options listed.

## Cross-Cutting Concerns

**Logging:** ISO8601 timestamps, thread IDs, module names via `ISOFormatter` in `logging_utils.py`. Agent output to stderr, structured logs to stdout.

**Validation:** All user inputs (paths, model names, budgets) validated via Pydantic schemas. CLI rejects invalid config early.

**Authentication:** Handled by provider implementations. Claude reads `ANTHROPIC_API_KEY`, Codex reads `OPENAI_API_KEY`, Opencode reads both. No auth abstraction — each provider manages its own.

**Resource Cleanup:** Temp directories created during agent runs cleaned up after process completes. Modal resources cleaned up automatically by Modal runtime.

**Determinism:** Cache enables deterministic results. Same config + same repo = same output (or cache miss if cache version changed).

---

*Architecture analysis: 2026-03-09*
