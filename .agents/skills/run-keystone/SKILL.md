---
name: run-keystone
description: How to install and run the Keystone CLI to generate devcontainers
advertise: true
---

# Running Keystone

Keystone generates a working `.devcontainer/` configuration for any git repository. It creates a `devcontainer.json`, `Dockerfile`, and `run_all_tests.sh` by running a coding agent inside a Modal sandbox.

## Installation

```bash
# From PyPI
pip install kystn

# Or run without installing
uvx kystn --help
```

Both install the `keystone` CLI command.

## Prerequisites

- **Modal account** — [sign up here](https://modal.com/docs/guide#getting-started), then `modal token set`
- **`$ANTHROPIC_API_KEY`** — set in your environment (used by the agent inside the sandbox)
- **`$DOCKER_REGISTRY_MIRROR`** — URL of a Docker Hub pull-through cache (e.g. `https://mirror.gcr.io`), required when running in Modal

## Basic usage

```bash
git clone https://github.com/psf/requests
keystone \
  --project_root ./requests \
  --provider claude \
  --model claude-opus-4-6 \
  --claude_reasoning_level medium \
  --max_budget_usd 1.0 \
  --test_artifacts_dir /tmp/test_artifacts
```

## CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--project_root` | *(required)* | Path to the git repo |
| `--test_artifacts_dir` | *(required)* | Directory for JUnit XML test reports |
| `--provider` | `claude` | LLM provider: `claude`, `codex`, or `opencode` |
| `--model` | *(required)* | Model identifier (see below) |
| `--max_budget_usd` | `1.0` | Cost cap for agent inference |
| `--agent_time_limit_seconds` | `3600` | Agent timeout |
| `--image_build_timeout_seconds` | `1800` | Docker build timeout |
| `--test_timeout_seconds` | `1800` | Test suite timeout |
| `--agent_in_modal` / `--run_agent_locally_with_dangerously_skip_permissions` | Modal | Run in Modal sandbox (default) or locally |
| `--claude_reasoning_level` | *(required for claude)* | `low`, `medium`, or `high` |
| `--codex_reasoning_level` | *(required for codex)* | `low`, `medium`, or `high` |
| `--guardrail` / `--no_guardrail` | enabled | Structural checks on agent output |
| `--use_agents_md` / `--no_use_agents_md` | disabled | Use AGENTS.md + short prompt instead of full inline prompt |
| `--log_db` | `~/.imbue_keystone/log.sqlite` | SQLite or PostgreSQL URL for caching/logging |
| `--require_cache_hit` | `false` | Fail on cache miss (for CI) |
| `--no_cache_replay` | `false` | Skip cache, force fresh run |
| `--output_file` | stdout | Write JSON result to file |
| `--docker_registry_mirror` | `$DOCKER_REGISTRY_MIRROR` | Pull-through cache URL |
| `--cost_poll_interval_seconds` | `30` | How often to poll cost and enforce budget (0 disables) |

## Available models

| Provider | Model ID | Enum |
|----------|----------|------|
| claude | `claude-opus-4-6` | `LLMModel.OPUS` |
| claude | `claude-haiku-4-5-20251001` | `LLMModel.HAIKU` |
| codex | `gpt-5.1-codex-mini` | `LLMModel.CODEX_MINI` |
| codex | `gpt-5.2-codex` | `LLMModel.CODEX` |
| codex | `gpt-5.3-codex` | `LLMModel.CODEX_53` |
| codex | `gpt-5.4` | `LLMModel.GPT_54` |
| opencode | `anthropic/claude-opus-4-6` | `LLMModel.OPENCODE_OPUS` |
| opencode | `anthropic/claude-haiku-4-5` | `LLMModel.OPENCODE_HAIKU` |
| opencode | `openai/gpt-5.1-codex-mini` | `LLMModel.OPENCODE_CODEX_MINI` |
| opencode | `openai/gpt-5.2-codex` | `LLMModel.OPENCODE_CODEX` |
| opencode | `openai/gpt-5.4` | `LLMModel.OPENCODE_GPT_54` |

## Output

Keystone writes a `BootstrapResult` JSON to stdout (or `--output_file`) containing:

- `success` — whether the devcontainer was built and tests passed
- `agent` — execution details (duration, cost, tokens, status messages)
- `verification` — image build time, test execution time, pass/fail counts
- `generated_files` — contents of the generated `devcontainer.json`, `Dockerfile`, and `run_all_tests.sh`
- `error_message` — what went wrong (if anything)

The generated `.devcontainer/` directory is also written directly into `--project_root`.

## Examples

```bash
# Claude with high reasoning
keystone --project_root ./myrepo --provider claude --model claude-opus-4-6 \
  --claude_reasoning_level high --max_budget_usd 5.0 \
  --test_artifacts_dir /tmp/artifacts

# Codex
keystone --project_root ./myrepo --provider codex --model gpt-5.3-codex \
  --codex_reasoning_level medium --max_budget_usd 5.0 \
  --test_artifacts_dir /tmp/artifacts

# OpenCode (no reasoning level, no cost cap)
keystone --project_root ./myrepo --provider opencode \
  --model anthropic/claude-opus-4-6 --max_budget_usd 0 \
  --test_artifacts_dir /tmp/artifacts

# Run locally (no Modal sandbox — use with caution)
keystone --project_root ./myrepo --provider claude --model claude-opus-4-6 \
  --claude_reasoning_level medium --max_budget_usd 1.0 \
  --run_agent_locally_with_dangerously_skip_permissions \
  --test_artifacts_dir /tmp/artifacts

# From source
uv run keystone --project_root ./myrepo --provider claude --model claude-opus-4-6 \
  --claude_reasoning_level medium --max_budget_usd 1.0 \
  --test_artifacts_dir /tmp/artifacts
```
