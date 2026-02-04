# Eval Harness for bootstrap_devcontainer

Runs `bootstrap_devcontainer` on many git repositories and collects results.

## Installation

```bash
# From repo root
uv sync
```

## Usage

```bash
# Run on a list of repos
uv run python -m evals.eval_cli run \
    --repo_list_path evals/examples/repos.jsonl \
    --clone_dir ~/.cache/bootstrap_eval/repos \
    --worktree_dir ~/.cache/bootstrap_eval/worktrees \
    --output_path ./eval_output.json \
    --max_budget_usd 1.0

# With caching database
uv run python -m evals.eval_cli run \
    --repo_list_path evals/examples/repos.jsonl \
    --log_db ~/.bootstrap_devcontainer/eval.sqlite \
    --output_path ./eval_output.json

# Run on only the first N repos (useful for testing)
uv run python -m evals.eval_cli \
    --repo_list_path evals/examples/repos.jsonl \
    --clone_dir ~/.cache/bootstrap_eval/repos \
    --worktree_dir ~/.cache/bootstrap_eval/worktrees \
    --timeout_minutes 60 \
    --max_budget_usd 10.0 \
    --limit 1 \
    --output_path ./eval_output.json
```

## Configuration

### repo_list.jsonl

Each line is a JSON object with at minimum a `repo` field:

```jsonl
{"repo": "https://github.com/psf/requests", "difficulty": "easy"}
{"repo": "https://github.com/pallets/flask", "difficulty": "easy"}
```

Optional metadata fields (preserved in output): `rank`, `language`, `build_system`, `tests`, `difficulty`, `notes`.

## Output

The output JSON contains:

```json
{
  "bootstrap_devcontainer_version": {
    "git_hash": "abc123...",
    "branch": "main",
    "commit_count": 100,
    "commit_timestamp": "2024-01-01T00:00:00",
    "is_dirty": false
  },
  "repos": [
    {"repo": "https://github.com/psf/requests", "commit_hash": "def456..."}
  ],
  "results": [
    {
      "repo_entry": {"repo": "...", "commit_hash": "..."},
      "success": true,
      "bootstrap_result": {...}
    }
  ]
}
```

## Architecture

1. **Clone** - Repos are cloned to `--clone_dir` (cached, reused across runs)
2. **Worktree** - A git worktree is created in `--worktree_dir` for each run
3. **Execute** - `bootstrap-devcontainer` CLI runs with `--agent_in_modal`
4. **Collect** - Results are aggregated with version stamps

Uses Prefect for task orchestration with caching.

## Testing

```bash
# Run the integration test (requires Modal)
cd evals
uv run pytest test_eval_flow.py -v -m "slow and modal"
```
