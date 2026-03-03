# Repo Explorer – EDA tools for sampling GitHub repos

Interactive tools for fetching GitHub repository metrics and exploring them
with a Plotly parallel coordinates plot to select evaluation candidates.

## Quick Start

```bash
# 1. Install dependencies (including pyarrow for parquet)
uv sync              # pyarrow is a core dependency
uv pip install plotly ipywidgets numpy  # for the notebook

# 2. Fetch repo metrics (needs a GitHub token with public repo access)
export GITHUB_TOKEN=ghp_...
uv run python evals/eda/fetch_repos.py      # writes repos.parquet; cached to .api_cache/

# 3. Open the notebook
jupyter lab evals/eda/repo_explorer.ipynb
```

API responses are cached to `evals/eda/.api_cache/` so reruns are instant.
Use `--no-cache` to force fresh fetches.

## What it does

**`fetch_repos.py`** queries the GitHub GraphQL API, sampling repos stratified
by language (12 languages) × star-count bucket (6 ranges from 10→500k stars).
This avoids the bias of only picking top-starred repos. For each repo it
collects:

| Metric | Description |
|---|---|
| `stars` | Stargazer count |
| `forks` | Fork count |
| `size_mb` | Disk usage in MB |
| `total_commits` | Total commits on default branch |
| `recent_commits_90d` | Commits in the last 90 days |
| `open_issues` | Open issue count |
| `open_prs` | Open PR count |
| `language` | Primary language |
| `license` | SPDX license ID |
| `topics` | Top 5 repo topics |

**`repo_explorer.ipynb`** renders an interactive Plotly parallel coordinates
plot colored by language. Drag ranges on any axis to filter, and the table
below updates live showing selected repo names. An "Export selected → JSONL"
button writes `selected_repos.jsonl` in the same format as
`evals/examples/repos.jsonl`.

## Options

```
uv run python evals/eda/fetch_repos.py --help

  --per-query N       Repos per GitHub search query (default: 10)
  --max-size-mb N     Skip repos larger than N MB (default: 500)
  --recent-days N     Window for recent commit count (default: 90)
  --csv               Also write CSV alongside parquet
  --out PATH          Output path (default: evals/eda/repos.parquet)
```
