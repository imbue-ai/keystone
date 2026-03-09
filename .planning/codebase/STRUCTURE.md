# Codebase Structure

**Analysis Date:** 2026-03-09

## Directory Layout

```
bootstrap_devcontainer/
├── .planning/              # GSD analysis and planning documents
│   └── codebase/
├── keystone/               # Main agent library (workspace member)
│   ├── src/keystone/       # Package source code
│   │   ├── llm_provider/   # LLM backend implementations
│   │   ├── modal/          # Modal cloud execution
│   │   └── *.py            # Core agent modules
│   └── tests/              # Test suite with fixtures
├── evals/                  # Evaluation harness (workspace member)
│   ├── eda/                # Exploratory data analysis scripts
│   ├── scripts/            # Utility scripts
│   ├── viewer/             # Result visualization
│   ├── viz/                # Visualization utilities
│   ├── test_data/          # Test datasets
│   └── *.py                # Evaluation CLI and flow
├── modal_registry/         # Modal infrastructure utilities
├── samples/                # Example projects (various languages)
├── prototypes/             # Experimental code
├── scripts/                # Root-level utility scripts
└── pyproject.toml          # Workspace root configuration
```

## Directory Purposes

**keystone/:**
- Purpose: Core agent library for generating .devcontainer/ setups
- Contains: Agent runner abstractions, LLM providers, prompt generation, evaluation
- Key files: `pyproject.toml`, `src/keystone/keystone_cli.py`, `tests/`
- Workspace member with CLI entrypoint `keystone`

**keystone/src/keystone/:**
- Purpose: Main package source tree
- Key patterns: One module per abstraction (llm_provider, modal, evaluator, etc.)
- Contains: 25 Python modules organized by concern

**keystone/src/keystone/llm_provider/:**
- Purpose: LLM backend abstraction layer
- Contains: base.py (abstract AgentProvider), registry.py (provider lookup), and implementations
- Files: claude.py, codex.py, opencode.py, pricing.py
- Pattern: Each backend implements same interface; registry enables name-based lookup

**keystone/src/keystone/modal/:**
- Purpose: Modal cloud sandbox execution
- Contains: modal_runner.py (execution logic), image.py (container image construction)
- Pattern: ManagedProcess wrapper handles async stream capture

**keystone/tests/:**
- Purpose: Test suite with fixtures, fake agents, and snapshot testing
- Patterns: Conftest fixtures, pytest markers (agentic, modal, local_docker, manual)
- Contains: fake agent implementations for deterministic testing, snapshot extension

**evals/:**
- Purpose: Distributed evaluation harness for testing on multiple repos
- Contains: Prefect-based orchestration, S3 integration, configuration schemas
- Key files: flow.py (main orchestration), eval_schema.py (config/result types), eval_cli.py
- Workspace member with CLI entrypoint `eval-harness`

**evals/eda/:**
- Purpose: Scripts for exploratory data analysis and repo enrichment
- Contains: fetch_repos.py, merge_repo_lists.py, enrich_test_counts.py
- Pattern: Standalone scripts for data pipeline (not imported by main code)

**evals/scripts/:**
- Purpose: Support scripts for eval infrastructure
- Examples: populate_commit_hashes.py (adds commit hash to JSONL)

**evals/viewer/:**
- Purpose: Web-based result visualization
- Contains: generate_viewer.py (generates static HTML from results)

**samples/:**
- Purpose: Example projects across different languages/frameworks
- Contains: python_project, node_project, go_project, rust_project, fullstack_project, cmake_vcpkg_project, etc.
- Pattern: Each sample is a complete project with build/test setup
- Used by: E2E tests (`test_e2e_fake_agent.py`)

**prototypes/:**
- Purpose: Experimental features and alternate approaches
- Examples: modal_devcontainer_verification, modal_docker
- Pattern: Proof-of-concept code, not used in main codebase

**scripts/:**
- Purpose: Root-level utility scripts
- Pattern: Standalone scripts for build, release, or admin tasks

**modal_registry/:**
- Purpose: Modal infrastructure and load testing
- Contains: mirror_registry_app.py, load_test_v2.py
- Pattern: Modal-specific utilities separate from core library

## Key File Locations

**Entry Points:**
- `keystone/src/keystone/keystone_cli.py`: Main CLI command (Typer app, `bootstrap()` function)
- `evals/eval_cli.py`: Evaluation harness CLI
- `keystone/src/keystone/__init__.py`: Package exports

**Configuration:**
- `pyproject.toml`: Root workspace config, dependency groups, tool settings
- `keystone/pyproject.toml`: Keystone library config (dependencies, CLI entrypoint)
- `evals/pyproject.toml`: Evals library config (depends on keystone workspace member)
- `pyright.config.json`: Type checking configuration
- `pytest.ini`: Pytest configuration (plugins, markers, log settings)

**Core Logic:**
- `keystone/src/keystone/schema.py`: Pydantic schemas (AgentConfig, KeystoneConfig, StreamEvent, etc.)
- `keystone/src/keystone/agent_runner.py`: Abstract runner interface and base classes
- `keystone/src/keystone/modal/modal_runner.py`: Modal cloud execution implementation
- `keystone/src/keystone/cached_runner.py`: Cache wrapper around any runner
- `keystone/src/keystone/agent_log.py`: Database logging and cache key computation
- `keystone/src/keystone/llm_provider/base.py`: Abstract AgentProvider interface
- `keystone/src/keystone/llm_provider/registry.py`: Provider lookup registry
- `keystone/src/keystone/evaluator.py`: Verification and repair via evaluator LLM
- `keystone/src/keystone/prompts.py`: Agent prompt templates and assembly
- `evals/flow.py`: Prefect orchestration for distributed eval runs

**Testing:**
- `keystone/tests/conftest.py`: Pytest fixtures (fake agents, temp dirs)
- `keystone/tests/test_cli.py`: CLI integration tests
- `keystone/tests/test_e2e_fake_agent.py`: E2E tests with deterministic fake agents
- `keystone/tests/test_e2e_agentic.py`: E2E tests with real agents (marked agentic)
- `evals/test_eval_flow.py`: Eval harness tests
- `evals/test_config_parsing.py`: Config parsing tests

## Naming Conventions

**Files:**
- Modules use `snake_case_name.py`
- Tests use `test_*.py` or `*_test.py`
- Main modules are single-concern: `agent_runner.py`, `evaluator.py`, `prompts.py`
- Provider implementations: `claude.py`, `codex.py`, `opencode.py`

**Directories:**
- Package directories use `snake_case`: `llm_provider`, `modal_registry`
- Test directories: `tests/`
- Support directories: `scripts/`, `eda/`, `viewer/`, `viz/`

**Classes:**
- PascalCase: `AgentRunner`, `ModalAgentRunner`, `CachedAgentRunner`, `AgentProvider`
- Enum types: `LLMModel`, `StreamType`
- Schema types: `KeystoneConfig`, `AgentConfig`, `BootstrapResult`

**Functions:**
- snake_case: `build_prompt()`, `generate_devcontainer_json()`, `run_process()`
- Private functions: `_stream_reader()`, `_s3_exists()`

**Constants:**
- SCREAMING_SNAKE_CASE: `DEFAULT_AGENT_TIMEOUT`, `TIMEOUT_EXIT_CODE`, `GUARDRAIL_SCRIPT_PATH`
- Prefixed by concern: `STATUS_MARKER`, `SUMMARY_MARKER`, `ANSI_BLUE`

## Where to Add New Code

**New Feature:**
- Primary code: `keystone/src/keystone/` (new module if cross-cutting concern, extend existing if specialized)
- Tests: `keystone/tests/test_*.py` (co-located with similar feature tests)
- Example: Adding new verification step → new module `keystone/src/keystone/verification_step.py` + `tests/test_verification_step.py`

**New LLM Provider Backend:**
- Implementation: `keystone/src/keystone/llm_provider/new_backend.py`
- Pattern: Inherit from `AgentProvider`, implement abstract methods
- Register: Add to `PROVIDER_REGISTRY` in `llm_provider/registry.py`
- Tests: `keystone/tests/test_llm_provider.py` (add test cases for new backend)

**New Agent Runner Execution Mode:**
- Implementation: `keystone/src/keystone/new_runner_type.py` or extend `modal/` directory
- Pattern: Inherit from `AgentRunner`, implement `run()`, `verify()`, `get_devcontainer_tarball()`
- Tests: `keystone/tests/test_e2e_*.py` (add E2E test with new runner type)

**New Evaluation Dataset:**
- JSONL repo list: `evals/data/new_dataset.jsonl` (create data directory as needed)
- Config: `evals/configs/new_dataset.json` or extend existing config
- Pattern: EvalRunConfig references repo list and outputs to S3 prefix

**Utilities & Helpers:**
- Shared helper functions: `keystone/src/keystone/new_utils.py`
- Process utilities: Extend `process_runner.py`
- Git utilities: Extend `git_utils.py`
- Docker utilities: Extend `docker_utils.py`

**Evaluation Scripts (EDA/Support):**
- One-off scripts: `evals/scripts/new_script.py`
- Pattern: Standalone, typically reads from S3 or local data, outputs JSON/CSV
- Import restrictions: Avoid importing core keystone modules; use subprocess if needed

## Special Directories

**keystone/tests/fixtures/:**
- Purpose: Reusable test data and fixture definitions
- Generated: Yes (__snapshots__ populated by syrupy on first test run)
- Committed: Yes (snapshots committed to repo for deterministic testing)

**keystone/tests/__snapshots__/:**
- Purpose: Snapshot files for snapshot-based testing (syrupy)
- Generated: Yes (auto-generated on first test run with --snapshot-update)
- Committed: Yes (snapshots are source of truth)

**evals/test_data/:**
- Purpose: Small datasets for testing (e.g., tiny_codex sample)
- Generated: No (manually created test data)
- Committed: Yes

**.venv/:, .pytest_cache/, .ruff_cache/:**
- Purpose: Development artifacts
- Generated: Yes (created by dependency installation and tool runs)
- Committed: No (.gitignored)

**eval_results/:**
- Purpose: Local cache of evaluation results
- Generated: Yes (created during eval runs)
- Committed: No

**dist/:**
- Purpose: Built wheel distributions
- Generated: Yes (created by `hatchling build`)
- Committed: No

## Import Organization

**Standard Order (enforced by isort via Ruff):**
1. Standard library imports
2. Third-party imports (anthropic, openai, modal, prefect, pydantic, etc.)
3. Local keystone imports
4. Local evals imports (in evals package only)

**Path Aliases:**
- None explicitly defined in pyrightconfig.json, but:
- Keystone imports from keystone package directly: `from keystone.schema import ...`
- Evals imports from keystone workspace member: `from keystone.agent_log import ...`

**First-Party Packages (isort known-first-party):**
- `keystone` (root workspace)
- `evals` (evals subpackage)

---

*Structure analysis: 2026-03-09*
