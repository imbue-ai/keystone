# Modal Sandbox Observability

## What This Is

A debugging and observability improvement to the bootstrap_devcontainer infrastructure. Modal sandboxes
are dying abruptly after ~45 minutes during large parallel eval runs (despite a 2-hour timeout), and
there is currently no way to trace which sandbox failed or why. This project adds the instrumentation
needed to investigate and diagnose those failures.

## Core Value

When a sandbox disappears mid-eval, we can identify exactly which Modal sandbox it was and reconstruct
what happened to it.

## Requirements

### Validated

- ✓ Modal sandbox execution for agents with configurable timeouts — existing
- ✓ Structured logging via Python's logging framework with ISO timestamps — existing
- ✓ BootstrapResult schema captures agent execution, verification, evaluator, and generated files — existing
- ✓ Database-backed run logging (`cli_run` and `agent_run` tables, SQLite/PostgreSQL) — existing
- ✓ Sandbox ID printed to stderr at creation (`Modal sandbox created: {sandbox_id}`) — existing
- ✓ Eval harness with Prefect for distributed multi-repo evaluation — existing

### Active

- [ ] Modal sandbox ID (`object_id`) persisted in `BootstrapResult` and therefore in `cli_run.bootstrap_result_json`
- [ ] Failure logging when sandbox connection drops (instead of silently swallowing the exception in `ManagedProcess._stream_reader`)
- [ ] Post-mortem script to query Modal API for sandbox history given a known sandbox ID

### Out of Scope

- Automated sandbox health monitoring / heartbeats — adds complexity before we understand the failure mode
- Root-cause fix — we're diagnosing first, fixing later once we have evidence

## Context

The `ModalAgentRunner` already captures `self._sandbox.object_id` and prints it to stderr, but it is
never threaded into the result or database. The `BootstrapResult` stored in `cli_run.bootstrap_result_json`
is the natural place to persist it — it would automatically be available in eval result analysis.

The failure only occurs during large parallel evaluations (many sandboxes running simultaneously), not
during automated tests. Likely culprits include Modal-side resource limits, quota exhaustion, or something
unique to the Prefect-driven multi-sandbox load pattern. The key diagnostic gap is that once a sandbox
dies, there's no record of its ID to look up in the Modal dashboard or API.

The `ManagedProcess._stream_reader` (modal_runner.py:74) has a bare `except Exception: pass` that silently
consumes the connection error when a sandbox terminates. This means failures are invisible in logs — we
only notice them when the stream stops.

## Constraints

- **Schema**: `BootstrapResult` changes must be backward-compatible (optional fields only)
- **Database**: Schema migrations via existing `ensure_column_exists()` helper pattern
- **Modal SDK**: Post-mortem tooling is limited to what the Modal Python SDK exposes for sandbox introspection

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Add `modal_sandbox_id` to `BootstrapResult` | Already stored as JSON in DB; no new column needed | — Pending |
| Log exception in `_stream_reader` instead of suppressing | Silent failures make debugging impossible | — Pending |
| Post-mortem as standalone script (not integrated into eval viewer) | Fastest to build; can promote later | — Pending |

---
*Last updated: 2026-03-09 after initialization*
