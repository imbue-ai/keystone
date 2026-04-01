You are an experienced, pragmatic software engineering AI agent. Do not over-engineer a solution when a simple one is possible. Keep edits minimal. If you want an exception to ANY rule, you MUST stop and get permission first.

# Agent Instructions

## Project Overview

**Keystone** is an open-source agentic tool that automatically generates a working `.devcontainer/` configuration for any git repository. Given a source repo, it spins up a Modal sandbox running Claude Code to create a `devcontainer.json`, `Dockerfile`, and `run_all_tests.sh`.

Published on PyPI as [`imbue-keystone`](https://pypi.org/project/imbue-keystone/).

**Tech stack:** Python 3.12+, [Modal](https://modal.com/) (sandboxed execution), [uv](https://github.com/astral-sh/uv) (package manager), ruff (lint/format), pyright (type checking), pytest + pytest-xdist (testing), Playwright (browser tests in evals viewer), marimo (notebooks), polars/pandas/pyarrow (data analysis).

## Repository Structure

This is a **uv workspace monorepo** with three member packages:

| Package | Path | Description |
|---------|------|-------------|
| `imbue-keystone` | `keystone/` | Core CLI and library (`keystone/src/keystone/`) |
| `evals` | `evals/` | Evaluation framework for benchmarking Keystone |
| `modal_registry` | `modal_registry/` | Shared Modal image definitions |

Other directories:
- `samples/` — Sample repos for testing (Go, Rust, Python, Node, CMake, etc.)
- `prototypes/` — Experimental code
- `scripts/` — Utility scripts
- `eval_results/` — Stored evaluation results
- `.beads/` — Issue tracking data (bd)

### Key Source Files

- `keystone/src/keystone/keystone_cli.py` — Main CLI entry point
- `keystone/src/keystone/agent_runner.py` — Agent orchestration
- `keystone/src/keystone/modal/modal_runner.py` — Modal sandbox runner
- `keystone/src/keystone/modal/image.py` — Modal image configuration
- `keystone/src/keystone/prompts.py` — LLM prompts
- `keystone/src/keystone/schema.py` — Data models
- `evals/eval_cli.py` — Eval CLI entry point
- `evals/flow.py` — Eval orchestration flow

## Issue Tracking (bd)

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Essential Commands

### Setup
```bash
uv sync                       # Install all dependencies
uv run pre-commit install     # Install pre-commit hooks
```

### Lint & Format
```bash
uv run ruff check .           # Lint
uv run ruff check . --fix     # Auto-fix lint issues
uv run ruff format .          # Format code
uv run pyright                # Type check
```

### Test
```bash
uv run pytest                           # Run all tests (auto-parallelized via -n auto)
uv run pytest keystone/tests/           # Run keystone tests only
uv run pytest evals/                    # Run eval tests only
uv run pytest -k "not manual and not modal and not agentic"  # Skip slow/external tests
```

**Test markers** (defined in `pytest.ini`):
- `manual` — Deselect with `-k "not manual"`
- `modal` — Tests that run on Modal; deselect with `-m "not modal"`
- `local_docker` — Tests that expect a local Docker daemon
- `agentic` — Tests that run a real coding agent (non-deterministic, slow)

### Running Modal/Agentic Tests

Modal credentials are configured in `~/.modal.toml` (profile `imbue`, marked `active = true`). No `MODAL_PROFILE=` prefix is needed — just run:

```bash
uv run pytest -x -k "not manual"      # All tests including modal/agentic (slow, uses real Modal sandboxes)
uv run pytest -x -m "modal"           # Modal tests only
```

These tests spin up real Modal sandboxes and may take several minutes. They require `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` in the environment for agentic tests.

### Run Keystone
```bash
uv run keystone --help        # Show CLI usage
```

## Code Style

- **No inline imports** — Put all imports at the top of the file, not inside functions.
- **Always use type annotations** — Add Python type annotations to all function parameters and return values.
- **Use uv for running Python** — Run tests with `uv run pytest`, not `python -m pytest`. Never use bare `python` or `pip`.

## Linting & Type Checking

This project uses **ruff** (linter/formatter) and **pyright** (type checker). Pre-commit hooks run these automatically.

**Fix all lint/type errors in files you touch, even pre-existing ones.** Don't leave warnings behind just because you didn't introduce them — if you edited the file, clean it up. (Exception: marimo notebooks have per-file ignores in `pyproject.toml` for cell-based import patterns; those are intentional.)

## Patterns

- **Workspace packages import each other** — `evals` depends on `imbue-keystone`; use `from keystone.<module> import ...` to import core library code.
- **Modal sandboxing** — All agent work runs inside Modal sandboxes. Docker-in-Docker is set up via `start_dockerd.sh` / `wait_for_docker.sh`.
- **Test fixtures** — Keystone tests use sample repos from `samples/` and fixture data from `keystone/tests/fixtures/`.

## Anti-Patterns

- **Don't use `python -m pytest` or bare `python`** — Always use `uv run`.
- **Don't put imports inside functions** — All imports go at the top of the file.
- **Don't rebase or force-push** — Always merge; never amend commits already pushed.
- **Don't skip type annotations** — Every function parameter and return value needs a type.

## Commit and Pull Request Guidelines

### Commit Messages
Use conventional commit format: `type: message` (e.g., `fix:`, `feat:`, `chore:`, `refactor:`, `docs:`, `test:`).

### Validation Before Committing
Run these checks on any code you changed:
```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -x -k "not manual and not modal and not agentic"
```

### Session Completion (Landing the Plane)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

1. **File issues for remaining work** — Create bd issues for anything that needs follow-up.
2. **Run quality gates** (if code changed) — Lint, type check, tests.
3. **Update issue status** — Close finished work, update in-progress items.
4. **PUSH TO REMOTE** — This is MANDATORY:
   ```bash
   git pull
   bd sync
   git push origin HEAD:<feature-branch-name>  # Push to a feature branch, NOT main
   git status  # MUST show "up to date with origin"
   ```
   **Note:** Always push to a feature branch. Never push directly to main — let the user review and merge.
5. **Clean up** — Clear stashes, prune remote branches.
6. **Verify** — All changes committed AND pushed.
7. **Hand off** — Provide context for next session.

**CRITICAL RULES:**
- **NEVER push to main without explicit user permission** — Always push to a feature branch and let the user review/merge.
- Work is NOT complete until `git push` succeeds.
- NEVER stop before pushing — that leaves work stranded locally.
- NEVER say "ready to push when you are" — YOU must push.
- If push fails, resolve and retry until it succeeds.
- **Always use merge, never rebase** — Preserve commit history with merge commits.
- **Never amend or force-push commits already pushed** — Add new fix commits instead.

## Skills

- **eval-parquet** (`.agents/skills/eval-parquet/SKILL.md`) — Schema reference and example queries for the eval results Parquet files produced by `evals/eda/eval_to_parquet_cli.py`. Use when analyzing or querying Keystone eval results.
- **run-keystone** (`.agents/skills/run-keystone/SKILL.md`) — How to install and run the Keystone CLI, including all CLI flags, available models, provider options, and example commands.
