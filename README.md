# Bootstrap Dev Container

Automatically generates a working `.devcontainer/` setup for any project using an AI agent. Given a source directory, it analyzes the project structure and creates:

- `devcontainer.json` - VS Code dev container configuration
- `Dockerfile` - Container image definition
- `run_all_tests.sh` - Test runner script with artifact collection

## Usage

Run directly from the repository using `uvx`:

```bash
uvx --from 'git+https://github.com/imbue-ai/bootstrap_devcontainer@prod#subdirectory=bootstrap_devcontainer' \
  bootstrap-devcontainer --project_root <project_path> --test_artifacts_dir ./artifacts
```

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
uv run bootstrap-devcontainer --project_root samples/python_project --test_artifacts_dir ./artifacts
```
