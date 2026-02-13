# Keystone: an agentic tool to configure Dockerfiles for any repo

Keystone automatically generates a working `.devcontainer/` configuration for any project using an AI agent.
Given a source repo, it analyzes the project structure and creates:

- `//devcontainer/devcontainer.json` - VS Code dev container configuration
- `//devcontainer/Dockerfile` - Container image definition
- `//devcontainer/run_all_tests.sh` - Test runner script with artifact collection

## Prerequisites for your environment

* A [Modal account] (https://modal.com/docs/guide#getting-started) -- we use this to safely sandbox Claude Code as it works on your container.
* `$ANTHROPIC_API_KEY` -- Keystone uses your API key to run Claude Code in its Modal sandbox.
* [`uvx`](https://docs.astral.sh/uv/getting-started/installation/) to run Keystone.

## Usage

Run directly from the repository using `uvx`:

IMPORTANT WARNING: Running this command invokes Claude Code with `--dangerously-skip-permissions` in your current environment.

```bash
uvx --from 'git+https://github.com/imbue-ai/keystone@prod' \
  keystone \
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

- `--output_file` - Path to write JSON result (defaults to stdout)
- `--agent_in_modal/--agent_local` - Run agent in Modal sandbox (default) or locally
- `--max_budget_usd` - Maximum budget for agent inference (default: $1.00)
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
