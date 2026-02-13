# Keystone

Automatically generates a working `.devcontainer/` setup for any project using an AI agent. Given a source directory, it analyzes the project structure and creates:

- `devcontainer.json` - VS Code dev container configuration
- `Dockerfile` - Container image definition
- `run_all_tests.sh` - Test runner script with artifact collection

## Usage

Run directly from the repository using `uvx`:

IMPORTANT WARNING: Running this command invokes Claude Code with `--dangerously-skip-permissions` in your current environment.

```bash
uvx --from 'git+https://github.com/imbue-ai/keystone' \
  bootstrap-devcontainer \
  --max_budget_usd 3.0 \
  --test_artifacts_dir /tmp/test_artifacts \
  --project_root ./my_project
```

Not currently supported:
* Setting up environments for projects that use Docker. (Does not currently work on itself.)

### Options

- `--project_root` - Path to the source project (required)
- `--test_artifacts_dir` - Directory for test artifacts (required)
- `--agent_cmd` - Agent command to run (default: `claude`)
- `--max_budget_usd` - Maximum budget for agent inference (default: 1.0)
- `--log_db` - Database for logging/caching. SQLite path or postgresql:// URL (default: `~/.bootstrap_devcontainer/log.sqlite`)
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
uv run bootstrap-devcontainer \
  --log_db ~/.bootstrap_devcontainer/log.sqlite \
  --max_budget_usd 3.0 \
  --test_artifacts_dir /tmp/test_artifacts \
  --project_root ./samples/python_project
```
