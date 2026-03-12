# Working with Eval Parquet Files

You have access to Parquet files produced by `evals/eda/eval_to_parquet_cli.py`.
These files contain flattened results from Keystone eval runs.

## How the Parquet is produced

```bash
# S3 → local
uv run python evals/eda/eval_to_parquet_cli.py \
    s3://int8-datasets/keystone/evals/<run_name> \
    ./results.parquet

# Local → local
uv run python evals/eda/eval_to_parquet_cli.py \
    /path/to/eval_output /tmp/results.parquet
```

## Schema

Each row is one trial (one agent run on one repo). Columns:

| Column | Type | Description |
|---|---|---|
| `source_path` | str | fsspec path to the original `eval_result.json` this row was read from |
| `raw_json` | str | Full `KeystoneRepoResult` serialized as JSON — deserialize with `KeystoneRepoResult.model_validate_json(row.raw_json)` to access any field |
| `config_name` | str \| null | `EvalConfig.name` — the primary identifier for which eval configuration was used (e.g. model name or experiment label) |
| `repo_id` | str | Unique repo identifier (e.g. `"requests"`, `"flask"`) |
| `trial_index` | int \| null | Trial number (0-indexed) when `trials_per_repo > 1` |
| `success` | bool | Whether the keystone run succeeded |
| `error_message` | str \| null | Error message if `success` is False |
| `agent_exit_code` | int \| null | Agent process exit code |
| `agent_walltime_seconds` | float \| null | Wall-clock time for the agent |
| `agent_timed_out` | bool \| null | Whether the agent hit its time limit |
| `cost_usd` | float \| null | Inference cost in USD |
| `input_tokens` | int \| null | LLM input tokens consumed |
| `output_tokens` | int \| null | LLM output tokens consumed |
| `image_build_seconds` | float \| null | Docker image build time |
| `test_execution_seconds` | float \| null | Test suite execution time |
| `tests_passed` | int \| null | Number of tests passed |
| `tests_failed` | int \| null | Number of tests failed |
| `summary` | str \| null | `agent.summary.message` — the agent's final summary of what it did |
| `status_messages` | str (JSON) | JSON array of `{"timestamp": "...", "message": "..."}` objects — the agent's step-by-step progress |

## Primary key

Rows are uniquely identified by `(config_name, repo_id, trial_index)`.

## Example queries

When the user asks you to analyze eval results, load the parquet with pandas and
answer their questions. Here are common patterns:

```python
import pandas as pd
df = pd.read_parquet("results.parquet")
```

### Success rate by config
```python
df.groupby("config_name")["success"].mean().sort_values(ascending=False)
```

### Cost summary by config
```python
df.groupby("config_name").agg(
    mean_cost=("cost_usd", "mean"),
    median_cost=("cost_usd", "median"),
    total_cost=("cost_usd", "sum"),
)
```

### Slowest repos
```python
df.nlargest(10, "agent_walltime_seconds")[["config_name", "repo_id", "agent_walltime_seconds", "success"]]
```

### Failures with error messages
```python
df[~df["success"]][["config_name", "repo_id", "error_message", "summary"]]
```

### Compare two configs head-to-head on the same repos
```python
a, b = "claude-opus", "codex"
merged = df[df.config_name == a].merge(
    df[df.config_name == b],
    on=["repo_id", "trial_index"],
    suffixes=("_a", "_b"),
)
merged["both_pass"] = merged.success_a & merged.success_b
merged["a_only"] = merged.success_a & ~merged.success_b
merged["b_only"] = ~merged.success_a & merged.success_b
merged[["both_pass", "a_only", "b_only"]].sum()
```

### Parse status_messages for a specific run
```python
import json
row = df[(df.config_name == "claude-opus") & (df.repo_id == "requests")].iloc[0]
messages = json.loads(row.status_messages)
for m in messages:
    print(f"[{m['timestamp']}] {m['message']}")
```

### Reconstruct full KeystoneRepoResult from raw_json
```python
from eval_schema import KeystoneRepoResult
result = KeystoneRepoResult.model_validate_json(row.raw_json)
# Now access any nested field, e.g.:
result.bootstrap_result.verification.test_results
```

### Test pass rate distribution
```python
df["test_pass_rate"] = df.tests_passed / (df.tests_passed + df.tests_failed)
df.groupby("config_name")["test_pass_rate"].describe()
```

## Tips

- `raw_json` preserves everything — if a column doesn't exist for something you need, deserialize `raw_json` back into `KeystoneRepoResult`.
- `status_messages` is a JSON string, not a list — use `json.loads()` to parse it.
- `source_path` lets you go back to the original file on S3 or local disk for debugging.
- Null values in agent/verification columns mean the run failed before reaching that stage.
