# Bootstrap Dev Container

Eval harness, test infrastructure, and supporting tools for [keystone](https://github.com/imbue-ai/keystone).

## Repo Structure

- **`keystone/`** — Git subtree of [imbue-ai/keystone](https://github.com/imbue-ai/keystone). The core CLI tool (Typer app, runnable via `uvx`). **Do not edit files here directly** — see subtree workflow below.
- **`evals/`** — Eval harness (Prefect flows for batch evaluation).
- **`modal_registry/`** — Modal-hosted Docker registry cache.
- **`samples/`** — Sample projects used by tests.
- **`prototypes/`** — Experimental scripts.

## Keystone Subtree

The `keystone/` directory is managed as a [git subtree](https://www.atlassian.com/git/tutorials/git-subtree) pointing at `https://github.com/imbue-ai/keystone.git` (branch `main`).

### Pull upstream changes into this repo

```bash
git subtree pull --prefix=keystone https://github.com/imbue-ai/keystone.git main --squash
```

### Push local keystone/ changes upstream

If you make changes inside `keystone/` in this repo and want to push them back:

```bash
git subtree push --prefix=keystone https://github.com/imbue-ai/keystone.git main
```

### Tip: add a remote alias

```bash
git remote add keystone https://github.com/imbue-ai/keystone.git

# Then the commands become:
git subtree pull --prefix=keystone keystone main --squash
git subtree push --prefix=keystone keystone main
```

## Usage

Run keystone directly from GitHub using `uvx`:

> **WARNING:** This invokes Claude Code with `--dangerously-skip-permissions` in your current environment.

```bash
uvx --from 'git+https://github.com/imbue-ai/keystone@main' \
  keystone \
  --log_db ~/.imbue_keystone/log.sqlite \
  --max_budget_usd 3.0 \
  --test_artifacts_dir /tmp/test_artifacts \
  --project_root ./samples/python_project
```

Not currently supported:
* Setting up environments for projects that use Docker. (Does not currently work on itself.)

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
- `--agent_time_limit_secs` - Maximum seconds for agent execution (default: 3600)
- `--image_build_timeout_secs` - Maximum seconds for building devcontainer image (default: 600)
- `--test_timeout_secs` - Maximum seconds for running tests (default: 300)

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
