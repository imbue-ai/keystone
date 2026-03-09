# Stack Research: Modal Sandbox Observability

**Domain:** Modal Python SDK sandbox introspection for post-mortem analysis
**Researched:** 2026-03-09
**Confidence:** HIGH — sourced directly from installed SDK source at `.venv/lib/python3.12/site-packages/modal/`

---

## Modal SDK Version Confirmed

**Installed version:** `modal==1.3.1`

Verified via:
```bash
.venv/bin/python -c "import modal; print(modal.__version__)"
# => 1.3.1
```

---

## What the SDK Exposes for Sandbox Introspection

### sandbox.object_id (available immediately at creation)

`modal.Sandbox` inherits from `_Object` (type_prefix `"sb"`). The `object_id` property is set as
soon as the sandbox is created. The codebase already captures this — `self._sandbox.object_id` in
`ModalAgentRunner` — but does not persist it anywhere.

```python
sandbox = modal.Sandbox.create(app=app, image=image, timeout=7200)
sandbox_id = sandbox.object_id  # e.g. "sb-abc123xyz"
```

**Confidence:** HIGH (from `modal/sandbox.py`, `modal/_object.py` source)

---

### Sandbox.from_id(sandbox_id) — Post-Mortem Lookup

`modal.Sandbox.from_id(sandbox_id)` is the primary post-mortem lookup mechanism. It:

1. Calls the `SandboxWait` RPC with `timeout=0` (non-blocking poll)
2. Hydrates a `Sandbox` object with whatever result status the server returns
3. Sets `obj._result` if the sandbox has a terminal status

```python
# Synchronous usage (Modal's synchronize_api wraps async)
sb = modal.Sandbox.from_id("sb-abc123xyz")
print(sb.returncode)   # int or None if still running
```

**What you get back after termination:**
- `sb.returncode` — integer code derived from `_result.status`:
  - `None` — sandbox is still running or result unknown
  - `124` — `GENERIC_STATUS_TIMEOUT` (exceeded `timeout=` parameter)
  - `137` — `GENERIC_STATUS_TERMINATED` (externally killed or internal Modal reason)
  - `0` or non-zero — `GENERIC_STATUS_SUCCESS` / `GENERIC_STATUS_FAILURE` exitcode

**The `_result` object** is `api_pb2.GenericResult` with these fields:
| Field | Type | Content |
|-------|------|---------|
| `status` | enum | See status enum below |
| `exception` | string | Exception message string (populated for some failures) |
| `exitcode` | int | Process exit code (populated for SUCCESS/FAILURE) |
| `traceback` | string | Traceback string (populated for some failures) |
| `propagation_reason` | string | Internal reason string (populated by Modal internals) |

**Status enum values** (`api_pb2.GenericResult`):

| Value | Int | Meaning |
|-------|-----|---------|
| `GENERIC_STATUS_UNSPECIFIED` | 0 | Not yet finished |
| `GENERIC_STATUS_SUCCESS` | 1 | Normal exit (check exitcode) |
| `GENERIC_STATUS_FAILURE` | 2 | Process exited non-zero |
| `GENERIC_STATUS_TERMINATED` | 3 | Killed externally (by Modal or `.terminate()`) |
| `GENERIC_STATUS_TIMEOUT` | 4 | Exceeded `timeout=` parameter |
| `GENERIC_STATUS_INIT_FAILURE` | 5 | Failed before user code ran |
| `GENERIC_STATUS_INTERNAL_FAILURE` | 6 | Modal infrastructure failure |
| `GENERIC_STATUS_IDLE_TIMEOUT` | 7 | Exceeded `idle_timeout=` parameter |

**Confidence:** HIGH (from `modal/sandbox.py` source, `modal_proto/api_pb2` protobuf inspection)

---

### Sandbox.poll() — Non-Blocking Status Check on Live Sandbox

`sandbox.poll()` also calls `SandboxWait` with `timeout=0` and returns `returncode` (int or None).
Same data as `from_id()` but on an already-hydrated sandbox object.

```python
code = sandbox.poll()  # None = still running, int = finished
```

**Confidence:** HIGH (from source)

---

### Sandbox.wait(raise_on_termination=True) — Blocking Wait

Polls in a loop with 10s timeout per call until the sandbox finishes. Raises:
- `modal.exception.SandboxTimeoutError` — if `GENERIC_STATUS_TIMEOUT`
- `modal.exception.SandboxTerminatedError` — if `GENERIC_STATUS_TERMINATED` and `raise_on_termination=True`

Use `wait(raise_on_termination=False)` to collect the result without raising on early termination.
After `wait()`, `sandbox.returncode` and `sandbox._result` are populated.

**Confidence:** HIGH (from source)

---

### Sandbox.list() — Running Sandboxes Only (Key Limitation)

`modal.Sandbox.list()` has `include_finished=False` **hardcoded**. It only returns currently
running sandboxes. This is confirmed in the SDK source:

```python
# From modal/sandbox.py _Sandbox.list():
req = api_pb2.SandboxListRequest(
    app_id=app_id,
    before_timestamp=before_timestamp,
    environment_name=environment_name,
    include_finished=False,  # HARDCODED — no way to override via public API
    tags=tags_list,
)
```

The proto has `include_finished` as a field on `SandboxListRequest`, but the Python SDK does not
expose it. **Terminated sandboxes cannot be enumerated via `Sandbox.list()`.**

**Confidence:** HIGH (source inspection — `include_finished=False` is literally in the source)

---

### Sandbox.from_name() — By Name, Running Only

`modal.Sandbox.from_name(app_name, name)` calls `SandboxGetFromName` RPC. Raises `NotFoundError`
if no running sandbox has that name. Does not find terminated sandboxes.

**Confidence:** HIGH (from source)

---

### Sandbox Tagging — set_tags() / get_tags()

Sandboxes can have arbitrary key-value string tags set at any time while running:

```python
await sandbox.set_tags({"eval_run_id": "abc", "repo": "foo/bar"})
tags = await sandbox.get_tags()  # {"eval_run_id": "abc", "repo": "foo/bar"}
```

Tags can be used to filter `Sandbox.list()` results. However, since `list()` only returns running
sandboxes, tags cannot help with post-mortem lookup of terminated sandboxes.

**Confidence:** HIGH (from source)

---

### SandboxInfo Fields Available via Sandbox.list()

When iterating `Sandbox.list()`, each `SandboxInfo` proto carries:

| Field | Content |
|-------|---------|
| `id` | sandbox object_id string |
| `created_at` | float Unix timestamp |
| `task_info.id` | internal task ID |
| `task_info.started_at` | float Unix timestamp |
| `task_info.finished_at` | float Unix timestamp |
| `task_info.enqueued_at` | float Unix timestamp |
| `task_info.result` | `GenericResult` (status, exitcode, exception) |
| `app_id` | app ID string |
| `name` | sandbox name (if set) |
| `image_id` | image ID string |
| `regions` | list of region strings |
| `timeout_secs` | configured timeout |
| `tags` | list of SandboxTag |

This metadata is **not** directly accessible via `from_id()` — `from_id()` only fetches the result
status, not full `SandboxInfo`. To get `created_at`, `started_at`, etc., you would need access to
`Sandbox.list()` while the sandbox is still running.

**Confidence:** HIGH (from proto inspection of `api_pb2.SandboxInfo`, `api_pb2.TaskInfo`)

---

### CLI: modal shell sb-{id} — Shell Into Running Sandbox

The `modal shell <sandbox_id>` CLI command connects an interactive shell to a **running** sandbox.
It does not work for terminated sandboxes. Useful for live debugging, not post-mortem.

**Confidence:** HIGH (confirmed from `modal shell --help` output)

---

## What Is NOT Available After Termination

| Capability | Available? | Notes |
|------------|-----------|-------|
| Enumerate past sandboxes | NO | `Sandbox.list()` hardcodes `include_finished=False` |
| Retrieve sandbox logs post-termination | NO | No log-retrieval API on sandbox object |
| Filesystem access post-termination | NO | `sandbox.open()`, `sb.exec()` require live sandbox |
| Streaming logs of terminated sandbox | NO | `sandbox.stdout`, `sandbox.stderr` are live streams only |
| Termination cause detail | PARTIAL | `GenericResult.status` enum + optional `exception` string |
| Sandbox metadata (created_at, region) | NO | `from_id()` does not return `SandboxInfo` |
| Dashboard URL (manual) | YES | `https://modal.com/apps/<workspace>/main/deployed/<app-name>` |

**The single exception:** if you call `from_id()` with a known sandbox ID *after* termination, you
get the terminal `GenericResult.status` (TERMINATED, TIMEOUT, FAILURE, etc.) and possibly an
`exception` string. This is all the SDK provides programmatically.

**Confidence:** HIGH (exhaustive source inspection of all Sandbox methods)

---

## Recommended Approach for This Project

### 1. Persist sandbox_id in BootstrapResult (already partially done)

The `ModalAgentRunner` already has `self._sandbox.object_id`. Thread it into `BootstrapResult`:

```python
@dataclass
class BootstrapResult:
    modal_sandbox_id: str | None = None  # backward-compatible optional field
```

This is the core prerequisite for all post-mortem analysis.

### 2. Log exception in _stream_reader instead of suppressing

Replace the bare `except Exception: pass` with logging:

```python
except Exception as e:
    logger.warning("[%s] %s stream closed: %s", self.prefix, stream_name.name, e)
```

This surfaces connection drops that currently disappear silently.

### 3. Post-Mortem Script Using from_id()

Given a sandbox ID from the database, a post-mortem script can retrieve terminal status:

```python
import modal
from modal.exception import SandboxTimeoutError, SandboxTerminatedError

sb = modal.Sandbox.from_id("sb-abc123xyz")
print("returncode:", sb.returncode)
# Access raw result for more detail:
if sb._result:
    print("status:", sb._result.status)
    print("exception:", sb._result.exception)
    print("exitcode:", sb._result.exitcode)
```

**What this will tell us:**
- `returncode == 137` (TERMINATED): sandbox was killed by Modal (quota, preemption, or explicit `.terminate()`)
- `returncode == 124` (TIMEOUT): sandbox exceeded its configured `timeout=` parameter
- `returncode == None`: sandbox is still running (or ID is wrong/expired)
- `exception` string: may contain Modal-internal reason for TERMINATED/INIT_FAILURE cases

**What this will NOT tell us:** which specific resource was exhausted, whether it was a quota limit
vs. preemption vs. a Modal infrastructure issue. The `exception` string is the closest thing to a
reason, and it is not guaranteed to be populated for all TERMINATED cases.

### 4. No SDK Support for Batch Historical Lookup

There is no SDK method to list all sandboxes from a past time window. The `include_finished` proto
field exists on the server but is not exposed in the Python SDK. If batch analysis is needed, the
Modal dashboard is the only alternative.

**Alternative:** If the sandbox IDs are persisted in the database (via step 1), a script can call
`from_id()` for each ID individually as a post-mortem batch tool.

---

## Installation / Version Pinning

```bash
# Already in project venv at 1.3.1
# No additional packages needed for sandbox introspection
pip install modal==1.3.1
```

The `modal_proto` package is installed automatically as a dependency of `modal`. All protobuf types
inspected here are available via `from modal_proto import api_pb2`.

---

## Alternatives Considered

| Approach | Why Not Used |
|----------|-------------|
| Modal REST API (undocumented) | Not a public API; no SLA; SDK is the supported interface |
| Modal dashboard scraping | Brittle; not programmatic; not maintainable |
| `Sandbox.list()` with `include_finished=True` (via gRPC directly) | Would require bypassing the SDK; fragile; not supported |
| Adding sandbox heartbeat / health monitoring | Out of scope per PROJECT.md; complexity before understanding failure mode |

---

## Sources

- `modal/sandbox.py` (installed at `.venv/lib/python3.12/site-packages/modal/sandbox.py`) — PRIMARY
  - `_Sandbox.from_id()`, `_Sandbox.poll()`, `_Sandbox.wait()`, `_Sandbox.list()`, `_Sandbox.returncode`
  - `SandboxConnectCredentials`, `from_name()`, `set_tags()`, `get_tags()`
- `modal/_object.py` — `object_id` property implementation, `_Object` base class
- `modal/exception.py` — `SandboxTimeoutError`, `SandboxTerminatedError` definitions
- `modal_proto/api_pb2` — `GenericResult`, `SandboxInfo`, `TaskInfo`, `SandboxListRequest` fields
  - Confirmed via Python introspection of `.DESCRIPTOR.fields`
- Installed SDK version: `modal==1.3.1` (confirmed via `modal.__version__`)

---

*Stack research for: Modal Python SDK sandbox introspection (modal_sandbox_observability project)*
*Researched: 2026-03-09*
