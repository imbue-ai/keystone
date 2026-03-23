# Keystone: a managed agent to configure Dockerfiles for any repo

Keystone is an open source agentic tool that automatically generates a working `.devcontainer/` configuration for any git code repository. We built it because we kept running into the same problem: *what's the shortest path to make this arbitrary code actually run?*

Given a source repo, Keystone analyzes the project structure and creates:

- `.devcontainer/devcontainer.json` — VS Code dev container configuration
- `.devcontainer/Dockerfile` — Container image definition
- `.devcontainer/run_all_tests.sh` — Test runner script with artifact collection

Keystone builds on the existing [dev container standard](https://containers.dev/), which is also supported by [VS Code](https://code.visualstudio.com/docs/devcontainers/containers) and [GitHub Codespaces](https://github.com/features/codespaces).

## Why bother?

There are several good reasons to configure a standardized container environment for a code repo:

- Have the repo self-describe a suitable execution environment
- Use reproducible environments across development and CI/CD
- Run coding agents safely inside containers

## How to use it

1. Create a [Modal account](https://modal.com/docs/guide#getting-started) and sign in
2. Create or clone a git repository
3. Run Keystone on it
4. Check in the resulting `.devcontainer/` directory

## How it works

Keystone creates a short-lived Modal sandbox and copies your repo into it so that it can run Claude Code safely, without interfering with your local environment. The sandbox is specially configured to allow Claude to run `devcontainer build` and `docker run` commands. The agent works to create an environment suitable for the project and tries to get the project's automated tests passing in that environment.

## Why not just use plain Claude Code?

Iterating on a containerized environment is a bit trickier than writing ordinary code. Although Claude Code can run `docker build` and `docker run` to iterate on a Dockerfile, doing so requires full access to your Docker daemon. In practice, we've observed Claude attempting potentially dangerous changes to the host system — clearing Docker configuration, changing kernel settings, and so on.

In short: containerization is especially important for safety when your agent is acting like a sysadmin.

## Prerequisites

- A [Modal account](https://modal.com/docs/guide#getting-started) — used to safely sandbox Claude Code as it works on your container
- `$ANTHROPIC_API_KEY` — Keystone uses your API key to run Claude Code inside the Modal sandbox

## Installation

The package is published on PyPI as [`kystn`](https://pypi.org/project/kystn/). Install it with pip:

```bash
pip install kystn
```

Or run it directly without installing using `uvx`:

```bash
uvx kystn --help
```

Both methods provide the `keystone` CLI command.

## Example usage

```bash
# Make a repo.
git clone https://github.com/fastapi/fastapi

# Run with pip install:
keystone \
  --max_budget_usd 1.0 \
  --test_artifacts_dir /tmp/test_artifacts \
  --project_root ./fastapi

# Or run directly with uvx (no install needed):
uvx kystn \
  --max_budget_usd 1.0 \
  --test_artifacts_dir /tmp/test_artifacts \
  --project_root ./fastapi
```

### Options

- `--project_root` - Path to the source project (required)
- `--test_artifacts_dir` - Directory for test artifacts (required)
- `--agent_cmd` - Agent command to run (default: `claude`)
- `--max_budget_usd` - Maximum budget for agent inference (default: 1.0)
- `--log_db` - Database for logging/caching. SQLite path or postgresql:// URL (default: `~/.imbue_keystone/log.sqlite`)
- `--require_cache_hit` - Fail immediately if cache miss (useful for CI/testing)
- `--no_cache_replay` - Skip cache lookup but still log the run (force fresh execution)
- `--cache_version` - String appended to cache key to invalidate old entries
- `--output_file` - Path to write JSON result (defaults to stdout)
- `--agent_in_modal/--agent_local` - Run agent in Modal sandbox (default) or locally
- `--agent_time_limit_seconds` - Maximum seconds for agent execution (default: 3600)
- `--image_build_timeout_seconds` - Maximum seconds for building devcontainer image (default: 600)
- `--test_timeout_seconds` - Maximum seconds for running tests (default: 300)

## Repo Structure

This is a monorepo containing the keystone tool and its supporting infrastructure:

- **`keystone/`** — The core keystone CLI tool (Typer app, runnable via `uvx`).
- **`evals/`** — Eval harness (Prefect flows for batch evaluation).
- **`modal_registry/`** — Modal-hosted Docker registry cache.
- **`samples/`** — Sample projects used by tests.
- **`prototypes/`** — Experimental scripts.

---

## Developer Notes

### Running from source

```bash
# Run local code tree on a project.
uv run keystone \
  --log_db ~/.imbue_keystone/log.sqlite \
  --max_budget_usd 3.0 \
  --test_artifacts_dir /tmp/test_artifacts \
  --project_root ./samples/python_project
```

## Evals

The `evals/` directory contains a harness for benchmarking Keystone across many repos with different LLM providers.

### Repo list

`evals/examples/repos.jsonl` defines the repos to evaluate — one JSON object per line with fields like `id`, `repo` (git URL), `commit_hash`, `language`, `stars`, and difficulty metadata. Example:

```json
{"id": "requests", "repo": "https://github.com/psf/requests", "commit_hash": "abc123", "language": "python", ...}
```

### S3 storage

Eval results are stored in S3 at `s3://int8-datasets/keystone/evals/`. Structure:

```
s3://int8-datasets/keystone/evals/
├── repo-tarballs/              # Cached repo snapshots
│   └── {repo_id}.tar.gz
└── {run_name}/                 # e.g. 2026-03-18-cat
    └── {config_name}/          # e.g. claude-opus, codex-gpt-5.3
        └── {repo_id}/
            └── trial_{n}/
                ├── eval_result.json
                ├── keystone_stderr.log
                ├── devcontainer.tar.gz
                └── agent_dir.tar.gz
```

### Running an eval

Evals are configured with a JSON file that specifies the repo list, S3 paths, concurrency, and model configs. See `evals/examples/` for examples. Each config entry names a provider (`claude`, `codex`, or `opencode`), a model, and budget/timeout settings.

### LLM providers

Three agent providers are supported in `keystone/src/keystone/llm_provider/`:

| Provider | CLI | Example models |
|----------|-----|----------------|
| **Claude** (Anthropic) | `claude` | `claude-opus-4-6`, `claude-haiku-4-5-20251001` |
| **Codex** (OpenAI) | `codex` | `gpt-5.3-codex`, `gpt-5.1-codex-mini` |
| **OpenCode** | `opencode` | Any of 75+ supported models |

### Parquet export

`evals/eda/eval_to_parquet_cli.py` flattens eval results into a single Parquet file for analysis. Key columns: `repo_id`, `config_name`, `success`, `cost_usd`, `agent_walltime_seconds`, `tests_passed`, `tests_failed`, `input_tokens`, `output_tokens`. The full `KeystoneRepoResult` is preserved in a `raw_json` column.

### CDF plots

`evals/eda/cdf_plot.py` generates interactive HTML plots showing cumulative distribution functions of execution time across model configs. Features cross-trace repo highlighting on hover and failure markers. Useful for comparing model speed/reliability side-by-side.

### Eval viewer

`evals/viewer/generate_viewer.py` builds a self-contained HTML dashboard that loads results from S3, with:

- Per-run tabs with success rates, costs, and test stats
- Failure categorization breakdown
- Sortable/filterable table of all repo results
- Parquet caching locally at `~/.keystone_evals/viewer_cache/` for fast reloads

## Feedback welcome

Bug reports and PRs are welcome. If you're interested in this space, feel free to reach out.
