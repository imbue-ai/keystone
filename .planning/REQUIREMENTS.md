# Requirements: Modal Sandbox Observability

**Defined:** 2026-03-09
**Core Value:** When a sandbox disappears mid-eval, we can identify exactly which Modal sandbox it was and reconstruct what happened to it.

## v1 Requirements

### Failure Logging

- [ ] **FAIL-01**: When the stdout/stderr stream connection breaks unexpectedly, the exception type and message are logged rather than silently swallowed
- [ ] **FAIL-02**: The logged exception identifies the Modal exception type (SandboxTerminatedError, ResourceExhaustedError, SandboxTimeoutError, ConnectionError) when available

### Sandbox Tracing

- [ ] **TRACE-01**: `BootstrapResult` includes the Modal sandbox `object_id` used for that run (`modal_sandbox_id: str | None`)
- [ ] **TRACE-02**: The sandbox ID is persisted in the database alongside each run (via existing `cli_run.bootstrap_result_json` storage — no schema migration required)

### Post-Mortem Tooling

- [ ] **POST-01**: Given a sandbox ID, a post-mortem script queries `modal.Sandbox.from_id()` and reports status, exit code, and termination reason
- [ ] **POST-02**: The post-mortem script can also query the local database for sandbox IDs from recent failed eval runs (to find a starting point without manually hunting through logs)

## v2 Requirements

### Enhanced Diagnostics

- **DIAG-01**: Sandbox memory and CPU resource metrics captured during runtime (requires Modal metrics API support)
- **DIAG-02**: Automated failure classification report across multiple eval runs

### Dashboard Integration

- **DASH-01**: Sandbox ID linkable directly from eval viewer HTML output

## Out of Scope

| Feature | Reason |
|---------|--------|
| Sandbox ID in cache key | Would invalidate every cached run — sandbox ID is an output, not an input |
| Heartbeat / health monitoring | Adds complexity before we understand the failure mode |
| Automatic sandbox restart / retry | Root-cause fix comes after diagnosis |
| Separate DB column for sandbox ID | Unnecessary — value rides inside existing `bootstrap_result_json` TEXT column |
| External log streaming infrastructure | Scope creep — Modal dashboard covers this |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FAIL-01 | Phase 1 | Pending |
| FAIL-02 | Phase 1 | Pending |
| TRACE-01 | Phase 2 | Pending |
| TRACE-02 | Phase 2 | Pending |
| POST-01 | Phase 3 | Pending |
| POST-02 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 6 total
- Mapped to phases: 6
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-09*
*Last updated: 2026-03-09 after initial definition*
