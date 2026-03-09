# Feature Research

**Domain:** Cloud sandbox observability — diagnosing unexpected Modal sandbox termination
**Researched:** 2026-03-09
**Confidence:** HIGH (codebase-grounded; LOW for Modal post-mortem API availability)

## Context: The Specific Problem

Modal sandboxes die after ~45 minutes during large parallel eval runs (many sandboxes running
simultaneously via Prefect). When they die, there is no record of which sandbox it was, no
logged exception, and no way to look up what happened. The feature set below is scoped entirely
to closing this diagnostic gap — not to building a general observability platform.

The three concrete gaps in the current system:

1. **No identity persistence**: `self._sandbox.object_id` is printed to stderr but never stored
   anywhere queryable. When the process ends, the ID is gone.
2. **Silent failure**: `ManagedProcess._stream_reader` has `except Exception: pass` (line 91-93,
   modal_runner.py). When the sandbox dies mid-stream, the exception is swallowed. No log entry,
   no stack trace, nothing.
3. **No post-mortem path**: Even if we had the sandbox ID, there is currently no script or
   workflow to query Modal's API for what happened to a given sandbox.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features that any debugging workflow for a distributed cloud execution system would assume exist.
Missing these means "I literally cannot start investigating."

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Sandbox ID in persisted result** | Every cloud platform (Fly.io, GCP Cloud Run, Railway) surfaces a container/instance ID in logs/results; without it you cannot look up anything | LOW | Add `modal_sandbox_id: str \| None = None` to `BootstrapResult`. One line. No DB migration. Already stored in JSON blob in `cli_run.bootstrap_result_json`. |
| **Logged exception on stream failure** | Any production system logs connection drop exceptions; bare `except: pass` is recognized as a defect, not a feature | LOW | Replace the bare `pass` in `ManagedProcess._stream_reader` with `logger.warning(...)` including the exception and stream name. 3 lines. |
| **Timestamp of stream failure** | Knowing when the connection dropped — relative to sandbox creation time — is the first fact needed for any hypothesis | LOW | Python's `logging` framework adds timestamps automatically when the exception is logged. No extra code needed once the exception is logged. |
| **Exception type logged** | `SandboxTerminatedError` vs. `ConnectionError` vs. `ResourceExhaustedError` are categorically different causes; Modal provides distinct exception types | LOW | The existing logger call captures `str(exc)` which includes the type name. Captured for free once the except block logs instead of passes. |
| **Sandbox ID queryable from DB** | Post-mortem investigation starts with "show me all runs where a sandbox was assigned" — needs to be extractable without reading every JSON blob | LOW | `json_extract(bootstrap_result_json, '$.modal_sandbox_id')` in SQLite works without any schema change. The JSON blob approach (not a separate column) is sufficient at this scale. |

### Differentiators (Especially Helpful for This Specific Problem)

Features beyond minimal logging that provide targeted diagnostic value for the parallel-eval-OOM
hypothesis. These are not generic observability; they address the specific failure mode.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Post-mortem query script** | Closes the loop from "I have a sandbox ID" to "here is what Modal knows about that sandbox's lifecycle" — without requiring a dashboard | MEDIUM | Standalone Python script. Uses `modal.Sandbox.from_id(sandbox_id)` to retrieve the sandbox object, then reads `.returncode` and `.get_tags()`. Runs against the existing DB (SQLite or PostgreSQL). High value: first time an engineer can answer "why did sandbox X die?" |
| **Failure classification in log message** | Distinguishing `SandboxTerminatedError` (Modal-side kill) from `ResourceExhaustedError` (quota hit) from plain `ConnectionError` (network blip) immediately narrows hypotheses | LOW | Inspect `type(exc).__name__` in the exception log. No extra dependencies. The Modal SDK exposes named exception types via `modal.exception`. |
| **Correlation between parallel sandbox count and failure rate** | The failure only occurs under high parallelism. Logging sandbox ID enables cross-run correlation: "did failures cluster at the same clock time (quota burst) or were they spread out (individual timeouts)?" | MEDIUM | Requires the sandbox ID + timestamp data from Feature 1 above. Analysis done in post-mortem script or in the existing eval viewer. No new instrumentation — the data comes from having the sandbox ID in the DB. |
| **`modal sandbox` CLI link logged at creation** | The existing code already prints the dashboard URL and `modal shell {sandbox_id}` to stderr, but this is only visible if you are watching the terminal. Promoting these to structured log entries means they appear in S3 keystone_stderr.log uploads. | LOW | Change `print(..., file=sys.stderr)` to `logger.info(...)` in `ensure_sandbox()`. The eval harness uploads `keystone_stderr.log` to S3 already (flow.py line 405-408). |
| **Sandbox ID in eval result S3 artifact** | The eval harness uploads `eval_result.json` to S3 per repo. Once `BootstrapResult.modal_sandbox_id` is populated, the ID automatically appears in this artifact with no additional code. | NONE | Free side effect of Feature 1. Listed here to make explicit that S3 storage is already solved. |

### Anti-Features (Things to Deliberately NOT Build at This Stage)

Features that seem related and are sometimes requested in observability discussions, but would add
complexity before the failure mode is understood.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Heartbeat / health monitoring** | "Wouldn't it be great to know a sandbox is alive?" | Requires polling Modal API from a separate process on a schedule, or background threading inside the eval worker. Adds a new failure mode (heartbeat race conditions) before we even know what's killing sandboxes. PROJECT.md explicitly calls this out of scope. | Fix the silent failure first. Once we know the exception type and time, we'll know if polling would have helped. |
| **Automatic retry on sandbox death** | "Just retry when the sandbox dies" | We don't know the failure mode yet. If it's a quota exhaustion, retrying immediately makes it worse. If it's an OOM, the retry will fail identically. Retry logic requires understanding the failure cause first. | Let failures fail loudly. Diagnose. Then decide if retry makes sense for this specific cause. |
| **Custom metrics / dashboards** | "Let's build a Grafana dashboard for sandbox health" | Zero infrastructure exists for metrics collection. This project is debugging infrastructure, not production observability. A Grafana integration is 10x the scope of the actual problem. | The existing Prefect Cloud dashboard already shows task success/failure rates. The post-mortem script (Feature: differentiator) is sufficient for the current diagnostic need. |
| **Sandbox filesystem snapshot on death** | "Capture the sandbox state when it dies" | Modal's `snapshot_filesystem()` only works on running sandboxes. By the time we detect the failure (stream closed), the sandbox is already dead. This API is not usable for post-mortem. | Log the exception and the sandbox ID. The file system state is less useful than knowing which Modal resource limits were hit. |
| **Real-time log streaming to external sink** | "Stream all sandbox logs to Datadog/CloudWatch" | The existing system already logs to S3 (`keystone_stderr.log`). Adding another sink requires credentials, network configuration, and integration code that isn't needed when the current failure is "I don't know the sandbox ID." | Use the S3 log that already exists. The problem is the missing sandbox ID, not the log transport. |
| **Separate DB column for `modal_sandbox_id`** | "Add a SQL column so we can query it fast" | Creates dual-write complexity (JSON blob AND column must be kept in sync). At current eval scale (tens to hundreds of runs), `json_extract()` is fast enough. A column only becomes necessary if we need SQL-level indexing across millions of rows. | Use `json_extract(bootstrap_result_json, '$.modal_sandbox_id')` in the post-mortem script. Revisit if scale demands it. |
| **Eval viewer integration** | "Show the sandbox ID in the HTML eval viewer" | The viewer is a separate artifact (generates static HTML). Adding sandbox ID there requires viewer template changes and is premature — the priority is getting the data, not displaying it prettily. | Post-mortem script is the right consumer. Viewer integration can be added once the failure is diagnosed and the feature proves durable. |

---

## Feature Dependencies

```
[Log exception in _stream_reader]          (standalone — no dependencies)

[Sandbox ID in BootstrapResult]
    └──requires──> [get_sandbox_id() on AgentRunner base]
    └──requires──> [get_sandbox_id() on ModalAgentRunner]
    └──requires──> [get_sandbox_id() delegation in CachedAgentRunner]
    └──requires──> [modal_sandbox_id field on BootstrapResult]
    └──requires──> [keystone_cli.py calls runner.get_sandbox_id()]

[Post-mortem query script]
    └──requires──> [Sandbox ID in BootstrapResult]  (needs IDs in the DB first)
    └──requires──> [modal.Sandbox.from_id() API — verify current behavior]

[Correlation analysis]
    └──requires──> [Sandbox ID in BootstrapResult]
    └──enhances──> [Post-mortem query script]

[Promote print() to logger.info() in ensure_sandbox()]
    └──enhances──> [Log exception in _stream_reader]  (fuller picture in structured logs)
```

### Dependency Notes

- **Sandbox ID in BootstrapResult requires 5 coordinated changes**: These must ship together as a
  single unit. None of the 5 changes has diagnostic value alone — the ID only becomes useful when
  it reaches the DB JSON blob.
- **Post-mortem script requires IDs already in DB**: The script is only useful after at least one
  eval run has populated `modal_sandbox_id` in the DB. Plan to run a full eval first, then run the
  script against the results.
- **Log exception is standalone**: This is the only feature with zero dependencies. It can ship in
  isolation and immediately provides diagnostic value even before sandbox IDs are persisted.
- **Modal post-mortem API is LOW confidence**: `modal.Sandbox.from_id()` is documented and the
  `.returncode` property exists. However, what data is accessible on a terminated sandbox (logs,
  exit reason, resource metrics) is NOT clearly documented. The post-mortem script should be
  written defensively, expecting that `.returncode` and `.get_tags()` may be the only available
  fields. The Modal dashboard is the authoritative source for detailed post-mortem; the script
  provides the link/lookup mechanism.

---

## MVP Definition

### Launch With (v1 — Minimum to Diagnose the Failure)

These three things together close the primary diagnostic gap.

- [ ] **Log exception in `_stream_reader`** — immediate value, zero risk, zero dependencies.
  Reveals exception type and timestamp when a sandbox connection drops.
- [ ] **Sandbox ID in `BootstrapResult`** — five coordinated changes (see ARCHITECTURE.md).
  Enables all future post-mortem work. Without this, everything else is impossible.
- [ ] **Post-mortem query script** — standalone script that reads the DB and calls Modal API.
  Converts a sandbox ID into whatever Modal exposes about that sandbox's lifecycle.

Together these three answer: "Which sandbox died? When did the stream close? What does Modal say
about that sandbox?" This is enough to form and test hypotheses.

### Add After Validation (v1.x — Once We Have Evidence)

Features to add once the initial diagnostic data reveals the failure pattern.

- [ ] **Failure classification refinement** — add specific handling for `ResourceExhaustedError`
  vs `SandboxTerminatedError` if the exception logs reveal quota issues are the cause. Trigger:
  exception logs show a consistent exception type.
- [ ] **Promote `print()` to `logger.info()` in `ensure_sandbox()`** — improves log fidelity but
  is a cosmetic change with no diagnostic urgency. Trigger: once the bigger changes are stable.
- [ ] **Eval viewer sandbox ID column** — if the feature proves durable and sandbox ID becomes a
  standard debugging artifact. Trigger: sandbox ID is confirmed useful enough to display routinely.

### Future Consideration (v2+ — After Root Cause Is Known)

Defer until the failure mode is understood.

- [ ] **Retry logic** — only viable once we know the failure cause and that retrying would help.
- [ ] **Heartbeat monitoring** — only valuable if the failure is a long-running silent hang rather
  than an abrupt termination. Current evidence (stream closes) suggests abrupt termination.
- [ ] **Separate `modal_sandbox_id` DB column** — only if post-mortem queries become slow at
  scale, which requires scale first.

---

## Feature Prioritization Matrix

| Feature | Diagnostic Value | Implementation Cost | Priority |
|---------|-----------------|---------------------|----------|
| Log exception in `_stream_reader` | HIGH (reveals failure type and time) | LOW (3 lines) | P1 |
| `modal_sandbox_id` in `BootstrapResult` | HIGH (enables all post-mortem) | LOW (5 coordinated 1-5 line changes) | P1 |
| Post-mortem query script | HIGH (converts ID to Modal data) | MEDIUM (new script, Modal SDK calls) | P1 |
| Promote `print()` to `logger.info()` | LOW (cosmetic improvement) | LOW (2 lines) | P2 |
| Failure classification by exception type | MEDIUM (narrows hypothesis space) | LOW (inspect `type(exc).__name__`) | P2 |
| Eval viewer sandbox ID display | LOW (convenience, not diagnostic) | MEDIUM (template changes) | P3 |
| Separate DB column for sandbox ID | LOW (performance at scale we don't have) | MEDIUM (schema migration + dual-write) | P3 |

**Priority key:**
- P1: Must have for launch — without these, the diagnostic gap remains open
- P2: Should have, add when convenient
- P3: Nice to have, future consideration

---

## Platform Comparison: What Comparable Platforms Expose

This informs realistic expectations for what Modal's API will surface in post-mortem.

| Observability Feature | Fly.io Machines | Google Cloud Run | Railway | Modal Sandbox |
|-----------------------|----------------|------------------|---------|---------------|
| Instance/container ID | Yes (`machine_id`) | Yes (instance ID) | Yes (deployment ID) | Yes (`object_id`) |
| Exit code post-termination | Yes (`fly machine status`) | Yes (Cloud Logging) | Yes (deployment state) | Yes (`.returncode` property) |
| OOM kill detection | Yes (exit code 137 in events) | Yes (structured log field) | Yes (memory limit kill) | Unknown — not documented |
| Termination reason / cause | Yes (events list with reason field) | Yes (Cloud Logging structured data) | Limited | `SandboxTerminatedError` vs `SandboxTimeoutError` only |
| Historical log retrieval after death | Yes (centralized logging) | Yes (Cloud Logging, retained) | Yes (retained logs) | Unknown — stdout/stderr streams appear to be live-only |
| Resource metrics (CPU/RAM at death) | Yes (Prometheus metrics) | Yes (Cloud Monitoring) | Limited | Not exposed via SDK |
| Tags/labels for filtering | Yes (app tags) | Yes (resource labels) | No | Yes (`get_tags()`) |
| "Reconnect to dead container" | No | No | No | No (Modal dashboard only) |

**Key implication**: Modal does not expose structured exit reasons or resource metrics via the
Python SDK the way GCP Cloud Run does. The post-mortem script will likely surface only:
`.returncode`, `.get_tags()`, and potentially reconstructed information from when the sandbox was
created. The Modal **dashboard** (UI) likely has richer information. The script's primary value is
automating the lookup — not replacing the dashboard.

**Confidence on Modal post-mortem API: LOW.** The documentation confirms `from_id()` exists and
`.returncode` is accessible. What data is available on a terminated sandbox is not explicitly
documented. The post-mortem script should be written to gracefully handle limited data.

---

## Sources

- Direct codebase inspection: `keystone/src/keystone/modal/modal_runner.py` (ManagedProcess,
  ModalAgentRunner.ensure_sandbox)
- Direct codebase inspection: `keystone/src/keystone/schema.py` (BootstrapResult)
- Direct codebase inspection: `keystone/src/keystone/agent_log.py` (CLIRunRecord,
  cli_run table schema)
- Direct codebase inspection: `evals/flow.py` (S3 upload of keystone_stderr.log, eval_result.json)
- Modal SDK reference: [modal.Sandbox](https://modal.com/docs/reference/modal.Sandbox) — MEDIUM
  confidence; `from_id()`, `returncode`, `get_tags()`, `list()` confirmed
- Modal exception types: [modal.exception](https://modal.com/docs/reference/modal.exception) —
  HIGH confidence; `SandboxTerminatedError`, `SandboxTimeoutError`, `ResourceExhaustedError`
  confirmed
- Modal guide: [Sandboxes](https://modal.com/docs/guide/sandboxes) — post-termination log
  retrieval NOT documented; confirmed LOW confidence
- Modal guide: [Developing and debugging](https://modal.com/docs/guide/developing-debugging) —
  debug shell terminates with container; no post-termination access confirmed
- Fly.io: [Machine states and lifecycle](https://fly.io/docs/machines/machine-states/) — exit
  codes and events list confirmed; used as baseline for what mature platforms expose
- GCP Cloud Run: [Troubleshoot Cloud Run issues](https://cloud.google.com/run/docs/troubleshooting)
  — structured exit codes, OOM detection via Cloud Logging confirmed
- Railway: [Monitoring and Observability](https://blog.railway.com/p/using-logs-metrics-traces-and-alerts-to-understand-system-failures)
  — ephemeral log loss on container crash confirmed; aligns with Modal's apparent behavior
- `.planning/PROJECT.md` — explicit out-of-scope items (heartbeats, root-cause fix)
- `.planning/research/ARCHITECTURE.md` — build order, component boundaries

---
*Feature research for: Modal sandbox observability — unexpected termination diagnostics*
*Researched: 2026-03-09*
