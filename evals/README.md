# Eval Harness for keystone

Runs `keystone` on many git repositories and collects results.
Per-repo results are uploaded to S3 as each task completes.

## Installation

```bash
# From repo root
uv sync
```

Requires AWS credentials in your environment for S3 access (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or an AWS profile).

## Usage — Local

Run the flow directly on your laptop. The Prefect orchestrator runs in-process; keystone tasks run on Modal.

```bash
# Run on all repos
cd evals
uv run eval-harness run \
    --repo_list_path examples/repos.jsonl \
    --s3_output_prefix s3://int8-datasets/keystone/evals/runs/$(date +%Y%m%d_%H%M%S)/ \
    --max_budget_usd 1.0

# Run on only the first 3 repos (useful for testing)
uv run eval-harness run \
    --repo_list_path examples/repos.jsonl \
    --s3_output_prefix s3://int8-datasets/keystone/evals/runs/test/ \
    --max_budget_usd 1.0 \
    --limit 3

# Force fresh execution (skip keystone cache)
uv run eval-harness run \
    --repo_list_path examples/repos.jsonl \
    --s3_output_prefix s3://int8-datasets/keystone/evals/runs/$(date +%Y%m%d_%H%M%S)/ \
    --no_cache_replay \
    --timeout_minutes 60 \
    --max_budget_usd 10.0
```

## Usage — Prefect Cloud

Deploy the flow to a Prefect work pool so it runs in the cloud instead of on your laptop.

```bash
cd evals

# One-time: deploy the flow
prefect deploy

# Trigger a run (uses defaults from prefect.yaml)
prefect deployment run eval_keystone/eval-keystone

# Trigger with overrides
prefect deployment run eval_keystone/eval-keystone \
    --param s3_output_prefix=s3://int8-datasets/keystone/evals/runs/$(date +%Y%m%d_%H%M%S)/ \
    --param limit=5
```

The deployment is configured in `prefect.yaml`. Edit it to change default parameters, work pool, etc.

## Configuration

### repo_list.jsonl

Each line is a JSON object with `id` (unique short name) and `repo` (git URL):

```jsonl
{"id": "requests", "repo": "https://github.com/psf/requests", "difficulty": "easy"}
{"id": "flask", "repo": "https://github.com/pallets/flask", "difficulty": "easy"}
```

Optional metadata fields (preserved in output): `rank`, `language`, `build_system`, `tests`, `difficulty`, `notes`.

## Output

Results are written to S3 under the `--s3_output_prefix`:

```
s3://int8-datasets/keystone/evals/runs/20260220_143000/
├── eval_summary.json          # Full eval output (all repos)
├── requests/
│   ├── eval_result.json       # Per-repo result
│   └── keystone_stderr.log    # keystone stderr for debugging
├── flask/
│   ├── eval_result.json
│   └── keystone_stderr.log
└── ...
```

Repo tarballs are cached separately at `--s3_repo_cache_prefix` (default: `s3://int8-datasets/keystone/evals/repo-tarballs/`).

## Architecture

1. **Archive** — Repos are cloned, `git archive`'d to tarballs, and uploaded to S3 (Prefect-cached, skipped on re-runs)
2. **Execute** — Each repo tarball is downloaded, extracted, and `keystone` runs with `--agent_in_modal`
3. **Upload** — Per-repo `eval_result.json` and `keystone_stderr.log` are uploaded to S3 as each task finishes
4. **Summarize** — `eval_summary.json` is written with all results

Uses Prefect for task orchestration and fsspec for S3 storage.

## Testing

```bash
# Run the integration test (requires Modal)
cd evals
uv run pytest test_eval_flow.py -v -m "slow and modal"
```
