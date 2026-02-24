# Eval Harness for keystone

Runs `keystone` on many git repositories and collects results.
Per-repo results are uploaded to S3 as each task completes.

## Installation

```bash
# From repo root
uv sync
```

Requires AWS credentials in your environment for S3 access (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or an AWS profile).

## Usage — Local (recommended for ad hoc work)

Run the flow directly on your laptop. The Prefect orchestrator runs in-process; keystone tasks run on Modal. **This always uses your current code tree — no deploy step needed.** Best for iterating on code changes.

```bash
# Quick smoke test: run on just 1 repo
uv run --package evals eval-harness \
    --repo_list_path evals/examples/repos.jsonl \
    --s3_output_prefix s3://int8-datasets/keystone/evals/runs/test/ \
    --max_budget_usd 1.0 \
    --limit 1

# Run on only the first 2 repos
uv run --package evals eval-harness \
    --repo_list_path evals/examples/repos.jsonl \
    --s3_output_prefix s3://int8-datasets/keystone/evals/runs/test/ \
    --max_budget_usd 1.0 \
    --limit 2

# Run on all repos
uv run --package evals eval-harness \
    --repo_list_path evals/examples/repos.jsonl \
    --s3_output_prefix s3://int8-datasets/keystone/evals/runs/$(date +%Y%m%d_%H%M%S)/ \
    --max_budget_usd 1.0

# Force fresh execution (skip keystone cache)
uv run --package evals eval-harness \
    --repo_list_path evals/examples/repos.jsonl \
    --s3_output_prefix s3://int8-datasets/keystone/evals/runs/$(date +%Y%m%d_%H%M%S)/ \
    --no_cache_replay \
    --timeout_minutes 60 \
    --max_budget_usd 10.0

# Multi-model comparison from a config file
uv run --package evals eval-harness \
    --config_file evals/examples/tiny_two_model_test.json
```

## Usage — Prefect Cloud

Deploy the flow to a Prefect-managed work pool so it runs in the cloud instead of on your laptop.

> **When do I need to redeploy?** Code is shipped to Prefect Cloud at deploy time. You must re-run `prefect deploy` every time you change `flow.py`, `config.py`, or anything they import. If you're only changing **parameters** (not code), you can skip the redeploy and pass `--param` overrides at run time.

### One-time setup

```bash
# 1. Create a managed work pool
prefect work-pool create keystone-eval --type prefect:managed

# 2. Store AWS credentials as Prefect Secrets (pulls from ~/.aws/credentials)
prefect block create secret/aws-access-key-id --value "$(aws configure get aws_access_key_id)"
prefect block create secret/aws-secret-access-key --value "$(aws configure get aws_secret_access_key)"

# 3. Deploy the flow (run from evals/)
cd evals && prefect deploy
```

### Running

```bash
# Trigger a run (uses defaults from prefect.yaml)
prefect deployment run eval_keystone/eval-keystone

# Quick test: run on just 1 repo
prefect deployment run eval_keystone/eval-keystone --param limit=1

# Trigger with overrides
prefect deployment run eval_keystone/eval-keystone \
    --param s3_output_prefix=s3://int8-datasets/keystone/evals/runs/$(date +%Y%m%d_%H%M%S)/ \
    --param limit=5
```

The deployment is configured in `prefect.yaml`. Edit it to change default parameters, work pool, etc.

### Local vs. Cloud cheat sheet

| Scenario | Command | Redeploy needed? |
|---|---|---|
| Ad hoc / iterating on code | `uv run eval-harness --limit 1 ...` | No — always uses current tree |
| Push code to Prefect Cloud | `cd evals && prefect deploy` | Yes — every code change |
| Change only parameters | `prefect deployment run ... --param limit=1` | No — params are runtime |

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
uv run --package evals pytest evals/test_eval_flow.py -v -m "slow and modal"
```
