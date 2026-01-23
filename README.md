# Bootstrap Dev Container

Automatically generates a working `.devcontainer/` setup for any project using an AI agent. Given a source directory, it analyzes the project structure and creates:

- `devcontainer.json` - VS Code dev container configuration
- `Dockerfile` - Container image definition
- `run_all_tests.sh` - Test runner script with artifact collection

## Usage

Run directly from the repository using `uvx`:

IMPORTANT WARNING: Running this command invokes Claude Code with `--dangerously-skip-permissions` in your current environment.

```bash
uvx --from 'git+https://github.com/imbue-ai/bootstrap_devcontainer@main#subdirectory=bootstrap_devcontainer' \
  bootstrap-devcontainer \
  --sqlite_cache_dir ~/.cache/bootstrap_devcontainer.sqlite \
  --test_artifacts_dir /tmp/test_artifacts \
  --max_budget_usd 2.0 \
  --project_root ./samples/python_project
```

Not currently supported:
* Setting up environments for projects that use Docker. (Does not currently work on itself.)

### Options

- `--project_root` - Path to the source project (required)
- `--test_artifacts_dir` - Directory for test artifacts (required)
- `--agent_cmd` - Agent command to run (default: `claude`)
- `--max_budget_usd` - Maximum budget for agent inference (default: 1.0)
- `--sqlite_cache_file` - SQLite cache file path (enables caching)
- `--output_file` - Path to write JSON result (defaults to stdout)

---

## Developer Notes

### Running from source

```bash
# Run local code tree on a project.
uv run bootstrap-devcontainer \
  --sqlite_cache_dir ~/.cache/bootstrap_devcontainer.sqlite \
  --test_artifacts_dir /tmp/test_artifacts \
  --project_root ~/nix_pytest_docker_build.small/tmp/nix-build-python3.11-geopy-2.4.0.drv-0/
```
