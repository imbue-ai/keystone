# Eval Harness for bootstrap_devcontainer

MapReduce-style evaluation harness that runs `bootstrap_devcontainer` on many code repositories.

Uses Prefect for flow orchestration with pluggable task runners - the same task code
runs identically whether executing locally or distributed.

## Installation

```bash
# From repo root - installs both bootstrap_devcontainer and evals
uv sync

# For distributed execution with Dask:
uv sync --extra dask
```

## Usage

### Test with a local directory

```bash
# Test with samples/python_project
uv run python -m cli test-local ../samples/python_project --output-dir ./test_output

# With custom budget
uv run python -m cli test-local ../samples/python_project --max-budget-usd 2.0
```

### Run on a list of repos

```bash
# Local execution (ThreadPoolTaskRunner - default)
uv run python -m cli run examples/agent_config.json5 examples/repo_list.jsonl --mode local

# Parallel processes (ProcessPoolTaskRunner)
uv run python -m cli run examples/agent_config.json5 examples/repo_list.jsonl --mode process

# Distributed with Dask (requires prefect-dask)
uv run python -m cli run examples/agent_config.json5 examples/repo_list.jsonl --mode dask
```

## Configuration

### agent_config.json5

```json5
{
    // Model configuration
    "model": "claude-sonnet-4-20250514",
    "max_budget_usd": 1.0,
    
    // Git source for bootstrap_devcontainer
    "bootstrap_git_url": "https://github.com/imbue-ai/bootstrap_devcontainer",
    "bootstrap_git_ref": "prod",  // or a specific commit hash
    
    // Execution settings
    "timeout_minutes": 30,
    
    // Cache settings
    "use_cache": true,
}
```

### repo_list.jsonl

Each line is a JSON object with `s3_repo_tarball`:

```jsonl
{"s3_repo_tarball": "s3://bucket/path/to/repo.tar.gz"}
{"s3_repo_tarball": "s3://bucket/path/to/another-repo.tar.gz"}
```

For local testing, you can use local paths instead of S3 URIs.

## Output

For each repo, the harness produces:
1. `bootstrap_result.json` - The BootstrapResult data
2. `devcontainer.tar.gz` - The generated .devcontainer directory
3. `session.jsonl` - The Claude session transcript
4. `stdout.txt` / `stderr.txt` - Raw output for debugging

A `summary.json` file is written with results for all repos.

## Environment Variables

- `ANTHROPIC_API_KEY` - Required for Claude API access
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` - Required for S3 access (Modal mode)

## Architecture

The harness uses:
- **Prefect** for flow orchestration and task management
- **Pluggable task runners** - same code runs locally or distributed:
  - `ThreadPoolTaskRunner` (default) - concurrent threads
  - `ProcessPoolTaskRunner` - parallel processes
  - `DaskTaskRunner` - distributed across cluster
- Workers need:
  - Docker (for devcontainer builds)
  - Node.js + Claude Code CLI
  - devcontainer CLI
  - uv for Python package management
