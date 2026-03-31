# Mutation-Augmented Eval Pipeline

## Overview

This feature adds an anti-cheating layer to the Keystone evaluation harness. The system works in two phases:

**Phase 1 (new):** A `mutation_flow.py` / `mutation_cli.py` pipeline runs a Claude Code agent inside a Modal sandbox on each repo to introduce N=5 small test-breaking commits. Each commit is stored as a branch (`broken-1`…`broken-N`) on top of the base commit in a bare git tarball, then uploaded to S3. An amended `repos_with_mutations.jsonl` is written locally (version-controlled in the git tree).

**Phase 2 (modified):** The existing eval pipeline (`flow.py`) is updated to consume Phase 1's tarballs (removing `archive_repo_task`). Keystone receives `--broken_commit_hashes` and, after the main bootstrap passes, cycles through each broken commit sequentially by swapping source files into a single long-running container (reusing build artifacts across runs), re-running tests, and checking that at least one test fails per commit. Results flow into `BootstrapResult` and surface in `KeystoneRepoResult` as `unexpected_broken_commit_passes` (an integer count). The eval viewer flags repos with unexpected passes.

**Operator UX:**
1. `uv run python evals/mutation_cli.py run --repo_list evals/examples/repos.jsonl --s3_prefix s3://…/mutations/` — runs Phase 1, writes `repos_with_mutations.jsonl` locally to `evals/examples/repos_with_mutations.jsonl` (check it in).
2. Edit Phase 2's JSON config: set `repo_list_path` → `repos_with_mutations.jsonl` and `s3_repo_cache_prefix` → Phase 1's S3 prefix.
3. `uv run python evals/eval_cli.py run --config_file …` — runs Phase 2 as before.

---

## Expected Behavior

### Phase 1: Mutation Pipeline

- Reads `repos.jsonl`; for each `RepoEntry`, clones the repo at `commit_hash`, then spawns a Claude Code agent in a Modal sandbox.
- The agent explores the repo's source files, introduces up to N=5 simple breaking changes (e.g., inserting an unconditional assertion failure in a source function), and commits each change independently as a new branch `broken-1`…`broken-N` off the base commit.
  - Agent only modifies source files, never test files.
  - Each breaking change is small, obvious, and self-contained.
  - Agent is told to take its best shot without a working build environment.
  - Prompt includes `language`, `build_system`, `tests`, and `notes` fields from `repos.jsonl` to help Claude target the right source files; agent explores the repo directly for specifics.
- Output per repo: a `.tar.gz` bare git repo containing the base commit + up to N `broken-*` branches (shallow history; submodules preserved).
- If fewer than N valid commits are produced, a warning is logged and however many were produced are recorded.
- Amended `repos_with_mutations.jsonl` adds `broken_commit_hashes: list[str]` to each entry.
- `repos_with_mutations.jsonl` is written locally as `evals/examples/repos_with_mutations.jsonl` and checked into the git tree. It is NOT written to S3.

### Phase 2: Keystone + Broken-Commit Re-verification

- `process_repo_task` downloads Phase 1's bare-git tarballs from S3 directly (no `archive_repo_task`).
- Keystone CLI accepts `--broken_commit_hashes h1,h2,h3`.
- After the main bootstrap succeeds and the base container image is built:
  - Broken-commit verification is skipped entirely if the main bootstrap failed.
  - One long-running detached container is started from the built image (`docker run -d ... sleep infinity`) and reused for all broken-commit runs and the restoration check. This allows compiled build artifacts (object files, downloaded dependencies) to persist across runs, so incremental recompilation works correctly for C++ and similar projects.
  - For each broken commit hash (sequentially): checks out the broken source files from the local git repo at that hash; copies them via `docker cp` into the running container; executes `docker exec /run_all_tests.sh` (which invokes the project's build system so source changes take effect); records the `VerificationResult`.
  - After all N broken-commit runs complete (regardless of individual results), the base source files are copied back into the same container via `docker cp`, and `/run_all_tests.sh` is re-run once more to confirm they still pass.
  - The container is stopped and removed after the restoration check completes.
  - Each broken-commit run and the restoration check each produce a `VerificationResult`.
- `BootstrapResult` gains:
  - `broken_commit_verifications: dict[str, VerificationResult]` — keyed by commit hash
  - `post_broken_commits_verification: VerificationResult | None`
  - `unexpected_broken_commit_passes: int` — count of broken commits with `tests_failed == 0`
- `KeystoneRepoResult` gains:
  - `unexpected_broken_commit_passes: int` — copied from `BootstrapResult`; 0 if bootstrap failed
  - `restoration_check_failed: bool` — True if `post_broken_commits_verification.success == False`

### Eval Viewer

- Repos with `unexpected_broken_commit_passes > 0` display ⚠️ on the repo row with a tooltip showing the count.
- A dedicated "Cheating Summary" section lists all flagged repos across the run.

---

## Data Model

### `RepoEntry` (amended, in `evals/eval_schema.py`)

```
broken_commit_hashes: list[str] = []   # NEW — populated by Phase 1
```

### `BootstrapResult` (amended, in `keystone/schema.py`)

```
broken_commit_verifications: dict[str, VerificationResult] = {}   # NEW, keyed by commit hash
post_broken_commits_verification: VerificationResult | None = None  # NEW
unexpected_broken_commit_passes: int = 0                            # NEW
```

### `KeystoneRepoResult` (amended, in `evals/eval_schema.py`)

```
unexpected_broken_commit_passes: int = 0   # NEW, derived from BootstrapResult
restoration_check_failed: bool = False     # NEW
```

### `repos_with_mutations.jsonl` (new output file)

Each line is a `RepoEntry` JSON object with the additional `broken_commit_hashes` field:

```jsonl
{"id": "flask", "repo": "...", "commit_hash": "3a9d5...", ..., "broken_commit_hashes": ["abc123", "def456", "ghi789", "jkl012", "mno345"]}
```

### S3 Layout (Phase 1 output)

```
s3://<prefix>/
  flask.tar.gz                        ← bare git repo: base + broken-1..5 branches
  requests.tar.gz
  ...
```

`repos_with_mutations.jsonl` lives in the git tree at `evals/examples/repos_with_mutations.jsonl`, not in S3.

### Bare Git Tarball Structure

```
<repo_id>.tar.gz
  └── (bare git repo: HEAD, refs/heads/broken-1..broken-N, objects/...)
```

- Shallow: only the base commit + N broken commits (no full upstream history).
- `HEAD` points to `main`, which is the base commit — `git clone` checks out the correct base automatically.
- Submodules: objects absorbed into the parent repo's object store via `git submodule absorbgitdirs` + `git repack` so they are present in the bare tarball.
- Branches: `broken-1`, `broken-2`, … `broken-N`, each a single commit directly off the base commit (`main`).

### Broken-Commit Re-verification Sequence (in Keystone)

```
Main bootstrap succeeds
  → docker run -d --name keystone-broken keystone-verify sleep infinity
  → for each hash in broken_commit_hashes (sequential):
      git archive <hash> → temp dir
      docker cp temp_dir/. keystone-broken:/project/
      docker exec keystone-broken /run_all_tests.sh
      record VerificationResult
  → (restoration) docker cp base_source/. keystone-broken:/project/
  → docker exec keystone-broken /run_all_tests.sh
  → record post_broken_commits_verification
  → docker stop/rm keystone-broken
```

---

## Implementation Plan

### New Files

**`evals/mutation_flow.py`** (Prefect flow + Typer CLI combined)
- `MutationRunConfig` (Pydantic): `repo_list_path`, `s3_output_prefix`, `n_mutations`, `modal_timeout_seconds`
- `mutate_repo_task(repo_entry: RepoEntry, s3_prefix: str, n: int) -> MutationResult` — Prefect task:
  - Clones repo at `commit_hash` into a temp dir
  - Spawns a Modal sandbox using the existing `create_modal_image()` from `keystone/modal/image.py` (already has `git` and `claude` CLI)
  - Uploads repo to sandbox
  - Runs Claude Code with `MUTATION_PROMPT` (see below)
  - Reads back new commits; resolves `broken-1`…`broken-N` branch hashes
  - Packages repo as shallow bare git tarball
  - Uploads to S3
  - Returns `MutationResult(repo_id, broken_commit_hashes, branches_created, warnings)`
- `mutation_flow(repo_list_path, s3_prefix, n, limit, max_concurrent) -> list[MutationResult]`:
  - Loads repos, submits `mutate_repo_task` per repo, collects results
  - Builds amended JSONL, writes to local path `evals/examples/repos_with_mutations.jsonl` only (not S3)
- `MUTATION_PROMPT`: instructs Claude Code to:
  - Explore source files (not test files) in `/project`
  - Introduce N independent breaking changes, one per `git commit`
  - Each change should be small and obvious (e.g., unconditional `raise`/`assert False` in a source function)
  - Create branches `broken-1`…`broken-N` each as a single commit off the same base
  - Includes `language`, `build_system`, `tests`, and `notes` from `repos.jsonl` to orient the agent
- `MutationResult` (Pydantic): `repo_id`, `broken_commit_hashes: list[str]`, `s3_tarball_path`, `warnings: list[str]`
- Typer `run` command: loads config, runs flow with `ThreadPoolTaskRunner`, prints summary

### Modified Files

**`keystone/schema.py`**
- Add to `BootstrapResult`:
  - `broken_commit_verifications: dict[str, VerificationResult] = {}`
  - `post_broken_commits_verification: VerificationResult | None = None`
  - `unexpected_broken_commit_passes: int = 0`

**`evals/eval_schema.py`**
- Add to `RepoEntry`: `broken_commit_hashes: list[str] = []`
- Add to `KeystoneRepoResult`:
  - `unexpected_broken_commit_passes: int = 0`
  - `restoration_check_failed: bool = False`

**`keystone/keystone_cli.py`**
- Add `--broken_commit_hashes` option: `str | None`, parsed as `str.split(",")` → `list[str]`
- After main verification succeeds, if `broken_commit_hashes` is non-empty:
  - Call `run_broken_commit_verifications(runner, broken_commit_hashes, project_root, ...)` (new helper)
  - Populate `BootstrapResult.broken_commit_verifications`, `post_broken_commits_verification`, `unexpected_broken_commit_passes`
- Derive `unexpected_broken_commit_passes` = count of hashes where `broken_commit_verifications[h].tests_failed == 0`

**`keystone/modal/modal_runner.py`**
- Add `run_broken_commit_verifications(self, broken_commit_hashes: list[str], project_root: Path, test_timeout_seconds: int) -> dict[str, VerificationResult]`:
  - Start ONE long-running detached container: `docker run -d --name keystone-broken keystone-verify sleep infinity`
  - For each `commit_hash` (sequentially):
    - `git archive <commit_hash>` into a temp dir to get that commit's source tree
    - `docker cp temp_dir/. keystone-broken:/project/`
    - `docker exec keystone-broken /run_all_tests.sh` (with timeout); `run_all_tests.sh` invokes the project's build system so source changes are compiled
    - Extract JUnit artifacts; record `VerificationResult` keyed by `commit_hash`
  - The same container is reused across all N cycles so compiled artifacts persist between runs
  - Stop and remove container after all cycles complete (including restoration — see below)
  - Return `dict[str, VerificationResult]`
- Add `run_restoration_check(self, project_root: Path, container_name: str, test_timeout_seconds: int) -> VerificationResult`:
  - Reuses the same already-running container (passed in as `container_name`)
  - `git archive HEAD` from `project_root` (base commit) into a temp dir
  - `docker cp temp_dir/. <container>:/project/`
  - `docker exec <container> /run_all_tests.sh` (with timeout)
  - Extract JUnit artifacts; stop and remove container
  - Return `VerificationResult`
- Note: `run_all_tests.sh` is expected to invoke the project's build system (e.g., `make`, `cmake --build`, `pip install -e .`) so that source file changes inside the container take effect before tests run

**`evals/flow.py`**
- Remove `archive_repo_task` and `_archive_repos` helper
- Remove `_tarball_cache_key` and related Prefect cache setup
- `process_repo_task`: directly look up `{s3_cache_prefix}/{repo_id}.tar.gz` (Phase 1's tarball path) instead of calling `archive_repo_task`
- Extract `broken_commit_hashes` from `repo_entry.broken_commit_hashes`; if non-empty, append `--broken_commit_hashes <csv>` to the keystone CLI command
- After parsing `bootstrap_result`, populate `KeystoneRepoResult.unexpected_broken_commit_passes` and `restoration_check_failed` from the nested `BootstrapResult`
- Update `process_repo_task` to restore a flat-tarball path from a bare git tarball: `git clone --bare` the tarball, then check out the base commit's working tree as a normal clone for passing to keystone

**`evals/viewer/generate_viewer.py`**
- `extract_summary`: add `unexpected_broken_commit_passes` and `restoration_check_failed` to the returned dict
- Row rendering: add ⚠️ badge with tooltip when `unexpected_broken_commit_passes > 0`
- Add a "Cheating Summary" section in the HTML output listing all repos with `unexpected_broken_commit_passes > 0` across configs

---

## Open Questions

1. **Source file identification for `docker cp` injection**: The broken-commit re-verification uses `git archive <hash>` to get the full source tree for each broken commit and copies it wholesale into the container. This is simple but copies all files, not just the changed ones. An optimisation would be to use `git diff --name-only <base> <hash>` to copy only changed files — but this requires knowing the base commit hash inside the CLI. The base hash is available from `repo_entry.commit_hash` passed via the eval harness; whether to pass it explicitly to keystone or have keystone infer it from `git log` is TBD.

2. **`broken_commit_hashes` in `BootstrapResult.cli_args`**: Currently `cli_args` captures `sys.argv`. The new `--broken_commit_hashes` flag will appear there as a long comma-separated string, which is fine for reproducibility but slightly awkward to re-parse. Low priority; acceptable as-is for V1.

---

## Testing Strategy

### Phase 1: Mutation Pipeline Tests

- **`evals/test_mutation_flow.py`** — unit/integration tests:
  - **Fixture: synthetic toy repo** — a Python package with `src/math_utils.py` (e.g., `def add(a, b): return a + b`) and `tests/test_math_utils.py` (e.g., `assert add(2, 3) == 5`). Created programmatically in a `tmp_path` fixture with a real git history.
  - **`test_mutation_produces_failing_tests`**: run the full `mutate_repo_task` against the toy repo (with Claude Code in Modal); assert that for each `broken_commit_hash`, checking out that branch and running `pytest` on the toy repo returns a non-zero exit code (at least one test fails).
  - **`test_bare_tarball_structure`**: after mutation, unpack the bare tarball and verify: `broken-1`…`broken-N` branches exist; each is a single commit off the base; the base commit matches `commit_hash`.
  - **`test_partial_mutations_logged`**: mock the Claude Code agent to produce fewer than N commits; assert the result contains fewer hashes and a warning is logged.
  - **`test_amended_jsonl_written`**: assert `repos_with_mutations.jsonl` contains the `broken_commit_hashes` field and is valid per the `RepoEntry` schema.

### Phase 2: Keystone Schema Tests

- **`keystone/tests/test_cli.py`** (extend existing):
  - Add snapshot tests for `BootstrapResult` serialization/deserialization with the new fields populated.
  - Verify `unexpected_broken_commit_passes` is computed correctly given mock `VerificationResult` values.

### Phase 2: Broken-Commit Re-verification Integration Tests

- **`keystone/tests/test_broken_commit_verification.py`** — integration tests with Modal + Docker in the loop (not mocked):
  - **Fixture**: minimal Docker-buildable repo (e.g., a Python project with a trivial `Dockerfile` and `run_all_tests.sh`) with a synthetic broken commit that inserts `raise AssertionError("broken")` in a source function called by a test.
  - **`test_broken_commit_fails_tests`**: run `ModalAgentRunner.run_broken_commit_verification()` against this fixture; assert `VerificationResult.tests_failed >= 1`.
  - **`test_restoration_check_passes`**: after the broken-commit run, run `run_restoration_check()`; assert `VerificationResult.success == True`.
  - **`test_unexpected_pass_detection`**: use a broken commit that doesn't actually break tests; assert `unexpected_broken_commit_passes == 1` in `BootstrapResult`.
  - **`test_broken_commit_skipped_on_bootstrap_failure`**: simulate a bootstrap failure; assert `broken_commit_verifications` is empty.

### Eval Viewer Tests

- **`evals/test_eval_flow.py`** (extend):
  - Build a `KeystoneRepoResult` with `unexpected_broken_commit_passes=2`; call `extract_summary`; assert `unexpected_broken_commit_passes == 2` in the returned dict.

### Edge Cases

- Repo with zero valid broken commits: flow completes, `broken_commit_hashes = []`, no broken-commit re-verification in Phase 2.
- Keystone bootstrap fails: `broken_commit_verifications = {}`, `unexpected_broken_commit_passes = 0`, `restoration_check_failed = False`.
- Broken commit reverts a change (accidentally passes): captured as `unexpected_broken_commit_passes += 1`.
- Restoration check fails (unlikely but possible if build state is corrupted): `restoration_check_failed = True`, distinct from cheating signal.