# Codebase Concerns

**Analysis Date:** 2026-03-09

## Tech Debt

**Broad Exception Handling:**
- Issue: Multiple locations catch generic `Exception` rather than specific exception types, obscuring the actual failure mode and making debugging difficult
- Files: `evals/flow.py`, `evals/scripts/populate_commit_hashes.py`, `keystone/src/keystone/agent_log.py`, `keystone/src/keystone/evaluator.py`, `keystone/src/keystone/modal/modal_runner.py`
- Impact: Silent failures, harder to diagnose production issues, potential masking of unrelated errors
- Fix approach: Replace broad exception handlers with specific exception types. For example, catch `subprocess.CalledProcessError`, `json.JSONDecodeError`, `OSError` separately. Only use bare `Exception` as final catch-all with explicit logging.

**Heavy Pandas Dependency for Simple Operations:**
- Issue: `keystone/src/keystone/agent_log.py:309` uses pandas only to append a single row to SQLite via `df.to_sql()`, which is inefficient
- Files: `keystone/src/keystone/agent_log.py` (lines 310-322, 336-349)
- Impact: Unnecessary runtime dependency overhead, slower database writes than direct SQL
- Fix approach: Replace `pd.DataFrame().to_sql()` calls with direct SQLAlchemy `INSERT` statements using `insert()` and `values()`

**Unspecific Git Archive Limitation:**
- Issue: `keystone/src/keystone/git_utils.py:35` uses `git archive` which doesn't support `--recurse-submodules` in current mainline git
- Files: `keystone/src/keystone/git_utils.py` (lines 32-47)
- Impact: Projects with git submodules will have incomplete source code in archives, potentially causing evaluations to fail
- Fix approach: Implement custom submodule handling or document the limitation clearly. Consider switching to `git clone --recurse-submodules` followed by tarball creation for completeness.

**Silent S3 Upload Failures:**
- Issue: `evals/flow.py:433` catches `Exception` on S3 uploads but only logs a warning, continuing with the process. Critical results may be lost without proper alerting
- Files: `evals/flow.py` (lines 433-434)
- Impact: Evaluation results may not persist to S3, losing data without clear signal to user
- Fix approach: Escalate S3 upload failures to task-level failures, or implement retry logic with exponential backoff

**Inconsistent Error Message Truncation:**
- Issue: `evals/flow.py:380` truncates stderr to first 1000 bytes, which may cut off the actual error
- Files: `evals/flow.py` (lines 380-381, 388)
- Impact: Critical error context lost for debugging; makes post-mortem analysis difficult
- Fix approach: Either store full stderr or implement intelligent truncation that preserves the last N bytes (which usually contain the actual error)

## Known Bugs

**Git Repository Initialization Redundancy:**
- Bug: `evals/flow.py:268-283` initializes a git repo from a tarball that "should already be" a git repo according to the TODO comment
- Symptoms: Unnecessary git operations (init, add, commit) that may fail or corrupt the extracted state
- Files: `evals/flow.py` (lines 268-283)
- Trigger: Every `process_repo_task` call re-initializes git on extracted archives
- Workaround: The code handles it anyway, but unnecessarily
- Impact: Wasted cycles and potential state corruption if the tarball extraction process fails mid-way

**S3 Tarball Download Error Recovery:**
- Bug: `evals/flow.py:245-250` attempts to parse a pre-existing result JSON but if parsing fails, it silently re-runs the entire task instead of investigating why the JSON is corrupted
- Symptoms: Duplicate work, slower eval completion
- Files: `evals/flow.py` (lines 245-250)
- Trigger: Corrupted or incomplete JSON files in S3 (e.g., from previous failed uploads)
- Workaround: None; relies on eventual success of re-run
- Impact: Potential infinite loops if the JSON continues to be corrupted

## Security Considerations

**Unvalidated GitHub API Response Parsing:**
- Risk: `evals/eda/fetch_repos.py:139-172` parses arbitrary GraphQL responses from GitHub API without defensive checks for missing nested fields
- Files: `evals/eda/fetch_repos.py` (lines 139-172)
- Current mitigation: Uses `.get()` with fallback defaults, but some deep nesting (`node["defaultBranchRef"]["target"]`) could KeyError
- Recommendations: Add explicit null checks or use `dict.get(key, {}).get(nested_key)` patterns consistently. Consider schema validation with pydantic for API responses.

**Unvalidated Environment Variable in Modal Configuration:**
- Risk: `keystone/src/keystone/modal/modal_runner.py:199` directly interpolates `DOCKER_REGISTRY_MIRROR` into Docker daemon.json without validation
- Files: `keystone/src/keystone/modal/modal_runner.py` (lines 197-201)
- Current mitigation: None explicit; assumes env var is a valid URL
- Recommendations: Validate the URL format before writing to daemon.json. At minimum, check it's not empty and matches a URL pattern.

**Command Execution via Modal Sandbox:**
- Risk: `keystone/src/keystone/modal/modal_runner.py:134-137` uses `sb.exec()` with user-supplied arguments without validation
- Files: `keystone/src/keystone/modal/modal_runner.py` (lines 134-137)
- Current mitigation: Arguments appear to be constructed internally, not from user input
- Recommendations: Document that all arguments to `run_modal_command()` must come from trusted internal sources, never from user input or config files

**Tarball Extraction Race Condition:**
- Risk: `evals/flow.py:263-266` and `keystone/src/keystone/modal/modal_runner.py:221-230` extract tarballs concurrently without verification of archive integrity
- Files: `evals/flow.py`, `keystone/src/keystone/modal/modal_runner.py`
- Current mitigation: Tarballs are immutable (cached by commit), but no checksum validation during extraction
- Recommendations: Add CRC/SHA256 validation of tarball contents before/after extraction

## Performance Bottlenecks

**Git Repository Cloning in Archive Task:**
- Problem: `evals/flow.py:172-174` performs full `git clone` + `git checkout` + `git archive` for every repo, even after caching
- Files: `evals/flow.py` (lines 172-174)
- Cause: Each repo is cloned separately; no batch cloning or shallow cloning
- Improvement path: Implement shallow clone (`--depth 1 --single-branch`) since only HEAD is needed, or use GitHub's archive API directly

**Pandas DataFrame Row Append Pattern:**
- Problem: `keystone/src/keystone/agent_log.py:310-322` creates a single-row DataFrame just to append to SQL, which is inefficient
- Files: `keystone/src/keystone/agent_log.py` (lines 310-322, 336-349)
- Cause: Unnecessary object allocation and conversion overhead
- Improvement path: Use direct SQLAlchemy `insert()` instead of going through pandas

**Exception Handling Overhead:**
- Problem: Multiple try/except blocks catching broad `Exception` may be hiding O(n) operations in the exception path
- Files: `evals/flow.py:76`, `evals/scripts/populate_commit_hashes.py:39,55`, `keystone/src/keystone/agent_log.py:218,382,393`
- Cause: No visibility into what's actually failing; exceptions may be slow operations
- Improvement path: Add specific exception type handling with dedicated logging to identify slow paths

**Large File Size of Modal Runner:**
- Problem: `keystone/src/keystone/modal/modal_runner.py` is 719 lines with complex state management and subprocess streaming
- Files: `keystone/src/keystone/modal/modal_runner.py`
- Cause: Threading, streaming, and Modal API interaction in a single class
- Improvement path: Extract stream handling into a separate `StreamingProcess` class and Modal lifecycle into a separate `SandboxLifecycle` class

## Fragile Areas

**Modal Sandbox Lifecycle:**
- Files: `keystone/src/keystone/modal/modal_runner.py` (entire file, especially lines 175-208, 210-240)
- Why fragile: Creates persistent sandbox that must be cleaned up; if cleanup fails, resources leak. Threading for stream capture can deadlock if not careful. Docker daemon startup is timing-dependent.
- Safe modification: All Modal sandbox operations should be wrapped in context managers or have explicit cleanup. Add comprehensive timeout handling for all wait operations. Test with intentional failures (Docker daemon crashes, sandbox timeouts).
- Test coverage: Need tests for: sandbox creation failure, Docker daemon startup timeout, stream thread deadlock, cleanup after exception

**S3 Path Construction:**
- Files: `evals/flow.py` (lines 89-96, 163, 239, 396-398)
- Why fragile: Prefix handling uses string slicing and concatenation. If `eval_config.name` is empty or matches in unexpected ways, paths will be wrong. `rstrip('/')` can be unsafe with certain inputs.
- Safe modification: Use `pathlib.PurePosixPath` or explicit path builder classes. Add validation that name doesn't contain special characters.
- Test coverage: Test with empty names, names containing `/`, trailing slashes, S3 prefixes with unusual formats

**Database Schema Migration:**
- Files: `keystone/src/keystone/agent_log.py` (lines 327-331, 214-222)
- Why fragile: Manual schema migrations using string concatenation in SQL. `rename_column_if_exists` uses introspection that may not work consistently across SQLite/PostgreSQL.
- Safe modification: Use SQLAlchemy migrations (Alembic) instead of manual migrations. Add explicit database version tracking.
- Test coverage: Test migrations on both SQLite and PostgreSQL. Test idempotency of migrations.

**Cache Key Generation:**
- Files: `keystone/src/keystone/agent_log.py` (lines 341-345)
- Why fragile: Cache key based on `compute_hash()` of config JSON. If JSON serialization changes, cache invalidates silently. No version field in cache structure.
- Safe modification: Add explicit `cache_version` as a top-level field. Use stable JSON serialization (sorted keys, consistent formatting). Document when cache should be cleared.
- Test coverage: Test that cache invalidation happens when version increments

## Scaling Limits

**Single-threaded API Fetching:**
- Current capacity: `evals/eda/fetch_repos.py` processes one language x star-bucket combination at a time with 0.5-1.0s delays
- Limit: For 13 languages x 6 star buckets = 78 API calls at 0.5s each = ~40 seconds minimum, plus API wait times. Any network hiccup forces 60s sleep.
- Scaling path: Implement concurrent requests with asyncio. Respect rate limits using a token bucket algorithm rather than fixed sleeps.

**Modal Sandbox Memory/Concurrency:**
- Current capacity: `keystone/src/keystone/modal/modal_runner.py` creates one sandbox per worker
- Limit: Each sandbox allocates memory for Docker daemon, agent environment, project state. Number of concurrent runs limited by Modal account quotas and regional availability.
- Scaling path: Implement sandbox pooling/reuse. Share Docker cache across runs. Consider regional distribution or on-prem fallback.

**Prefect Task Parallelism:**
- Current capacity: `evals/flow.py` submits all archive tasks and process_repo tasks in parallel via Prefect
- Limit: Constrained by Prefect work pool concurrency limits, S3 bandwidth, and git clone performance
- Scaling path: Implement task batching for archive operations. Add rate limiting to S3 uploads. Consider using GitHub CLI for faster cloning.

## Dependencies at Risk

**Modal SDK Dependency:**
- Risk: Entire `keystone/src/keystone/modal/` module depends on proprietary Modal SDK with limited public documentation
- Impact: Changes to Modal API (breaking changes in `Modal.Sandbox.create()`, `sb.exec()`, etc.) will require code updates. Modal outages block all distributed execution.
- Migration plan: Maintain abstraction layer `AgentRunner` interface; consider fallback to subprocess-only mode. Document Modal API contract clearly.

**Pandas Usage:**
- Risk: Pandas is imported only for `DataFrame.to_sql()` but is a heavy dependency (100+ MB)
- Impact: Slow imports, unnecessary transitive dependencies
- Migration plan: Remove pandas dependency, replace with raw SQLAlchemy inserts. Requires refactoring `log_cli_run()` and `log_agent_run()` methods in `AgentLog` class.

**Unversioned API Endpoints:**
- Risk: `evals/eda/fetch_repos.py:29` uses `https://api.github.com/graphql` without version pinning
- Impact: GitHub API changes may break queries silently
- Migration plan: Pin GraphQL schema version. Implement response schema validation with pydantic. Add tests for API response format.

## Missing Critical Features

**Data Integrity Verification:**
- Problem: No checksums or signatures on cached tarballs, results, or database records
- Blocks: Can't detect bit-rot, S3 corruption, or tampering
- Recommended: Add SHA256 checksums to tarball metadata, validate on extraction. Hash database records for integrity checks.

**Comprehensive Error Telemetry:**
- Problem: Broad exception handlers swallow error details; no centralized error tracking
- Blocks: Hard to diagnose failures in production eval runs
- Recommended: Implement error classification and metrics. Use Sentry or similar for aggregated error tracking.

**Graceful Degradation of Failed Components:**
- Problem: Modal sandbox failures cause entire task failure; no fallback to local agent
- Blocks: Single point of failure in distributed execution
- Recommended: Implement local fallback mode if Modal sandbox creation fails (with option `--allow_local_fallback`)

## Test Coverage Gaps

**Broad Exception Handling in Production Paths:**
- What's not tested: How the system behaves when specific subprocess operations fail (git clone timeout, tar extraction failure, S3 connection drop)
- Files: `evals/flow.py` (archive_repo_task, process_repo_task), `evals/eda/fetch_repos.py` (fetch_repos)
- Risk: Silent failures, incomplete error messages, data loss
- Priority: High

**Modal Sandbox Lifecycle Under Failure:**
- What's not tested: Sandbox creation failure, Docker daemon timeout, stream thread deadlock, cleanup after exception
- Files: `keystone/src/keystone/modal/modal_runner.py`
- Risk: Resource leaks, hanging processes, cascading failures
- Priority: High

**Schema Migration Compatibility:**
- What's not tested: Schema migrations on both SQLite and PostgreSQL; idempotency of migrations; forward/backward compatibility
- Files: `keystone/src/keystone/agent_log.py` (schema functions)
- Risk: Database corruption, migration failures in production
- Priority: High

**Concurrent S3 Operations:**
- What's not tested: Multiple tasks uploading to same S3 prefix simultaneously; race conditions in cache checking (`_s3_exists` + write)
- Files: `evals/flow.py` (archive tasks, process_repo tasks)
- Risk: Lost uploads, cache invalidation, corrupted result files
- Priority: Medium

**Git Submodule Handling:**
- What's not tested: Repositories with submodules; verification that archives contain submodule contents
- Files: `keystone/src/keystone/git_utils.py`
- Risk: Incomplete source code in evaluations, false failures
- Priority: Medium

---

*Concerns audit: 2026-03-09*
