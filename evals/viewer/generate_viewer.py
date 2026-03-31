#!/usr/bin/env python3
"""Generate a self-contained HTML viewer for keystone eval results.

Usage:
    python evals/viewer/generate_viewer.py [--out path/to/viewer.html]
    python evals/viewer/generate_viewer.py --local [--out path/to/viewer.html]
"""

import argparse
import json
import sys
from pathlib import Path

import fsspec
import polars as pl
from tqdm import tqdm

# Ensure the project root is importable.
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from eval_schema import KeystoneRepoResult  # noqa: E402

EVALS_DIR = Path.home() / "keystone_evals"
DEFAULT_S3_PREFIX = "s3://int8-datasets/keystone/evals/"
VIEWER_CACHE_DIR = Path.home() / "keystone_evals" / "viewer_cache"

# Which runs to include and in what order
RUN_NAMES = [
    # "2026-03-02_cat_v1",
    # "2026-03-02_cat_v1_opencode",
    # "2026-03-03_cat_v1_agents_md",
    # "2026-03-02_thad_v2",
    # "2026-03-05_cat_v1",
    # "2026-03-05_cat_v2",
    # "2026-03-10_four_model_thad",
    # "2026-03-08_four_model_thad_v2",
    # "2026-03-11_cat_v8",
    # "2026-03-11_opencode_vs_claude_v2",
    # "2026-03-12_opencode_vs_claude_cost_v2",
    # "2026-03-12_opencode_vs_claude_cost_v3",
    "2026-03-18-cat",
    "2026-03-13_four_model_thad",
    "2026-03-13_five_model_full_v3",
    "2026-03-14_thad_eval",
    "main",
]

RUN_LABELS = {
    "2026-03-02_cat_v1": "Native (baseline)",
    "2026-03-02_cat_v1_opencode": "OpenCode",
    "2026-03-03_cat_v1_agents_md": "AGENTS.md ablation",
    "2026-03-02_thad_v2": "Five-model 2026-03-05",
    "2026-03-05_cat_v1": "Four-model 2026-03-05 (v1)",
    "2026-03-05_cat_v2": "Four-model 2026-03-05 (v2)",
    "2026-03-08_four_model_thad_v2": "Four-model 2026-03-08 (v2)",
    "2026-03-10_four_model_thad": "Four-model 2026-03-10",
    "2026-03-11_cat_v8": "Four-model 2026-03-11 (v8)",
    "2026-03-11_opencode_vs_claude_v2": "OpenCode vs Claude 2026-03-11",
    "2026-03-12_opencode_vs_claude_cost_v2": "OpenCode vs Claude (cost) 2026-03-12",
    "2026-03-12_opencode_vs_claude_cost_v3": "OpenCode vs Claude (cost v3) 2026-03-12",
    "2026-03-13_four_model_thad": "Four-model 2026-03-13",
    "2026-03-13_five_model_full_v3": "Five-model full 2026-03-13",
    "2026-03-14_thad_eval": "Thad eval 2026-03-14",
    "main": "Main",
    "2026-03-18-cat": "Four-model 2026-03-18",
}

# Canonical model display order & colors per run
MODEL_META = {
    "claude-opus": {"label": "claude-opus", "color": "#636EFA"},
    "claude-haiku": {"label": "claude-haiku", "color": "#EF553B"},
    "codex-gpt-5.2": {"label": "codex-gpt-5.2", "color": "#00CC96"},
    "codex-mini-gpt-5.1": {"label": "codex-mini", "color": "#AB63FA"},
    "codex-gpt-5.3": {"label": "codex-gpt-5.3", "color": "#FFA15A"},
    "opencode-opus": {"label": "opencode-opus", "color": "#636EFA"},
    "opencode-haiku": {"label": "opencode-haiku", "color": "#EF553B"},
    "opencode-codex": {"label": "opencode-codex", "color": "#00CC96"},
    "opencode-codex-mini": {"label": "opencode-mini", "color": "#AB63FA"},
    "opencode-claude": {"label": "opencode-claude", "color": "#19D3F3"},
    "gpt-5.4": {"label": "gpt-5.4", "color": "#FF6692"},
    "opus-4.6": {"label": "opus-4.6", "color": "#B6E880"},
    "claude-opus-effort_max": {"label": "claude-opus (max)", "color": "#1F77B4"},
    "claude-opus-effort_medium": {"label": "claude-opus (medium)", "color": "#8DA0CB"},
    "codex-gpt-5.3-reasoning_xhigh": {"label": "codex-5.3 (xhigh)", "color": "#FF7F0E"},
    "codex-gpt-5.3-reasoning_medium": {"label": "codex-5.3 (medium)", "color": "#FFBB78"},
}

INFRA_CATEGORIES = {
    "Sandbox expired",
    "Sandbox container finished",
    "Sandbox container crashed",
    "Sandbox container not found",
    "Infrastructure error",
}


def categorize_error(error: str) -> str:
    """Categorize a failure error message into a named bucket."""
    if not error:
        return "Other"
    e = error.lower()
    if "dockerfile not found" in e:
        return "No files created"
    if "timeout" in e or "timed out" in e or "status timeout" in e:
        return "Agent timeout"
    if "not found" in e and "already shut down" in e:
        return "Sandbox expired"
    if "associated container has finished" in e:
        return "Sandbox container finished"
    if "container id" in e and ("finished" in e or "status=" in e):
        return "Sandbox container crashed"
    if "no container with id" in e:
        return "Sandbox container not found"
    if "build failed" in e:
        return "Docker build failed"
    if "test run failed" in e or ("test" in e and "return code" in e):
        return "Tests failed"
    if (
        "nodename nor servname" in e
        or "file descriptor not found" in e
        or "errno" in e
        or "eof" in e
    ):
        return "Infrastructure error"
    return "Other"


def load_run(
    run_uri: str, run_label: str
) -> tuple[dict[str, dict[str, KeystoneRepoResult]], dict[str, dict]]:
    """Load all results from a run directory (local path or fsspec URI).

    Returns ({model: {repo: KeystoneRepoResult}}, {model: rerun_meta}).
    """
    fs, base_prefix = fsspec.core.url_to_fs(run_uri)
    base_prefix = base_prefix.rstrip("/")

    models: dict[str, dict[str, KeystoneRepoResult]] = {}
    rerun_meta: dict[str, dict] = {}

    # Glob for all eval_result.json files under this run
    json_paths = sorted(str(p) for p in fs.glob(f"{base_prefix}/**/eval_result.json"))
    for path in tqdm(json_paths, desc=f"  {run_label}", unit="file"):
        with fs.open(path) as f:
            raw = json.load(f)
        result = KeystoneRepoResult(**raw)
        config_name = result.eval_config.name if result.eval_config else None
        if not config_name:
            continue
        if config_name not in models:
            models[config_name] = {}
        repo_id = result.repo_entry.id
        existing = models[config_name].get(repo_id)
        # Keep the best trial: prefer success over failure
        if existing is None or (not existing.success and result.success):
            models[config_name][repo_id] = result

    # Load rerun manifests (one per model directory)
    for rp in fs.glob(f"{base_prefix}/*/rerun.json"):
        rerun_path = str(rp)
        try:
            with fs.open(rerun_path) as f:
                data = json.load(f)
            rerun_config_name: str = data.get("name") or rerun_path.rstrip("/").split("/")[-2]
            rerun_meta[rerun_config_name] = {
                "s3_uri": f"s3://{rerun_path}",
                "git_commit": data.get("git_commit", "unknown"),
                "git_is_dirty": data.get("git_is_dirty", False),
            }
        except Exception:
            pass

    return models, rerun_meta


def extract_summary(result: KeystoneRepoResult) -> dict:
    """Extract a flat summary dict from a KeystoneRepoResult."""
    br = result.bootstrap_result
    agent = br.agent if br else None
    cost_info = agent.cost if agent else None
    verification = br.verification if br else None

    summary_msg = ""
    if agent and agent.summary:
        summary_msg = agent.summary.message or ""

    # Use the clean bootstrap error, NOT the giant top-level CLI log dump
    clean_error = (br.error_message if br else None) or ""

    # Agent status messages: short progress strings the agent emitted
    status_messages = [m.message or "" for m in (agent.status_messages if agent else [])]

    # Agent error messages (may be empty even on failure)
    agent_error_msgs = list(agent.error_messages) if agent else []

    return {
        "success": result.success,
        "language": result.repo_entry.language or "",
        "duration_s": round(agent.duration_seconds if agent else 0),
        "cost_usd": round(cost_info.cost_usd if cost_info else 0, 3),
        "tests_passed": verification.tests_passed if verification else None,
        "tests_failed": verification.tests_failed if verification else None,
        "build_seconds": round(verification.image_build_seconds or 0 if verification else 0),
        "test_seconds": round(verification.test_execution_seconds or 0 if verification else 0),
        "summary": summary_msg,
        "error": clean_error,
        "status_messages": status_messages,
        "agent_error_msgs": agent_error_msgs,
        "unexpected_broken_commit_passes": result.unexpected_broken_commit_passes,
        "restoration_check_failed": result.restoration_check_failed,
    }


def save_run_cache(run_name: str, models: dict[str, dict[str, KeystoneRepoResult]]) -> None:
    """Serialize a run's results to a local parquet cache."""
    rows = []
    for config_name, repos in models.items():
        for repo_id, result in repos.items():
            summary = extract_summary(result)
            rows.append(
                {
                    "config_name": config_name,
                    "repo_id": repo_id,
                    **summary,
                    "status_messages": json.dumps(summary["status_messages"]),
                    "agent_error_msgs": json.dumps(summary["agent_error_msgs"]),
                }
            )
    if not rows:
        return
    VIEWER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(VIEWER_CACHE_DIR / f"{run_name}.parquet")


def load_run_cache(run_name: str) -> dict[str, dict[str, dict]] | None:
    """Load cached run data, or None if no cache exists."""
    cache_path = VIEWER_CACHE_DIR / f"{run_name}.parquet"
    if not cache_path.exists():
        return None
    models: dict[str, dict[str, dict]] = {}
    for row in pl.read_parquet(cache_path).to_dicts():
        config_name = row.pop("config_name")
        repo_id = row.pop("repo_id")
        row["status_messages"] = json.loads(row["status_messages"])
        row["agent_error_msgs"] = json.loads(row["agent_error_msgs"])
        models.setdefault(config_name, {})[repo_id] = row
    return models


def build_data(
    run_names: list[str],
    use_s3: bool = True,
    s3_prefix: str = DEFAULT_S3_PREFIX,
    use_cache: bool = True,
) -> dict:
    """Build the full data dict for embedding in HTML."""
    runs: dict[str, dict] = {}
    rerun_meta: dict[str, dict] = {}
    all_repos: set[str] = set()

    for run_name in run_names:
        # Try cache first
        if use_cache:
            cached = load_run_cache(run_name)
            if cached is not None:
                print(f"  Loading {run_name} from cache...")
                for repos in cached.values():
                    all_repos.update(repos.keys())
                runs[run_name] = cached
                continue

        run_uri = f"{s3_prefix.rstrip('/')}/{run_name}" if use_s3 else str(EVALS_DIR / run_name)
        print(f"  Loading {run_name} from {run_uri}...")
        models, run_rerun = load_run(run_uri, run_label=run_name)
        if not models:
            print(f"  [skip] {run_name} — no results found")
            continue
        rerun_meta[run_name] = run_rerun

        # Collect repo names
        for repos in models.values():
            all_repos.update(repos.keys())
        # Summarize and cache
        runs[run_name] = {
            model: {repo: extract_summary(result) for repo, result in repos.items()}
            for model, repos in models.items()
        }
        if use_cache:
            save_run_cache(run_name, models)

    # Sort repos alphabetically
    repo_list = sorted(all_repos)

    return {
        "runs": runs,
        "run_names": [r for r in run_names if r in runs],
        "run_labels": RUN_LABELS,
        "repo_list": repo_list,
        "model_meta": MODEL_META,
        "rerun_meta": rerun_meta,
        "s3_prefix": s3_prefix,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Keystone Eval Viewer</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@31/styles/ag-grid.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@31/styles/ag-theme-alpine.min.css">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f1117; color: #e2e8f0; font-size: 14px;
         display: flex; flex-direction: column; }

  header { padding: 16px 24px; background: #1a1d27; border-bottom: 1px solid #2d3148;
           display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 100;
           flex-wrap: wrap; }
  header h1 { font-size: 18px; font-weight: 600; color: #a78bfa; white-space: nowrap; }

  .run-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
  .run-tab { padding: 6px 14px; border-radius: 6px; cursor: pointer; border: 1px solid #3d4163;
             background: #1e2235; color: #94a3b8; font-size: 13px; transition: all .15s; }
  .run-tab:hover { background: #272b42; color: #e2e8f0; }
  .run-tab.active { background: #312e81; border-color: #6366f1; color: #c7d2fe; }

  .stats-section { background: #14172a; border-bottom: 1px solid #2d3148; }

  .stats-bar { padding: 10px 24px; display: flex; gap: 24px; align-items: center;
               flex-wrap: wrap; cursor: pointer; user-select: none; }
  .stats-bar:hover { background: #191c2e; }
  .stat-chip { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .stat-chip .dot { width: 12px; height: 12px; border-radius: 3px; flex-shrink: 0; }
  .stat-chip .pct { font-weight: 700; font-size: 15px; }
  .stat-chip .label { color: #64748b; }
  .stat-chip .cost { color: #c4b5fd; font-size: 13px; font-weight: 600; margin-left: 2px; }

  .stats-chevron { margin-left: auto; color: #475569; font-size: 13px; flex-shrink: 0; }

  .breakdown-panel { overflow: hidden; max-height: 0; transition: max-height .3s ease; }
  .breakdown-panel.open { max-height: 600px; }
  .breakdown-inner { padding: 12px 24px 16px; border-top: 1px solid #2d3148; }
  .breakdown-title { font-size: 12px; color: #64748b; text-transform: uppercase;
                     letter-spacing: .06em; margin-bottom: 10px; }

  .fail-chart { display: flex; align-items: flex-end; gap: 8px; height: 160px; padding-bottom: 0;
               width: 100%; }
  .fail-chart-col { display: flex; flex-direction: column; align-items: center; gap: 4px; flex: 1; }
  .fail-bar-stack { display: flex; flex-direction: column-reverse; width: 100%;
                    border-radius: 3px 3px 0 0; overflow: hidden; }
  .fail-bar-seg { width: 100%; transition: height .3s; cursor: default; }

  #seg-tooltip { position: fixed; background: #1e2235; border: 1px solid #3d4163;
                 border-radius: 6px; padding: 6px 10px; font-size: 12px; color: #e2e8f0;
                 pointer-events: none; z-index: 9999; display: none; white-space: nowrap; }
  .fail-bar-col-label { font-size: 11px; color: #64748b; text-align: center; white-space: nowrap;
                        overflow: hidden; text-overflow: ellipsis; width: 100%; }
  .fail-bar-count { font-size: 11px; color: #475569; text-align: center; }

  .breakdown-legend { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 12px; }
  .legend-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: #94a3b8; }
  .legend-dot { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }

  .main { padding: 16px 24px; }

  table { width: 100%; border-collapse: collapse; }
  thead th { position: sticky; top: 57px; background: #1a1d27; z-index: 50;
             padding: 8px 10px; text-align: center; font-size: 12px; font-weight: 600;
             color: #94a3b8; border-bottom: 2px solid #2d3148; }
  thead th.repo-col { text-align: left; min-width: 160px; }
  thead th.meta-col { color: #475569; }

  tbody tr { border-bottom: 1px solid #1e2235; cursor: pointer; }
  tbody tr.expanded { background: #1a1d27; }

  td { padding: 7px 10px; vertical-align: middle; }
  td.repo-name { font-weight: 500; color: #c7d2fe; font-size: 13px; }
  td.meta { color: #475569; font-size: 12px; }
  td.result-cell { text-align: center; vertical-align: top; height: 52px; }

  .badge { display: inline-block; width: 28px; height: 28px; border-radius: 6px;
           line-height: 28px; font-size: 13px; font-weight: 700; text-align: center; }
  .badge.pass { background: #14532d; color: #4ade80; }
  .badge.fail { background: #450a0a; color: #f87171; }
  .badge.timeout { background: #422006; color: #fbbf24; }
  .badge.infra { background: #7c3a10; color: #fed7aa; }
  .badge.missing { background: #1e2235; color: #475569; }

  .rc { display: flex; flex-direction: column; align-items: flex-start; justify-content: center;
        height: 100%; padding: 2px 0; line-height: 1; }
  .rc-meta { display: flex; align-items: baseline; gap: 4px; margin-top: 2px; padding-left: 2px; }
  .rc-cost { font-size: 11px; color: #a78bfa; font-weight: 600; }
  .rc-time { font-size: 10px; color: #475569; }

  .rc-expanded { padding: 6px 4px; font-size: 12px; line-height: 1.4;
                 max-height: 220px; overflow-y: auto; }
  .rc-expanded-header { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; flex-wrap: wrap; }
  .rc-tests { font-size: 11px; color: #64748b; }
  .rc-err { color: #fca5a5; font-size: 11px; margin-top: 4px; word-break: break-word; }
  .rc-summary { color: #94a3b8; font-size: 11px; margin-top: 4px; word-break: break-word; }
  .rc-steps { margin-top: 4px; border-top: 1px solid #2d3148; padding-top: 4px; }
  .rc-step { font-size: 11px; color: #64748b; line-height: 1.3; }
  .rc-step:last-child { color: #94a3b8; }

  .cell-meta { display: block; font-size: 11px; color: #475569; margin-top: 1px;
               line-height: 1.2; white-space: nowrap; min-height: 13px; }
  .cell-meta .cm-cost { color: #a78bfa; font-weight: 600; }
  .cell-meta .cm-time { color: #475569; font-size: 10px; }

  .detail-row td { padding: 0; }
  .detail-panel { padding: 16px 20px 20px; background: #12151f;
                  border-top: 1px solid #2d3148; }
  .detail-panel h3 { color: #a78bfa; font-size: 13px; margin-bottom: 12px; font-weight: 600; }
  .model-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
  .model-card { background: #1a1d27; border-radius: 8px; border: 1px solid #2d3148;
                padding: 12px; overflow: hidden; }
  .model-card.pass-card { border-left: 3px solid #22c55e; }
  .model-card.fail-card { border-left: 3px solid #ef4444; }
  .model-card.timeout-card { border-left: 3px solid #fbbf24; }
  .model-card.infra-card { border-left: 3px solid #fb923c; }
  .model-card .card-header { display: flex; justify-content: space-between; align-items: center;
                              margin-bottom: 8px; }
  .model-card .card-name { font-weight: 600; font-size: 13px; }
  .model-card .card-status { font-size: 12px; font-weight: 700;
                              padding: 2px 8px; border-radius: 4px; }
  .model-card .card-status.pass { background: #14532d; color: #4ade80; }
  .model-card .card-status.fail { background: #450a0a; color: #f87171; }
  .model-card .card-status.timeout { background: #422006; color: #fbbf24; }
  .model-card .card-status.infra { background: #431407; color: #fdba74; }
  .meta-row { display: flex; gap: 16px; font-size: 12px; color: #64748b; margin-bottom: 8px;
              flex-wrap: wrap; }
  .meta-row span { display: flex; gap: 4px; align-items: center; }
  .meta-row span b { color: #94a3b8; }
  .summary-text { font-size: 12px; color: #94a3b8; line-height: 1.5;
                  border-top: 1px solid #2d3148; padding-top: 8px; margin-top: 4px;
                  word-break: break-word; }
  .error-text { font-size: 12px; color: #fca5a5; line-height: 1.5; font-weight: 600;
                border-top: 1px solid #2d3148; padding-top: 8px; margin-top: 4px;
                word-break: break-word; }
  .status-trail { border-top: 1px solid #2d3148; padding-top: 8px; margin-top: 4px; }
  .status-trail .trail-label { font-size: 11px; color: #475569; text-transform: uppercase;
                                letter-spacing: .06em; margin-bottom: 5px; }
  .status-step { display: flex; gap: 6px; align-items: flex-start; margin-bottom: 3px;
                 font-size: 12px; color: #64748b; line-height: 1.4; }
  .status-step::before { content: ">"; color: #475569; flex-shrink: 0; margin-top: 0px; }
  .status-step.last-step { color: #94a3b8; }
  .agent-err { font-size: 12px; color: #fb923c; margin-top: 4px; word-break: break-word; }

  .chevron { display: inline-block; transition: transform .2s; margin-left: 6px;
             color: #475569; font-size: 11px; }
  .expanded .chevron { transform: rotate(90deg); }

  .rerun-btn { background: none; border: 1px solid #3d4163; border-radius: 4px;
               color: #64748b; cursor: pointer; font-size: 13px; padding: 1px 5px;
               line-height: 1; transition: all .15s; }
  .rerun-btn:hover { border-color: #6366f1; color: #a78bfa; background: #1e2235; }

  .cell-actions { display: inline-flex; gap: 2px; margin-left: 4px; vertical-align: middle;
                  opacity: 0; transition: opacity .15s; }
  td.result-cell:hover .cell-actions { opacity: 1; }
  .cell-btn { background: none; border: 1px solid transparent; border-radius: 3px;
              color: #475569; cursor: pointer; font-size: 11px; padding: 1px 3px;
              line-height: 1; transition: all .15s; }
  .cell-btn:hover { border-color: #6366f1; color: #a78bfa; background: #1e2235; }

  .confirm-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6);
                     z-index: 1000; display: flex; align-items: center; justify-content: center; }
  .confirm-overlay.hidden { display: none; }
  .confirm-box { background: #1a1d27; border: 1px solid #3d4163; border-radius: 10px;
                 padding: 20px 24px; max-width: 480px; width: 90%; }
  .confirm-box h2 { font-size: 15px; color: #fbbf24; margin-bottom: 8px; }
  .confirm-box p { font-size: 13px; color: #94a3b8; margin-bottom: 16px; line-height: 1.5; }
  .confirm-box .rerun-cmd { margin-bottom: 12px; }
  .confirm-actions { display: flex; gap: 8px; justify-content: flex-end; }

  .rerun-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6);
                   z-index: 1000; display: flex; align-items: center; justify-content: center; }
  .rerun-overlay.hidden { display: none; }
  .rerun-box { background: #1a1d27; border: 1px solid #3d4163; border-radius: 10px;
               padding: 20px 24px; max-width: 600px; width: 90%; }
  .rerun-box h2 { font-size: 15px; color: #a78bfa; margin-bottom: 12px; }
  .rerun-git { font-size: 12px; color: #64748b; margin-bottom: 12px; font-family: monospace; }
  .rerun-git .dirty { color: #fb923c; }
  .rerun-cmd-label { font-size: 12px; color: #94a3b8; margin-bottom: 6px; }
  .rerun-cmd { background: #0f1117; border: 1px solid #2d3148; border-radius: 6px;
               padding: 10px 12px; font-family: monospace; font-size: 12px; color: #c7d2fe;
               word-break: break-all; white-space: pre-wrap; margin-bottom: 12px; }
  .rerun-actions { display: flex; gap: 8px; justify-content: flex-end; }
  .btn { padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px;
         border: 1px solid #3d4163; background: #1e2235; color: #94a3b8; transition: all .15s; }
  .btn:hover { background: #272b42; color: #e2e8f0; }
  .btn.primary { background: #312e81; border-color: #6366f1; color: #c7d2fe; }
  .btn.primary:hover { background: #3730a3; }

  /* AG Grid */
  #myGrid { width: 100%; }
  #detailPanel { flex-shrink: 0; }
  .ag-theme-alpine-dark {
    --ag-background-color: #0f1117;
    --ag-header-background-color: #1a1d27;
    --ag-odd-row-background-color: #0f1117;
    --ag-even-row-background-color: #0f1117;
    --ag-row-hover-color: transparent;
    --ag-selected-row-background-color: #1a1d27;
    --ag-border-color: #2d3148;
    --ag-header-foreground-color: #94a3b8;
    --ag-foreground-color: #e2e8f0;
    --ag-secondary-foreground-color: #64748b;
    --ag-font-size: 13px;
    --ag-font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --ag-row-border-color: #1e2235;
    --ag-input-focus-border-color: #6366f1;
    --ag-checkbox-checked-color: #6366f1;
    --ag-side-bar-panel-width: 0px;
  }
  .ag-theme-alpine-dark .ag-header { border-bottom: 2px solid #2d3148; }
  .ag-theme-alpine-dark .ag-row { cursor: pointer; }
  .ag-theme-alpine-dark .ag-cell { border-right-color: transparent; overflow: visible; white-space: normal !important; }
  .ag-theme-alpine-dark .ag-header-cell-text { font-weight: 600; }
  .ag-theme-alpine-dark .ag-paging-panel,
  .ag-theme-alpine-dark .ag-status-bar,
  .ag-theme-alpine-dark .ag-sticky-bottom { display: none !important; }

  .detail-section { padding: 0 24px 24px; border-top: 1px solid #2d3148; background: #0f1117; }

  .sort-mode-btn { display: inline-block; font-size: 10px; padding: 1px 5px;
                   border: 1px solid #3d4163; border-radius: 3px; cursor: pointer;
                   color: #6366f1; background: transparent; transition: all .15s;
                   margin-left: 4px; vertical-align: middle; user-select: none; }
  .sort-mode-btn:hover { border-color: #6366f1; background: #272b42; color: #a78bfa; }
</style>
</head>
<body>
<div id="seg-tooltip"></div>

<div class="rerun-overlay hidden" id="rerunOverlay" onclick="closeRerun(event)">
  <div class="rerun-box" onclick="event.stopPropagation()">
    <h2>Rerun <span id="rerunModelName"></span></h2>
    <div class="rerun-git" id="rerunGitInfo"></div>
    <div class="rerun-cmd-label">Run this command from the repo root:</div>
    <pre class="rerun-cmd" id="rerunCmd"></pre>
    <div class="rerun-actions">
      <button class="btn" onclick="closeRerun()">Close</button>
      <button class="btn primary" id="rerunCopyBtn" onclick="copyRerunCmd()">Copy command</button>
    </div>
  </div>
</div>

<div class="confirm-overlay hidden" id="confirmOverlay" onclick="closeConfirm(event)">
  <div class="confirm-box" onclick="event.stopPropagation()">
    <h2 id="confirmTitle">Are you sure?</h2>
    <p id="confirmMsg"></p>
    <div class="rerun-cmd-label">Command:</div>
    <pre class="rerun-cmd" id="confirmCmd"></pre>
    <div class="confirm-actions">
      <button class="btn" onclick="closeConfirm()">Cancel</button>
      <button class="btn primary" id="confirmCopyBtn" onclick="copyConfirmCmd()">Copy &amp; close</button>
    </div>
  </div>
</div>

<header>
  <h1>Keystone Eval Viewer</h1>
  <div class="run-tabs" id="runTabs"></div>
</header>

<div class="stats-section">
  <div class="stats-bar" id="statsBar" onclick="toggleBreakdown()"></div>
  <div class="breakdown-panel" id="breakdownPanel">
    <div class="breakdown-inner" id="breakdownInner"></div>
  </div>
</div>

<div class="main">
  <div id="myGrid" class="ag-theme-alpine-dark"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/ag-grid-community@31/dist/ag-grid-community.min.js"></script>
<script>
const DATA = __DATA__;

let currentRun = DATA.run_names[0];
let expandedRepo = null;
let breakdownOpen = false;
let gridApi = null;
const columnSortModes = {}; // field -> "status"|"cost"|"time"

const CATEGORY_COLORS = {
  "No files created":           "#d62728",
  "Docker build failed":        "#ff7f0e",
  "Tests failed":               "#9467bd",
  "Agent timeout":              "#e377c2",
  "Sandbox expired":            "#8c564b",
  "Sandbox container finished": "#bcbd22",
  "Sandbox container crashed":  "#a65628",
  "Sandbox container not found":"#f4a582",
  "Infrastructure error":       "#7f7f7f",
  "Unknown":                    "#c7c7c7",
  "Other":                      "#17becf",
};

const INFRA_CATEGORIES = new Set([
  "Sandbox expired",
  "Sandbox container finished",
  "Sandbox container crashed",
  "Sandbox container not found",
  "Infrastructure error",
]);

const CATEGORY_ORDER = [
  "No files created",
  "Docker build failed",
  "Tests failed",
  "Agent timeout",
  "Sandbox expired",
  "Sandbox container finished",
  "Sandbox container crashed",
  "Sandbox container not found",
  "Infrastructure error",
  "Other",
  "Unknown",
];

function categorizeError(errorMsg) {
  if (!errorMsg) return "Other";
  const e = errorMsg.toLowerCase();
  if (e.includes("dockerfile not found")) return "No files created";
  if (e.includes("timeout") || e.includes("timed out") || e.includes("status timeout")) return "Agent timeout";
  if (e.includes("not found") && e.includes("already shut down")) return "Sandbox expired";
  if (e.includes("associated container has finished")) return "Sandbox container finished";
  if (e.includes("container id") && (e.includes("finished") || e.includes("status="))) return "Sandbox container crashed";
  if (e.includes("no container with id")) return "Sandbox container not found";
  if (e.includes("build failed")) return "Docker build failed";
  if (e.includes("test run failed") || (e.includes("test") && e.includes("return code"))) return "Tests failed";
  if (e.includes("nodename nor servname") || e.includes("file descriptor not found") || e.includes("errno") || e.includes("eof")) return "Infrastructure error";
  return "Other";
}

function fmtDuration(s) {
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return sec ? m + "m" + sec + "s" : m + "m";
}

function modelOrder(run) {
  const models = Object.keys(DATA.runs[run] || {});
  const order = ["claude-opus","claude-haiku","codex-gpt-5.2","codex-mini-gpt-5.1","codex-gpt-5.3",
                 "opencode-opus","opencode-haiku","opencode-codex","opencode-codex-mini"];
  return models.sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

function getModelMeta(model) {
  return DATA.model_meta[model] || { label: model, color: "#94a3b8" };
}

function renderTabs() {
  const tabs = document.getElementById("runTabs");
  tabs.innerHTML = DATA.run_names.map(r => `
    <div class="run-tab ${r === currentRun ? 'active' : ''}" onclick="selectRun('${r}')">
      ${DATA.run_labels[r] || r}
    </div>
  `).join("");
}

function renderStats() {
  const bar = document.getElementById("statsBar");
  const runData = DATA.runs[currentRun] || {};
  const models = modelOrder(currentRun);
  const chips = models.map(model => {
    const repos = runData[model] || {};
    const total = Object.keys(repos).length;
    const passed = Object.values(repos).filter(r => r.success).length;
    const pct = total ? Math.round(100 * passed / total) : 0;
    const totalCost = Object.values(repos).reduce((sum, r) => sum + (r.cost_usd || 0), 0);
    const costStr = "$" + totalCost.toFixed(2);
    const meta = getModelMeta(model);
    const hasRerun = !!((DATA.rerun_meta[currentRun] || {})[model]);
    const rerunBtn = hasRerun
      ? `<button class="rerun-btn" title="Rerun this config"
           onclick="showRerun(event,'${currentRun}','${model}')">&#x21ba;</button>`
      : "";
    return `<div class="stat-chip">
      <div class="dot" style="background:${meta.color}"></div>
      <span class="pct" style="color:${meta.color}">${pct}%</span>
      <span class="label">${meta.label} (${passed}/${total})</span>
      <span class="cost">· ${costStr}</span>
      ${rerunBtn}
    </div>`;
  }).join("");
  bar.innerHTML = chips + `<span class="stats-chevron" id="statsChevron">${breakdownOpen ? "▲ breakdown" : "▼ breakdown"}</span>`;
}

function toggleBreakdown() {
  breakdownOpen = !breakdownOpen;
  const panel = document.getElementById("breakdownPanel");
  const chevron = document.getElementById("statsChevron");
  if (breakdownOpen) {
    panel.classList.add("open");
    if (chevron) chevron.classList.add("open");
    renderBreakdown();
  } else {
    panel.classList.remove("open");
    if (chevron) chevron.classList.remove("open");
  }
}

function renderBreakdown() {
  const runData = DATA.runs[currentRun] || {};
  const models = modelOrder(currentRun);
  const inner = document.getElementById("breakdownInner");

  // Compute failure category counts per model
  const modelCounts = {};
  for (const model of models) {
    const repos = runData[model] || {};
    const counts = {};
    for (const r of Object.values(repos)) {
      if (!r.success) {
        const cat = categorizeError(r.error);
        counts[cat] = (counts[cat] || 0) + 1;
      }
    }
    modelCounts[model] = counts;
  }

  // Collect all categories present
  const presentCats = new Set();
  for (const counts of Object.values(modelCounts)) {
    for (const cat of Object.keys(counts)) presentCats.add(cat);
  }
  const cats = CATEGORY_ORDER.filter(c => presentCats.has(c));

  // Find max failure count for proportional heights
  const maxFail = Math.max(...models.map(m => {
    const repos = runData[m] || {};
    return Object.values(repos).filter(r => !r.success).length;
  }), 1);
  const MAX_BAR_PX = 120;

  // Build bar columns
  const rows = models.map(model => {
    const repos = runData[model] || {};
    const total = Object.keys(repos).length;
    const failCount = Object.values(repos).filter(r => !r.success).length;
    const counts = modelCounts[model];
    const meta = getModelMeta(model);
    const barHeightPx = Math.round((failCount / maxFail) * MAX_BAR_PX);

    const segs = cats.map(cat => {
      const n = counts[cat] || 0;
      if (!n || !total) return "";
      const pct = (n / failCount) * 100;
      const color = CATEGORY_COLORS[cat] || "#c7c7c7";
      return `<div class="fail-bar-seg" style="height:${pct.toFixed(1)}%;background:${color}"
               onmouseenter="showSegTip(event,'${cat}: ${n}')" onmouseleave="hideSegTip()"></div>`;
    }).join("");

    return `<div class="fail-chart-col">
      <div class="fail-bar-count">${failCount}/${total}</div>
      <div class="fail-bar-stack" style="height:${barHeightPx}px">${segs}</div>
      <div class="fail-bar-col-label" style="color:${meta.color}" title="${meta.label}">${meta.label}</div>
    </div>`;
  }).join("");

  // Legend
  const legendItems = cats.map(cat => {
    const color = CATEGORY_COLORS[cat] || "#c7c7c7";
    return `<div class="legend-item">
      <div class="legend-dot" style="background:${color}"></div>
      <span>${cat}</span>
    </div>`;
  }).join("");

  // Cheating summary: repos with unexpected broken commit passes
  let cheatHtml = "";
  const cheatRepos = [];
  for (const model of models) {
    const repos = runData[model] || {};
    for (const [repoId, r] of Object.entries(repos)) {
      if (r.unexpected_broken_commit_passes > 0) {
        cheatRepos.push({repo: repoId, model: getModelMeta(model).label, count: r.unexpected_broken_commit_passes});
      }
    }
  }
  if (cheatRepos.length > 0) {
    const cheatRows = cheatRepos.map(c =>
      `<div style="display:flex;gap:12px;padding:4px 0;border-bottom:1px solid #2d3148">
        <span style="color:#fbbf24">⚠️</span>
        <span style="color:#e2e8f0;min-width:160px">${escHtml(c.repo)}</span>
        <span style="color:#94a3b8">${escHtml(c.model)}</span>
        <span style="color:#fbbf24">${c.count} unexpected pass(es)</span>
      </div>`
    ).join("");
    cheatHtml = `
      <div style="margin-top:16px;padding-top:12px;border-top:1px solid #2d3148">
        <div class="breakdown-title">Cheating Summary</div>
        ${cheatRows}
      </div>`;
  }

  inner.innerHTML = `
    <div class="breakdown-title">Failure categories by model</div>
    <div class="fail-chart">${rows}</div>
    <div class="breakdown-legend">${legendItems}</div>
    ${cheatHtml}
  `;
}

function buildRowData() {
  const runData = DATA.runs[currentRun] || {};
  const models = modelOrder(currentRun);
  const rows = [];
  DATA.repo_list.forEach(repo => {
    let lang = "";
    for (const m of models) {
      const r = (runData[m] || {})[repo];
      if (r) { lang = r.language || ""; break; }
    }
    const row = { repo, lang };
    for (const m of models) {
      row[m] = (runData[m] || {})[repo] || null;
    }
    rows.push(row);
  });
  return rows;
}

class SortModeHeader {
  init(params) {
    this.params = params;
    this.field = params.column.getColId();
    this.el = document.createElement("div");
    this.el.style.cssText = "display:flex;align-items:center;gap:4px;width:100%;cursor:pointer";
    this.el.onclick = () => { this.params.progressSort(); };

    const label = document.createElement("span");
    label.textContent = params.displayName;
    label.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap";

    this.btn = document.createElement("span");
    this.btn.className = "sort-mode-btn";
    this.btn.title = "Click to cycle: status / cost / time";
    this.updateLabel();
    this.btn.onclick = (e) => {
      e.stopPropagation();
      const modes = ["status", "cost", "time"];
      const cur = columnSortModes[this.field] || "status";
      columnSortModes[this.field] = modes[(modes.indexOf(cur) + 1) % modes.length];
      this.updateLabel();
      // Re-apply sort
      const sortModel = gridApi.getSortModel ? gridApi.getSortModel() : [];
      if (gridApi) {
        gridApi.setColumnDefs(buildColDefs());
        gridApi.setRowData(buildRowData());
      }
    };

    this.el.appendChild(label);
    this.el.appendChild(this.btn);

    // Sort indicator
    this.sortIcon = document.createElement("span");
    this.sortIcon.style.cssText = "font-size:10px;color:#475569;margin-left:2px";
    this.el.appendChild(this.sortIcon);
    this.onSortChanged();
    params.column.addEventListener("sortChanged", () => this.onSortChanged());
  }
  updateLabel() {
    const mode = columnSortModes[this.field] || "status";
    this.btn.textContent = mode === "status" ? "\\u2713/\\u2717" : mode === "cost" ? "$" : "\\u23f1";
  }
  onSortChanged() {
    const sort = this.params.column.getSort();
    this.sortIcon.textContent = sort === "asc" ? "\\u25b2" : sort === "desc" ? "\\u25bc" : "";
  }
  getGui() { return this.el; }
  destroy() {}
}

function buildColDefs() {
  const models = modelOrder(currentRun);
  return [
    {
      field: "repo",
      headerName: "Repo",
      headerTooltip: "Repository name",
      pinned: "left",
      minWidth: 180,
      flex: 1,
      filter: "agTextColumnFilter",
      suppressMovable: true,
      cellRenderer: params => {
        if (params.data._isDetail) return "";
        const chevron = expandedRepo === params.value
          ? `<span style="color:#6366f1;margin-right:4px">&#9660;</span>`
          : `<span style="color:#475569;margin-right:4px">&#9658;</span>`;
        return chevron + escHtml(params.value);
      },
      cellStyle: { fontWeight: "500", color: "#c7d2fe", cursor: "pointer" },
    },
    {
      field: "lang",
      headerName: "Lang",
      headerTooltip: "Language",
      minWidth: 60,
      flex: 0.5,
      filter: "agTextColumnFilter",
      cellStyle: { color: "#475569", fontSize: "12px" },
    },
    ...models.map(m => {
      const meta = getModelMeta(m);
      if (!columnSortModes[m]) columnSortModes[m] = "status";
      return {
        field: m,
        headerName: meta.label,
        headerComponent: SortModeHeader,
        headerTooltip: meta.label,
        headerStyle: { color: meta.color },
        minWidth: 120,
        flex: 1,
        sortable: true,
        filter: false,
        tooltipValueGetter: params => {
          const r = params.value;
          if (!r) return "No data";
          const parts = [r.success ? "PASS" : "FAIL"];
          if (r.cost_usd) parts.push("Cost: $" + r.cost_usd.toFixed(2));
          if (r.duration_s) parts.push("Time: " + fmtDuration(r.duration_s));
          if (r.error) parts.push("Error: " + r.error.slice(0, 120));
          if (r.summary) parts.push("Summary: " + r.summary.slice(0, 120));
          return parts.join("\\n");
        },
        cellRenderer: params => {
          const r = params.value;
          const expanded = expandedRepo === params.data.repo;
          if (!r) return `<div class="rc"><span class="badge missing">&#8212;</span></div>`;
          const durStr = r.duration_s ? fmtDuration(r.duration_s) : "";
          const costStr = r.cost_usd ? "$" + r.cost_usd.toFixed(2) : "";
          const cat = categorizeError(r.error || "");
          let badge;
          if (r.success) badge = `<span class="badge pass">&#10003;</span>`;
          else if (cat === "Agent timeout") badge = `<span class="badge timeout" title="Agent timeout">&#9201;</span>`;
          else if (INFRA_CATEGORIES.has(cat)) badge = `<span class="badge infra" title="${escHtml(cat)}">?</span>`;
          else badge = `<span class="badge fail">&#10007;</span>`;
          // Mutation integrity warning
          if (r.unexpected_broken_commit_passes > 0) {
            badge += `<span class="badge" style="background:#78350f;color:#fbbf24;margin-left:2px" title="${r.unexpected_broken_commit_passes} broken commit(s) unexpectedly passed">&#9888;</span>`;
          }
          if (!expanded) {
            return `<div class="rc">${badge}<div class="rc-meta"><span class="rc-cost">${costStr}</span><span class="rc-time">${durStr}</span></div></div>`;
          }
          // Expanded: show full detail in-cell
          const tests = (r.tests_passed != null) ? `${r.tests_passed}\\u2713 ${r.tests_failed||0}\\u2717` : "";
          const errHtml = r.error ? `<div class="rc-err">\\u2717 ${escHtml(r.error.slice(0,200))}</div>` : "";
          const summaryHtml = r.summary ? `<div class="rc-summary">${escHtml(r.summary.slice(0,300))}</div>` : "";
          const steps = r.status_messages || [];
          const lastSteps = steps.slice(-3).map(s => `<div class="rc-step">${escHtml(s)}</div>`).join("");
          const stepsHtml = lastSteps ? `<div class="rc-steps"><div style="font-size:10px;color:#475569;text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Agent steps</div>${lastSteps}</div>` : "";
          return `<div class="rc-expanded">
            <div class="rc-expanded-header">${badge}<span class="rc-cost">${costStr}</span><span class="rc-time">${durStr}</span>${tests ? `<span class="rc-tests">${tests}</span>` : ""}</div>
            ${errHtml}${stepsHtml}${summaryHtml}
          </div>`;
        },
        autoHeight: true,
        comparator: (a, b) => {
          const mode = columnSortModes[m] || "status";
          if (mode === "cost") {
            const ac = a ? (a.cost_usd || 0) : 0;
            const bc = b ? (b.cost_usd || 0) : 0;
            return ac - bc;
          }
          if (mode === "time") {
            const at = a ? (a.duration_s || 0) : 0;
            const bt = b ? (b.duration_s || 0) : 0;
            return at - bt;
          }
          const av = a ? (a.success ? 2 : 1) : 0;
          const bv = b ? (b.success ? 2 : 1) : 0;
          return av - bv;
        },
      };
    }),
  ];
}

function initGrid() {
  const container = document.getElementById("myGrid");
  if (gridApi) { gridApi.destroy(); gridApi = null; }
  const options = {
    columnDefs: buildColDefs(),
    rowData: buildRowData(),
    headerHeight: 40,
    domLayout: 'autoHeight',
    defaultColDef: { sortable: true, resizable: true },
    suppressFieldDotNotation: true,
    suppressCellFocus: true,
    animateRows: false,
    suppressStatusBar: true,
    suppressPaginationPanel: true,
    tooltipShowDelay: 300,
    tooltipMouseTrack: true,
    getRowHeight: params => expandedRepo === params.data.repo ? null : 52,
    onRowClicked: handleRowClick,
  };
  new agGrid.Grid(container, options);
  gridApi = options.api;
}

function handleRowClick(params) {
  const repo = params.data.repo;
  if (expandedRepo === repo) {
    expandedRepo = null;
  } else {
    expandedRepo = repo;
  }
  gridApi.resetRowHeights();
  gridApi.refreshCells({ force: true });
}

function renderDetailPanelInto(panel, repo, models, runData) {
  const cards = models.map(model => {
    const r = (runData[model] || {})[repo];
    if (!r) return `<div class="model-card"><div class="card-header">
      <span class="card-name" style="color:${getModelMeta(model).color}">${getModelMeta(model).label}</span>
      <span class="card-status fail">No data</span></div></div>`;

    const meta = getModelMeta(model);
    const errCat = categorizeError(r.error || "");
    const cls = r.success ? "pass" : (errCat === "Agent timeout" ? "timeout" : (INFRA_CATEGORIES.has(errCat) ? "infra" : "fail"));
    const dur = r.duration_s ? `${r.duration_s}s` : "—";
    const cost = r.cost_usd ? `$${r.cost_usd}` : "—";
    const tests = (r.tests_passed != null)
      ? `${r.tests_passed}✓ ${r.tests_failed || 0}✗`
      : "—";
    const build = r.build_seconds ? `${r.build_seconds}s build` : "";
    const testTime = r.test_seconds ? `${r.test_seconds}s` : "";

    const summaryHtml = r.summary
      ? `<div class="summary-text">${escHtml(r.summary)}</div>`
      : "";

    // Status trail (last 6 steps)
    const steps = r.status_messages || [];
    const stepsHtml = steps.length ? (() => {
      const show = steps.slice(-6);
      const skipped = steps.length - show.length;
      const rows = show.map((s, i) =>
        `<div class="status-step${i === show.length - 1 ? ' last-step' : ''}">${escHtml(s)}</div>`
      ).join("");
      const prefix = skipped ? `<div class="status-step" style="color:#374151">(+${skipped} earlier steps…)</div>` : "";
      return `<div class="status-trail"><div class="trail-label">Agent steps</div>${prefix}${rows}</div>`;
    })() : "";

    // Clean error (not the log dump)
    const errorHtml = r.error
      ? `<div class="error-text">✗ ${escHtml(r.error)}</div>`
      : "";

    // Agent-level error messages (if any)
    const agentErrs = r.agent_error_msgs || [];
    const agentErrHtml = agentErrs.length
      ? agentErrs.map(e => `<div class="agent-err">⚠ ${escHtml(e.slice(0, 300))}</div>`).join("")
      : "";

    return `<div class="model-card ${cls}-card">
      <div class="card-header">
        <span class="card-name" style="color:${meta.color}">${meta.label}</span>
        <span class="card-status ${cls}">${r.success ? "PASS" : (cls === "timeout" ? "TIMEOUT" : "FAIL")}</span>
      </div>
      <div class="meta-row">
        <span><b>time:</b> ${dur}</span>
        <span><b>cost:</b> ${cost}</span>
        <span><b>tests:</b> ${tests}</span>
        ${build ? `<span><b>build:</b> ${build}</span>` : ""}
        ${testTime ? `<span><b>test time:</b> ${testTime}</span>` : ""}
      </div>
      ${errorHtml}${stepsHtml}${summaryHtml}${agentErrHtml}
    </div>`;
  }).join("");

  panel.innerHTML = `<h3 style="color:#a78bfa;font-size:13px;margin-bottom:12px;font-weight:600;padding-top:16px">${escHtml(repo)}</h3><div class="model-cards">${cards}</div>`;
}

function selectRun(run) {
  currentRun = run;
  expandedRepo = null;
  renderTabs();
  renderStats();
  if (gridApi) {
    gridApi.setColumnDefs(buildColDefs());
    gridApi.setRowData(buildRowData());
    gridApi.sizeColumnsToFit();
  }
  if (breakdownOpen) renderBreakdown();
}

function showSegTip(e, text) {
  const t = document.getElementById("seg-tooltip");
  t.textContent = text;
  t.style.display = "block";
  positionSegTip(e);
}
function hideSegTip() {
  document.getElementById("seg-tooltip").style.display = "none";
}
function positionSegTip(e) {
  const t = document.getElementById("seg-tooltip");
  t.style.left = (e.clientX + 12) + "px";
  t.style.top = (e.clientY - 28) + "px";
}
document.addEventListener("mousemove", e => {
  if (document.getElementById("seg-tooltip").style.display !== "none") positionSegTip(e);
});

function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
          .replace(/"/g,"&quot;").replace(/\\n/g,"<br>");
}

function showRerun(e, run, model) {
  e.stopPropagation();
  const meta = (DATA.rerun_meta[run] || {})[model];
  if (!meta) return;
  const displayModel = getModelMeta(model).label;
  document.getElementById("rerunModelName").textContent = displayModel;
  const commit = meta.git_commit.length > 12 ? meta.git_commit.slice(0, 12) : meta.git_commit;
  const dirtyHtml = meta.git_is_dirty
    ? ' <span class="dirty">&#9888; uncommitted changes at run time</span>'
    : "";
  document.getElementById("rerunGitInfo").innerHTML = "git: " + commit + dirtyHtml;
  const cmd = "uv run python evals/eval_cli.py --config_file " + meta.s3_uri;
  document.getElementById("rerunCmd").textContent = cmd;
  document.getElementById("rerunCopyBtn").textContent = "Copy command";
  document.getElementById("rerunOverlay").classList.remove("hidden");
}

function closeRerun(e) {
  if (e && e.target !== document.getElementById("rerunOverlay")) return;
  document.getElementById("rerunOverlay").classList.add("hidden");
}

function copyRerunCmd() {
  const cmd = document.getElementById("rerunCmd").textContent;
  navigator.clipboard.writeText(cmd).then(() => {
    const btn = document.getElementById("rerunCopyBtn");
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Copy command"; }, 1500);
  });
}

function getS3Path(run, model, repo) {
  const prefix = (DATA.s3_prefix || "s3://int8-datasets/keystone/evals/").replace(/\\/+$/, "");
  return prefix + "/" + run + "/" + model + "/" + repo + "/trial_0/";
}

function getRerunCmd(run, model) {
  const meta = (DATA.rerun_meta[run] || {})[model];
  if (meta && meta.s3_uri) {
    return "uv run python evals/eval_cli.py --config_file " + meta.s3_uri;
  }
  // Fallback: point at the rerun.json path
  const prefix = (DATA.s3_prefix || "s3://int8-datasets/keystone/evals/").replace(/\\/+$/, "");
  return "uv run python evals/eval_cli.py --config_file " + prefix + "/" + run + "/" + model + "/rerun.json";
}

function copyS3Path(e, run, model, repo) {
  e.stopPropagation();
  const path = getS3Path(run, model, repo);
  navigator.clipboard.writeText(path).then(() => {
    showToast("Copied: " + path);
  });
}

function rerunRepo(e, run, model, repo, hasData) {
  e.stopPropagation();
  const cmd = getRerunCmd(run, model);
  if (hasData) {
    // Show confirmation overlay
    document.getElementById("confirmTitle").textContent = "Rerun " + getModelMeta(model).label + " / " + repo + "?";
    document.getElementById("confirmMsg").innerHTML =
      "There is <b>preexisting data</b> for this test. Running again will overwrite the existing eval output.";
    document.getElementById("confirmCmd").textContent = cmd;
    document.getElementById("confirmCopyBtn").textContent = "Copy command";
    document.getElementById("confirmOverlay").classList.remove("hidden");
  } else {
    // No existing data — just copy directly
    navigator.clipboard.writeText(cmd).then(() => {
      showToast("Copied rerun command");
    });
  }
}

function closeConfirm(e) {
  if (e && e.target && e.target !== document.getElementById("confirmOverlay")) return;
  document.getElementById("confirmOverlay").classList.add("hidden");
}

function copyConfirmCmd() {
  const cmd = document.getElementById("confirmCmd").textContent;
  navigator.clipboard.writeText(cmd).then(() => {
    const btn = document.getElementById("confirmCopyBtn");
    btn.textContent = "Copied!";
    setTimeout(() => {
      closeConfirm();
      btn.textContent = "Copy command";
    }, 800);
  });
}

function showToast(msg) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);" +
      "background:#312e81;color:#c7d2fe;padding:8px 16px;border-radius:6px;font-size:13px;" +
      "z-index:9999;opacity:0;transition:opacity .3s;pointer-events:none;";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = "1";
  setTimeout(() => { t.style.opacity = "0"; }, 1800);
}

// Right-click context menu to reset columns
document.getElementById("myGrid").addEventListener("contextmenu", function(e) {
  e.preventDefault();
  const existing = document.getElementById("gridContextMenu");
  if (existing) existing.remove();
  const menu = document.createElement("div");
  menu.id = "gridContextMenu";
  menu.style.cssText = "position:fixed;z-index:9999;background:#1a1d27;border:1px solid #3d4163;" +
    "border-radius:6px;padding:4px 0;box-shadow:0 4px 12px rgba(0,0,0,.4);";
  menu.style.left = e.clientX + "px";
  menu.style.top = e.clientY + "px";
  const item = document.createElement("div");
  item.textContent = "Reset columns";
  item.style.cssText = "padding:6px 16px;cursor:pointer;font-size:13px;color:#94a3b8;";
  item.onmouseenter = () => { item.style.background = "#272b42"; item.style.color = "#e2e8f0"; };
  item.onmouseleave = () => { item.style.background = "none"; item.style.color = "#94a3b8"; };
  item.onclick = () => {
    menu.remove();
    if (gridApi) {
      const colDefs = buildColDefs();
      gridApi.setColumnDefs(colDefs);
      // Make all columns visible
      const allColIds = gridApi.getColumnDefs().map(c => c.field);
      gridApi.columnModel.setColumnsVisible(allColIds, true);
      gridApi.sizeColumnsToFit();
    }
  };
  menu.appendChild(item);
  document.body.appendChild(menu);
  const dismiss = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("click", dismiss); } };
  setTimeout(() => document.addEventListener("click", dismiss), 0);
});

// Init
renderTabs();
renderStats();
initGrid();
</script>
</body>
</html>
"""


BLOG_STATIC = Path.home() / "src" / "generallyintelligent.com" / "static" / "keystone"


def main():
    parser = argparse.ArgumentParser(description="Generate keystone eval HTML viewer")
    parser.add_argument(
        "--out",
        default=str(EVALS_DIR / "viewer" / "viewer.html"),
        help="Output HTML file path",
    )
    parser.add_argument(
        "--blog",
        action="store_true",
        default=False,
        help=f"Save output to blog repo at {BLOG_STATIC / 'eval_viewer.html'}",
    )
    parser.add_argument(
        "--s3",
        action="store_true",
        default=True,
        help="Load data from S3 (default)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        default=False,
        help="Load data from local EVALS_DIR instead of S3",
    )
    parser.add_argument(
        "--s3_prefix",
        default=DEFAULT_S3_PREFIX,
        help=f"S3 prefix for eval results (default: {DEFAULT_S3_PREFIX})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help=f"Skip local parquet cache and fetch from S3 (cache dir: {VIEWER_CACHE_DIR})",
    )
    args = parser.parse_args()

    use_s3 = not args.local

    print("Loading eval data...")
    data = build_data(
        RUN_NAMES, use_s3=use_s3, s3_prefix=args.s3_prefix, use_cache=not args.no_cache
    )

    out_path = BLOG_STATIC / "eval_viewer.html" if args.blog else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, indent=None))
    out_path.write_text(html)

    total = sum(len(repos) for run in data["runs"].values() for repos in run.values())
    print(f"Written {total} results to {out_path}")
    print(f"Open with: open '{out_path}'")


if __name__ == "__main__":
    main()
