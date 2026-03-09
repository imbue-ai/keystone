# Project Research Summary

**Project:** Modal Sandbox Observability — Unexpected Termination Diagnostics
**Domain:** Cloud execution observability — threading traceability through an existing result/logging pipeline
**Researched:** 2026-03-09
**Confidence:** HIGH (SDK source + codebase inspection) / LOW (Modal post-mortem API data richness)

## Executive Summary

Modal sandboxes die after approximately 45 minutes during large parallel eval runs, and the current system has no way to determine which sandbox failed, when it failed, or why. Three structural gaps combine to make failures invisible: the sandbox ID is printed to stderr but never persisted, stream reader exceptions are silently swallowed by a bare `except Exception: pass`, and there is no post-mortem tooling to query Modal's API even if an ID were available. This is a narrow, well-defined diagnostic problem — not a general observability platform effort — and it can be closed with three focused changes totaling fewer than 30 lines of production code.

The recommended approach is strictly sequential: fix the silent failure first (3 lines, immediate diagnostic value on the next eval run), then thread the sandbox ID through the existing result pipeline into the database JSON blob (5 coordinated changes, no schema migration), then build a post-mortem script once real IDs exist in the database to query against. The entire project fits within the existing architecture: `BootstrapResult` as the persistence vehicle, the getter delegation pattern already used by `get_devcontainer_tarball()` and `get_inference_cost()`, and SQLite's `json_extract()` for querying. No new infrastructure, no new dependencies, no schema migrations.

The primary risk is building in the wrong order. The post-mortem script is useless without IDs in the database; IDs in the database are less useful without exception-type logging to correlate against; and the root cause (OOM vs. quota exhaustion vs. gRPC reset vs. idle_timeout) cannot be determined without the exception type that Phase 1 surfaces. A secondary risk is misplacing the sandbox ID in `AgentConfig` or `CacheKey`, which would silently destroy the caching layer. Both risks are avoided by strict adherence to the three-phase build order and the architectural constraint that sandbox ID is an output, not an input.

## Key Findings

### Recommended Stack

The project requires no new dependencies. `modal==1.3.1` is already installed and provides all needed introspection: `Sandbox.from_id(sandbox_id)` for post-mortem lookup, the `GenericResult` protobuf with status enum and optional exception string, and named exception types (`SandboxTerminatedError`, `SandboxTimeoutError`, `ResourceExhaustedError`) that distinguish failure categories. The `modal_proto.api_pb2` package is installed automatically as a dependency and was directly inspected to confirm field availability.

The critical SDK constraint is that `Sandbox.list()` hardcodes `include_finished=False` — terminated sandboxes cannot be enumerated. The only post-mortem path is `from_id()` with a known ID, making ID persistence the prerequisite for everything else. Post-termination data is sparse compared to mature platforms: only `returncode` (derived from `GenericResult.status`), the optional `exception` string, and tags are retrievable. No logs, no resource metrics, no structured termination reason.

**Core technologies:**
- `modal==1.3.1` (installed): Sandbox introspection via `from_id()`, `poll()`, `wait()`, named exceptions — already present, no upgrade needed
- `modal_proto.api_pb2` (installed as dependency): `GenericResult` status enum, `SandboxInfo` fields — directly inspected from source
- SQLite `json_extract()`: Queries `cli_run.bootstrap_result_json` without schema migration — already in use by the system

### Expected Features

The three must-have features form a dependency chain and together close the primary diagnostic gap. Everything else is explicitly deferred.

**Must have (table stakes — P1, ship together as three phases):**
- **Log exception in `_stream_reader`** — surfaces exception type and timestamp when stream closes; without this, failures are completely invisible; 3 lines, zero dependencies
- **`modal_sandbox_id` in `BootstrapResult`** — persists the ID through the result pipeline into the DB JSON blob; without this, post-mortem lookup is impossible; 5 coordinated changes totaling ~20 lines
- **Post-mortem query script** — translates a sandbox ID into whatever Modal exposes about that sandbox's terminal state; depends on IDs existing in the DB from a completed eval run

**Should have (P2, add after initial diagnostic data exists):**
- Failure classification by exception type (`type(exc).__name__` in the warning log) — already partially captured once P1 logging is in place
- Promote `print()` to `logger.info()` in `ensure_sandbox()` — improves structured log fidelity, cosmetic change

**Defer (v2+, after root cause is known):**
- Retry logic — only valid after failure mode is understood
- Heartbeat monitoring — adds complexity; current evidence suggests abrupt termination, not silent hang
- Separate `modal_sandbox_id` DB column — only needed at scale that doesn't yet exist
- Eval viewer sandbox ID display — premature until feature proves durable

### Architecture Approach

The change threads a single string (the sandbox object ID) through an existing delegation chain using patterns already in the codebase. `ModalAgentRunner` captures the ID at sandbox creation time and exposes it via a new `get_sandbox_id() -> str | None` getter. `AgentRunner` (base class) provides a default returning `None` so local runners require no change. `CachedAgentRunner` delegates to the inner runner on cache misses and returns `None` on cache hits (correct — a cached replay has no sandbox). `keystone_cli.py` calls `runner.get_sandbox_id()` after `run()` completes and injects the value into `BootstrapResult`. `BootstrapResult` adds one optional field with a `None` default, which serializes into the existing `cli_run.bootstrap_result_json` blob with no schema migration.

**Major components:**
1. `ManagedProcess._stream_reader` (modal_runner.py:91-93) — replace bare `except Exception: pass` with `logger.warning()` including `type(exc).__name__` and stream name
2. `ModalAgentRunner` / `AgentRunner` / `CachedAgentRunner` — add `get_sandbox_id()` getter delegation chain (mirrors `get_devcontainer_tarball()` pattern)
3. `BootstrapResult` (schema.py) — add `modal_sandbox_id: str | None = None` (backward-compatible Pydantic optional field)
4. `keystone_cli.py:bootstrap()` — call `runner.get_sandbox_id()` and pass result to `BootstrapResult` construction
5. Post-mortem script (new, standalone) — queries `cli_run` table via `json_extract()`, calls `modal.Sandbox.from_id()` for each ID

### Critical Pitfalls

1. **Silent `except Exception: pass` in `_stream_reader`** — `SandboxTerminatedError`, `ResourceExhaustedError`, and gRPC errors are all swallowed identically. Fix: replace `pass` with `logger.warning("[%s] %s stream closed unexpectedly: %s: %s", prefix, stream_name.value, type(exc).__name__, exc)`. Critical: include `type(exc).__name__` — some Modal exceptions have empty string representations, so `str(exc)` alone may not identify the type.

2. **Sandbox ID placed in `AgentConfig` or `CacheKey`** — this destroys the caching layer silently (every run becomes a cache miss). Sandbox ID is an output, not an input. It belongs only in `BootstrapResult`. Verify cache hit rate is unchanged after Phase 2 ships.

3. **Building Phase 3 before Phase 2 data exists** — the post-mortem script will return zero rows if no eval run has completed with Phase 2 deployed. Sequence strictly: ship Phase 2, run at least one production eval, verify `json_extract(bootstrap_result_json, '$.modal_sandbox_id')` returns non-NULL values, then build Phase 3.

4. **Assuming the 45-minute failure is a timeout** — the sandbox `timeout=` is set to `2x time_limit_seconds` (4 hours for a 2-hour limit). The more likely causes are Modal-side OOM kill from Docker-in-Docker memory pressure, `ResourceExhaustedError` from parallel quota exhaustion, or gRPC connection reset after ~45 minutes of sustained load. Do not adjust any parameters before Phase 1 data identifies the exception type.

5. **Misidentifying the failure layer** — the failure can occur at four distinct layers (agent OOM inside sandbox, Docker daemon inside sandbox, Modal killing the container, gRPC network reset). Only the exception type from `_stream_reader` can distinguish layer 3 (`SandboxTerminatedError`) from layer 4 (gRPC/`ConnectionError`). Layers 1 and 2 would manifest as clean stream termination with no agent exit code, not as stream exceptions.

## Implications for Roadmap

Based on research, a strict three-phase build order is required by the dependency structure. No phases can be safely parallelized — each depends on the previous.

### Phase 1: Fix Silent Failure
**Rationale:** This is the only change with zero dependencies. It provides immediate diagnostic value on the very next eval run that experiences a failure. Without it, all other work produces data that cannot be interpreted. Shipping Phase 2 without Phase 1 means you have a sandbox ID but no context for when or why the stream closed.
**Delivers:** Exception type, exception message, and stream name logged at WARNING level whenever `_stream_reader` encounters an unexpected termination. On the next failing eval run, logs will show `SandboxTerminatedError` vs `ResourceExhaustedError` vs connection errors — immediately narrowing the root cause hypothesis space.
**Addresses:** Table-stakes feature "logged exception on stream failure" and "exception type logged"; implicitly delivers "timestamp of stream failure" via the logging framework.
**Avoids:** Pitfalls 1, 5, and 6 — the silent failure, the timeout misattribution, and the layer misidentification.

### Phase 2: Thread Sandbox ID to BootstrapResult
**Rationale:** The five changes in this phase form an atomic unit — none is useful without the others. This phase must ship after Phase 1 (so stream logs provide correlating context) and before Phase 3 (so IDs exist in the DB to query). After this phase ships, a production eval run must be executed to populate IDs in the database before Phase 3 begins.
**Delivers:** `modal_sandbox_id` populated in `cli_run.bootstrap_result_json` for every eval run that uses a Modal sandbox. Also surfaces in `keystone_result.json` and, by extension, `eval_result.json` uploaded to S3 — for free, with no additional code.
**Uses:** `modal==1.3.1` `sandbox.object_id` (already accessible, just not persisted). Pydantic optional-field pattern for backward-compatible schema extension.
**Implements:** Getter delegation chain (Pattern 1 + Pattern 2 from ARCHITECTURE.md), optional field on `BootstrapResult` (Pattern 3).
**Avoids:** Pitfall 2 (lost sandbox ID), Pitfall 3 (ID in cache key — verify cache hit rate after shipping).

### Phase 3: Post-Mortem Query Script
**Rationale:** This phase can only be built after Phase 2 has shipped AND at least one production eval run has completed with failures, so there are real sandbox IDs in the database to work with. The script is the payoff: it closes the loop from "which sandbox?" to "what does Modal say about it?"
**Delivers:** A standalone script that queries `cli_run` for `modal_sandbox_id` values in a time window, calls `modal.Sandbox.from_id()` for each, and prints `returncode`, status enum, and `exception` string. Enables an engineer to answer "what happened to sandbox X?" without touching the Modal dashboard.
**Uses:** `modal.Sandbox.from_id()` (HIGH confidence from SDK source), `GenericResult` status enum (HIGH confidence), `json_extract()` SQLite queries.
**Avoids:** Pitfall 4 (build after data exists — verify with a raw SQL query first).

### Phase Ordering Rationale

- **Phase 1 before Phase 2:** Exception logs give context that makes the persisted sandbox ID meaningful. Without Phase 1, you have an ID but no correlated log entry showing when the stream closed or what exception type was raised.
- **Phase 2 before Phase 3:** The post-mortem script has no data to work with until IDs are persisted. Building Phase 3 before Phase 2 produces a script that returns zero results and wastes debugging time.
- **Production eval run between Phase 2 and Phase 3:** This is not a code dependency but a data dependency. The script is only useful after IDs exist in the database. Schedule a full parallel eval run after Phase 2 ships specifically to populate data.
- **No parallelism:** All three phases depend on the previous in a strict chain. The total implementation is small enough (fewer than 30 production lines) that sequential execution is faster than coordination overhead.

### Research Flags

Phases with well-documented, standard patterns (no additional research needed):
- **Phase 1:** Exception logging in stream readers is a universal pattern. No research needed.
- **Phase 2:** The getter delegation pattern is already established in this codebase (`get_devcontainer_tarball()`, `get_inference_cost()`). Pydantic optional fields are standard. No research needed.

Phases that may need validation during implementation:
- **Phase 3:** What data `modal.Sandbox.from_id()` actually returns for a terminated sandbox has LOW confidence. The SDK source confirms `returncode` and `_result.status` are accessible, but the `exception` field may be empty for many termination types. Build the script defensively — handle empty fields gracefully and treat the Modal dashboard URL as the fallback for human investigation. Validate by manually calling `from_id()` on a known-terminated sandbox before writing the full script.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Sourced directly from installed SDK source at `.venv/lib/python3.12/site-packages/modal/`; `modal==1.3.1` confirmed. `include_finished=False` is literally in the source code — not inferred. |
| Features | HIGH (scope) / LOW (Modal API richness) | Feature scope is codebase-grounded with direct file inspection. What Modal's post-mortem API actually returns is not well-documented; `from_id()` is confirmed but data fields on terminated sandboxes are uncertain. |
| Architecture | HIGH | Direct codebase inspection of all relevant files. Getter delegation pattern is confirmed working from existing examples. No schema migration needed is confirmed from existing JSON blob approach. |
| Pitfalls | HIGH (identification) / MEDIUM (Modal-specific causes) | Silent exception and cache key risks are confirmed from source. The 45-minute failure root cause hypotheses (OOM, quota, gRPC reset, idle_timeout) are informed by Modal docs and patterns, but the actual cause is unknown until Phase 1 logging runs. |

**Overall confidence:** HIGH for implementation plan; LOW for post-mortem data richness (what `from_id()` returns for a terminated sandbox).

### Gaps to Address

- **Modal post-mortem data richness:** `modal.Sandbox.from_id()` on a terminated sandbox is confirmed to return `returncode` and `GenericResult.status`. Whether the `exception` string is populated — and with what content — for OOM kills, quota exhaustion, and gRPC resets is not documented. Handle during Phase 3 by: (1) testing `from_id()` manually on a sandbox that has been terminated before writing the full script, (2) building the script to gracefully handle all fields being None/empty, (3) always including the Modal dashboard URL in output as the authoritative fallback.

- **45-minute failure root cause:** This gap is structural — it cannot be resolved by research, only by running Phase 1 and observing the next failure. After Phase 1 ships, the first failing eval run will surface the exception type and close this gap.

- **`idle_timeout` status in current `Sandbox.create()` call:** Research identified `idle_timeout` as a possible cause (a sandbox paused between agent execution and verification could be killed). The current `ensure_sandbox()` call at modal_runner.py:175-181 does not explicitly set `idle_timeout`, but this should be verified during Phase 2 implementation and confirmed is not being set via a Modal config file or SDK default.

## Sources

### Primary (HIGH confidence)
- `modal/sandbox.py` (installed at `.venv/lib/python3.12/site-packages/modal/sandbox.py`) — `from_id()`, `poll()`, `wait()`, `list()`, `returncode`, `set_tags()`, `get_tags()`
- `modal/_object.py` — `object_id` property, `_Object` base class
- `modal_proto/api_pb2` — `GenericResult`, `SandboxInfo`, `TaskInfo`, `SandboxListRequest` fields (inspected via `.DESCRIPTOR.fields`)
- Direct codebase inspection: `keystone/src/keystone/modal/modal_runner.py` — `ManagedProcess._stream_reader` (lines 91-93), `ModalAgentRunner.ensure_sandbox()` (lines 175-184)
- Direct codebase inspection: `keystone/src/keystone/schema.py` — `BootstrapResult`, `AgentExecution`
- Direct codebase inspection: `keystone/src/keystone/agent_log.py` — `CacheKey`, `AgentRunRecord`, `ensure_column_exists()`
- Direct codebase inspection: `keystone/src/keystone/cached_runner.py` — `CachedAgentRunner` delegation pattern
- Direct codebase inspection: `keystone/src/keystone/keystone_cli.py` — `BootstrapResult` assembly (lines 614-641)
- Direct codebase inspection: `evals/flow.py` — `process_repo_task`, stderr truncation (line 380), S3 uploads (lines 405-408)

### Secondary (MEDIUM confidence)
- Modal official docs: [modal.Sandbox reference](https://modal.com/docs/reference/modal.Sandbox) — `poll()`, `returncode`, `from_id()`, `get_tags()`
- Modal official docs: [modal.exception reference](https://modal.com/docs/reference/modal.exception) — `SandboxTerminatedError`, `SandboxTimeoutError`, `ResourceExhaustedError`
- Modal official docs: [Sandboxes guide](https://modal.com/docs/guide/sandboxes) — timeout, idle_timeout, lifecycle
- Modal official docs: [Changelog](https://modal.com/docs/reference/changelog) — v1.0.3 `terminate()` non-blocking; v1.3.2 `modal container logs <sandbox-id>`; v1.3.4 `terminate(wait=True)`

### Tertiary (LOW confidence)
- Modal post-mortem data richness — what `from_id()` returns for terminated sandboxes is not explicitly documented; inferred from `GenericResult` proto fields and comparison with Fly.io/GCP Cloud Run patterns. Needs validation during Phase 3 implementation.
- Failure root cause hypotheses (OOM, quota, gRPC reset, idle_timeout) — informed by Modal docs on resource limits and connection behavior; actual cause unknown until Phase 1 logging is deployed.

---
*Research completed: 2026-03-09*
*Ready for roadmap: yes*
