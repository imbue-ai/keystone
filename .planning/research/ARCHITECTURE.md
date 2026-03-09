# Architecture Research

**Domain:** Cloud execution observability — threading traceability data through an existing result/logging pipeline
**Researched:** 2026-03-09
**Confidence:** HIGH (based on direct codebase inspection)

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                       Eval Harness (Prefect)                      │
│  evals/flow.py: process_repo_task (N parallel tasks)             │
│  Spawns keystone CLI as subprocess, reads keystone_result.json    │
└────────────────────────┬─────────────────────────────────────────┘
                         │ subprocess (keystone CLI)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                     CLI Orchestrator                              │
│  keystone_cli.py: bootstrap()                                    │
│  Assembles BootstrapResult, logs CLIRunRecord to DB              │
│  ← This is where modal_sandbox_id must be injected               │
└──────┬──────────────────────────┬────────────────────────────────┘
       │                          │
       ▼                          ▼
┌─────────────────┐   ┌──────────────────────────────────────────┐
│  CachedRunner   │   │  AgentLog (agent_log.py)                 │
│  cached_runner  │   │  Tables: agent_run, cli_run              │
│  .py: wraps     │   │  cli_run.bootstrap_result_json ← target  │
│  inner runner   │   │  ensure_column_exists() for migrations   │
└──────┬──────────┘   └──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   ModalAgentRunner (modal_runner.py)              │
│  self._sandbox = modal.Sandbox.create(...)                       │
│  self._sandbox.object_id  ← sandbox ID is HERE                  │
│  Prints to stderr but never returns to caller                    │
└──────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Relevant to This Change |
|-----------|----------------|------------------------|
| `ModalAgentRunner` | Creates Modal sandbox, holds `self._sandbox.object_id` | Origin of sandbox ID |
| `AgentRunner` (ABC) | Defines interface: `run()`, `verify()`, `cleanup()`, `get_devcontainer_tarball()` | No new abstract method needed |
| `CachedAgentRunner` | Wraps inner runner; delegates `get_inference_cost()`, `get_agent_dir_tarball()` | Must also delegate `get_sandbox_id()` |
| `keystone_cli.py:bootstrap()` | Assembles `BootstrapResult`, logs `CLIRunRecord` | Reads sandbox ID, puts in `BootstrapResult` |
| `BootstrapResult` (schema.py) | Pydantic model stored as JSON in `cli_run.bootstrap_result_json` | Add optional `modal_sandbox_id: str \| None` field |
| `AgentExecution` (schema.py) | Sub-model of `BootstrapResult.agent` | Alternative placement (see trade-offs below) |
| `AgentLog` | Writes to `cli_run` and `agent_run` tables | No change needed — sandbox ID travels inside JSON blob |
| `ManagedProcess._stream_reader` | Bare `except Exception: pass` silently swallows connection errors | Add exception logging here |
| `evals/flow.py:process_repo_task` | Reads `keystone_result.json` (= `BootstrapResult` JSON) | Gets sandbox ID automatically if it's in `BootstrapResult` |

## Data Flow: Sandbox ID from Creation to DB

### Current Flow (ID is lost)

```
modal.Sandbox.create()
    → self._sandbox.object_id  (string, e.g. "sb-abc123")
    → print(f"Modal sandbox created: {sandbox_id}", file=sys.stderr)
    → DROPPED — never returned to CachedAgentRunner or CLI
```

### Target Flow (ID is preserved)

```
ModalAgentRunner.ensure_sandbox()
    → self._sandbox.object_id  stored as self._sandbox_id
    → new method: get_sandbox_id() -> str | None

CachedAgentRunner (wraps ModalAgentRunner)
    → new method: get_sandbox_id() -> str | None
    → delegates to self._inner.get_sandbox_id()
    → on cache hit: returns None (no sandbox was created)

keystone_cli.py:bootstrap()
    → calls runner.get_sandbox_id() after run() returns
    → passes into BootstrapResult construction

BootstrapResult
    → modal_sandbox_id: str | None = None   (new optional field)
    → serialized to JSON in cli_run.bootstrap_result_json

DB (cli_run table)
    → bootstrap_result_json column already exists
    → sandbox ID embedded in JSON blob — no schema migration needed

evals/flow.py
    → reads bootstrap_result (dict) from keystone_result.json
    → modal_sandbox_id available in dict for post-mortem queries
```

### Placement Decision: Where in BootstrapResult

Two options exist. The recommended option is documented here with rationale.

**Option A: Top-level field on `BootstrapResult`**

```python
class BootstrapResult(BaseModel):
    ...
    modal_sandbox_id: str | None = None  # ADD THIS
```

Why: Sandbox ID is execution infrastructure metadata, not agent-behavioral data. It belongs alongside `success`, `error_message`, and `cli_args` — all top-level infrastructure fields. `AgentExecution` represents the agent's work product; sandbox identity is the container that ran it.

**Option B: Field on `AgentExecution`**

```python
class AgentExecution(BaseModel):
    ...
    modal_sandbox_id: str | None = None  # alternative
```

Why not: `AgentExecution` is re-used in cached replays where no sandbox was created. Adding sandbox ID there creates a conceptual mismatch — a cached replay has `AgentExecution` data but no sandbox.

**Recommendation: Option A.** Consistent with how `cli_args` is placed — top-level infrastructure provenance.

## Architectural Patterns for Traceability in Cloud Execution Systems

### Pattern 1: Optional Getter with Default None

Infrastructure metadata that only some runner implementations produce (Modal has sandbox IDs, local runner doesn't) follows the "optional getter with default None" pattern already used in this codebase.

**Existing example:** `get_agent_dir_tarball()` and `get_inference_cost()` both return `Optional` types. `LocalAgentRunner` inherits the default implementation returning `None`. `ModalAgentRunner` overrides with the real value. `CachedAgentRunner` checks `self._cache_hit` before delegating.

**What:** Concrete method on base class returns `None`. Subclass overrides when it has data. Caller handles `None` gracefully.

**When to use:** When only one execution strategy produces a piece of metadata, and the absence is meaningful (not an error).

**Example:**

```python
# agent_runner.py (base class — no change)
def get_sandbox_id(self) -> str | None:
    return None  # default: not applicable

# modal_runner.py (override)
def get_sandbox_id(self) -> str | None:
    return self._sandbox.object_id if self._sandbox else None

# cached_runner.py (delegation pattern)
def get_sandbox_id(self) -> str | None:
    if self._cache_hit:
        return None  # No sandbox was created during cache replay
    return self._inner.get_sandbox_id()
```

### Pattern 2: Capture-at-Construction, Expose-via-Getter

The sandbox ID is captured inside `ensure_sandbox()` which is called during `run()`. The pattern here is: capture eagerly (at creation time), expose lazily (via getter called after `run()` completes). This is the same pattern used for `_exit_code`, `_devcontainer_tarball`, and `_cached_inference_cost`.

**What:** Store metadata as private instance variable at point of creation. Expose via public getter only after the caller's lifecycle is complete.

**Why:** Callers (the CLI) consume metadata in a single batch after `run()` finishes, not incrementally. Getter-based access keeps the interface clean without requiring iterator-level plumbing.

**Example (existing pattern in modal_runner.py):**

```python
# Capture during execution
self._sandbox = modal.Sandbox.create(...)
sandbox_id = self._sandbox.object_id
# ... (already printed to stderr)

# Expose via getter (new — mirrors existing pattern)
def get_sandbox_id(self) -> str | None:
    if self._sandbox is None:
        return None
    return self._sandbox.object_id
```

### Pattern 3: Optional Field on the Result Model (backward-compatible schema extension)

`BootstrapResult` is a Pydantic model serialized to JSON in the database. The safe way to add fields to a stored-JSON model is `field: Type | None = None`. Old rows that don't have the field deserialize without error (Pydantic fills the default). New rows carry the value.

**What:** Add `modal_sandbox_id: str | None = None` to `BootstrapResult`. No database migration needed because the value is stored inside the JSON blob, not as a separate column.

**When to use:** When the datum is naturally part of an existing JSON payload that the DB stores opaquely. Adding a separate column is only needed if you need to query/index by that value directly in SQL.

**Contrast with** the `agent_run` table where cache key components ARE separate columns (because the cache lookup queries them). Sandbox ID doesn't need SQL-level querying — it's only needed for human post-mortem lookup.

### Pattern 4: Exception Logging in Stream Readers

`ManagedProcess._stream_reader` has a bare `except Exception: pass` at line 91-93. This is the silent failure that makes sandbox termination invisible. The fix pattern for connection-dropped stream readers in long-running distributed systems:

**What:** Catch the exception, log at WARNING or ERROR level with context (process name, stream type), then continue. Don't re-raise — stream termination is expected on sandbox death.

**When:** The exception IS meaningful (it indicates the sandbox terminated unexpectedly), but is not actionable at the stream level (the process is already dead). Log it so the post-mortem can reconstruct the timeline.

**Example:**

```python
except Exception as exc:
    # Stream closed due to sandbox termination.
    # Log at WARNING so we can correlate with Modal sandbox lifecycle events.
    logger.warning(
        "[%s] %s stream closed unexpectedly: %s",
        self.prefix,
        stream_name.value,
        exc,
    )
```

## Component Boundaries

### What Should NOT Change

| Component | Why |
|-----------|-----|
| `CacheKey` and `AgentConfig` | Sandbox ID must NOT be part of the cache key. The same logical run (same prompt, same repo, same config) should produce the same cache entry regardless of which sandbox was assigned. Cache key = inputs only. |
| `AgentRunRecord` | Stores events + tarball for cache replay. Sandbox ID doesn't belong here — cached replays have no sandbox. |
| `agent_run` table schema | No SQL migration needed; value goes in the JSON blob in `cli_run`. |
| `AgentRunner.run()` signature | The runner interface yields `StreamEvent` objects. Sandbox ID is not an event. |

### What Needs Minimal Change

| Component | Change | Scope |
|-----------|--------|-------|
| `ModalAgentRunner` | Add `get_sandbox_id() -> str \| None` method | 4 lines |
| `AgentRunner` (base) | Add default `get_sandbox_id() -> str \| None` returning `None` | 5 lines |
| `CachedAgentRunner` | Add delegation method `get_sandbox_id()` | 6 lines |
| `keystone_cli.py` | Call `runner.get_sandbox_id()` and pass to `BootstrapResult` | 2 lines |
| `BootstrapResult` (schema.py) | Add `modal_sandbox_id: str \| None = None` | 1 line |
| `ManagedProcess._stream_reader` | Replace `pass` with `logger.warning(...)` | 3 lines |

## Build Order (Dependency Chain)

Each step can be shipped independently. Later steps depend on earlier ones.

**Step 1: Fix the silent failure (no dependencies)**
Change `ManagedProcess._stream_reader` bare `except Exception: pass` to log the exception. This is self-contained — no other component changes. Immediate diagnostic value even before sandbox ID is persisted.

**Step 2: Thread sandbox ID to BootstrapResult (depends on Step 1 being safe)**
1. Add `get_sandbox_id()` to `AgentRunner` base (default `None`)
2. Add `get_sandbox_id()` override to `ModalAgentRunner`
3. Add `get_sandbox_id()` delegation to `CachedAgentRunner`
4. Add `modal_sandbox_id: str | None = None` to `BootstrapResult`
5. Call `runner.get_sandbox_id()` in `keystone_cli.py:bootstrap()` and pass to `BootstrapResult`

These five changes form a single logical unit and should be done together. None is useful without the others.

**Step 3: Post-mortem tooling (depends on Step 2 — needs sandbox IDs in the DB)**
Standalone script that:
- Queries `cli_run.bootstrap_result_json` for rows where `json_extract(bootstrap_result_json, '$.modal_sandbox_id') IS NOT NULL`
- For each sandbox ID, calls Modal SDK to retrieve sandbox status/logs
This step has no dependencies on the keystone library; it only needs DB access and the Modal SDK.

## Cache Key Invariant: Sandbox ID Must Never Touch It

The cache key (`CacheKey` in `agent_log.py`) is computed from:
- `git_tree_hash` — content of the repo
- `prompt_hash` — the agent prompt
- `agent_config_json` — AgentConfig fields
- `cache_version` — explicit invalidation string

`AgentConfig.to_cache_key_json()` serializes all `AgentConfig` fields. Sandbox ID is NOT in `AgentConfig` and must not be. If sandbox ID were in the cache key, every new run (which gets a new sandbox) would be a cache miss, destroying the cache entirely.

The sandbox ID only flows through the non-cache path:
- `ModalAgentRunner` → `CachedAgentRunner.get_sandbox_id()` (None on cache hits) → `keystone_cli.py` → `BootstrapResult.modal_sandbox_id` → `cli_run.bootstrap_result_json`

On cache hits, `modal_sandbox_id` is `None` in the `BootstrapResult`. This is correct — the result was replayed, not re-executed in a sandbox.

## Integration Points

### Modal SDK

`modal.Sandbox.object_id` is the stable external identifier for a sandbox. It is accessible immediately after `modal.Sandbox.create()` returns (which is when `ensure_sandbox()` completes). No additional API call is needed.

For post-mortem, the Modal SDK exposes:
- `modal.Sandbox.from_id(sandbox_id)` — retrieve sandbox by ID (verify this is current API)
- Sandbox status and metadata via the retrieved object

**Confidence on Modal SDK post-mortem API: MEDIUM.** The `object_id` field and `Sandbox.create()` are confirmed by reading the existing codebase. The post-mortem API (`from_id` or equivalent) should be verified against current Modal docs before building Step 3.

### Database

No SQL schema migration is needed for Steps 1 and 2. The `cli_run.bootstrap_result_json` TEXT column already exists and stores the full `BootstrapResult` as JSON. Adding a field to `BootstrapResult` with a default of `None` means:
- Old rows: `json_extract(..., '$.modal_sandbox_id')` returns NULL (field absent = NULL in SQLite JSON)
- New rows: field is present with the sandbox ID string or null

For the post-mortem script (Step 3), SQLite's `json_extract()` function allows querying without any schema changes.

## Anti-Patterns

### Anti-Pattern 1: Adding Sandbox ID to the Cache Key

**What people do:** Add `modal_sandbox_id` to `AgentConfig` or `CacheKey` thinking it "belongs with" the run configuration.

**Why it's wrong:** The sandbox ID is an output of the run, not an input. Adding it to the cache key means every run is a cache miss, as each sandbox gets a new ID. The entire caching layer becomes useless.

**Do this instead:** Keep sandbox ID completely out of `CacheKey`, `AgentConfig`, and `AgentRunRecord`. It belongs only in `BootstrapResult` (the final output) and surfaces via a separate getter method.

### Anti-Pattern 2: Yielding Sandbox ID as a StreamEvent

**What people do:** Yield the sandbox ID as a special `StreamEvent` from `ModalAgentRunner.run()`, then parse it back out in the CLI.

**Why it's wrong:** `StreamEvent` is for agent output (text, tool calls, errors). Mixing infrastructure metadata (sandbox ID) into the event stream breaks the single-responsibility principle of the stream. The CLI would need fragile string parsing to extract it. The `AgentRunRecord` stores all events for cache replay — replaying a "sandbox ID event" makes no sense.

**Do this instead:** Use the getter pattern. `get_sandbox_id()` is called once after `run()` completes, same as `get_devcontainer_tarball()` and `get_inference_cost()`.

### Anti-Pattern 3: Storing Sandbox ID in a New DB Column

**What people do:** Add a `modal_sandbox_id TEXT` column to `cli_run` table, using `ensure_column_exists()`.

**Why it's usually wrong (here):** The `cli_run.bootstrap_result_json` already stores a complete JSON blob of all result data. Adding a separate column for one field creates two sources of truth that must be kept in sync. The column is only needed if you need SQL-level indexing/querying of sandbox IDs across rows.

**When it would be right:** If the post-mortem script needs to scan thousands of rows efficiently by sandbox ID. At the current scale (debugging tool, not analytics pipeline), a JSON `json_extract()` query is fast enough and avoids the dual-write complexity.

## Sources

- Direct codebase inspection: `keystone/src/keystone/modal/modal_runner.py` (lines 164-208, 183-184)
- Direct codebase inspection: `keystone/src/keystone/schema.py` (BootstrapResult, AgentExecution)
- Direct codebase inspection: `keystone/src/keystone/agent_log.py` (CacheKey, AgentRunRecord, ensure_column_exists)
- Direct codebase inspection: `keystone/src/keystone/cached_runner.py` (CachedAgentRunner delegation pattern)
- Direct codebase inspection: `keystone/src/keystone/keystone_cli.py` (BootstrapResult assembly, lines 614-641)
- Direct codebase inspection: `evals/flow.py` (process_repo_task result handling)
- Pattern reference: existing `get_agent_dir_tarball()` and `get_inference_cost()` delegation chain

---
*Architecture research for: Modal sandbox observability — sandbox ID threading*
*Researched: 2026-03-09*
