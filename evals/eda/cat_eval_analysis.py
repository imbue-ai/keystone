# ruff: noqa: PLC0415
"""CAT Eval Analysis — marimo notebook.

Analyzes the 2026-03-18-cat eval run data from cat_results.parquet.
Covers success rates, test discovery, failure modes, agent personality,
cost/efficiency, and train/holdout hypothesis validation.

Run interactively::

    uv run marimo edit evals/eda/cat_eval_analysis.py

Render to static HTML::

    uv run marimo export html evals/eda/cat_eval_analysis.py -o cat_eval_analysis.html
"""

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="medium")


# ---------------------------------------------------------------------------
# Cell 1: Data loading + config overview
# ---------------------------------------------------------------------------
@app.cell
def _():
    import json
    import re
    from itertools import combinations
    from pathlib import Path

    import marimo as mo
    import numpy as np
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go

    # Load data
    _parquet_path = Path(__file__).resolve().parents[2] / "cat_results.parquet"
    df = pd.read_parquet(_parquet_path)

    # Extract config metadata from raw_json for the first row of each config
    config_rows = []
    for cfg in sorted(df["config_name"].unique()):
        row = df[df["config_name"] == cfg].iloc[0]
        raw = json.loads(row["raw_json"])
        eval_cfg = raw.get("eval_config", {})
        config_rows.append(
            {
                "config_name": cfg,
                "provider": eval_cfg.get("provider", ""),
                "model": eval_cfg.get("model", ""),
                "reasoning_level": eval_cfg.get("codex_reasoning_level")
                or eval_cfg.get("claude_reasoning_level")
                or "",
                "n_rows": len(df[df["config_name"] == cfg]),
                "success_rate": df[df["config_name"] == cfg]["success"].mean(),
            }
        )
    config_df = pd.DataFrame(config_rows)

    mo.md(
        f"""
        # CAT Eval Analysis (2026-03-18)

        **Dataset:** {len(df)} rows — {df["config_name"].nunique()} configs
        x ~{df["repo_id"].nunique()} repos x {df["trial_index"].nunique()} trials

        ## Config Overview
        {mo.as_html(config_df.style.format({"success_rate": "{:.1%}"}))}
        """
    )
    return config_df, config_rows, df, go, json, mo, np, pd, px, re, combinations


# ---------------------------------------------------------------------------
# Cell 2: Success rate comparison — train vs holdout
# ---------------------------------------------------------------------------
@app.cell
def _(df, mo, pd, px):
    # Split into train (trials 0,1) and holdout (trial 2)
    df_train = df[df["trial_index"].isin([0, 1])]
    df_holdout = df[df["trial_index"] == 2]

    _rows = []
    for cfg in sorted(df["config_name"].unique()):
        _rows.append(
            {
                "config": cfg,
                "split": "Train (trials 0+1)",
                "success_rate": df_train[df_train["config_name"] == cfg]["success"].mean(),
            }
        )
        _rows.append(
            {
                "config": cfg,
                "split": "Holdout (trial 2)",
                "success_rate": df_holdout[df_holdout["config_name"] == cfg]["success"].mean(),
            }
        )
    split_df = pd.DataFrame(_rows)

    fig_success = px.bar(
        split_df,
        x="config",
        y="success_rate",
        color="split",
        barmode="group",
        title="Success Rate by Config: Train vs Holdout",
        labels={"success_rate": "Success Rate", "config": "Config"},
    )
    fig_success.update_yaxes(tickformat=".0%")
    fig_success.update_layout(legend_title_text="Split")

    mo.md("## Success Rate: Train vs Holdout")
    mo.ui.plotly(fig_success)
    return df_holdout, df_train, fig_success, split_df


# ---------------------------------------------------------------------------
# Cell 3: Test discovery analysis
# ---------------------------------------------------------------------------
@app.cell
def _(df, mo, px):
    df_tests = df.copy()
    df_tests["tests_total"] = df_tests["tests_passed"].fillna(0) + df_tests["tests_failed"].fillna(
        0
    )

    fig_tests_box = px.box(
        df_tests,
        x="config_name",
        y="tests_total",
        color="config_name",
        title="Test Discovery: Total Tests Found (passed + failed)",
        labels={"tests_total": "Total Tests Found", "config_name": "Config"},
    )

    fig_passed_box = px.box(
        df_tests,
        x="config_name",
        y="tests_passed",
        color="config_name",
        title="Tests Passed Distribution by Config",
        labels={"tests_passed": "Tests Passed", "config_name": "Config"},
    )

    # Per-repo normalized comparison: for each repo+trial, normalize by max tests found
    df_norm = df_tests.copy()
    df_norm["max_tests"] = df_norm.groupby(["repo_id", "trial_index"])["tests_total"].transform(
        "max"
    )
    df_norm["norm_tests"] = df_norm["tests_total"] / df_norm["max_tests"].replace(0, 1)

    fig_norm = px.box(
        df_norm,
        x="config_name",
        y="norm_tests",
        color="config_name",
        title="Normalized Test Discovery (fraction of max tests found per repo)",
        labels={"norm_tests": "Fraction of Max Tests", "config_name": "Config"},
    )

    mo.md("## Test Discovery Analysis")
    mo.ui.plotly(fig_tests_box)
    mo.ui.plotly(fig_passed_box)
    mo.ui.plotly(fig_norm)
    return df_norm, df_tests, fig_norm, fig_passed_box, fig_tests_box


# ---------------------------------------------------------------------------
# Cell 4: Head-to-head pairwise comparison
# ---------------------------------------------------------------------------
@app.cell
def _(combinations, df, go, mo, np, pd):
    configs = sorted(df["config_name"].unique())
    win_matrix = pd.DataFrame(
        np.zeros((len(configs), len(configs))),
        index=configs,
        columns=configs,
        dtype=float,
    )

    for cfg_a, cfg_b in combinations(configs, 2):
        a_df = df[df["config_name"] == cfg_a][["repo_id", "trial_index", "success", "tests_passed"]]
        b_df = df[df["config_name"] == cfg_b][["repo_id", "trial_index", "success", "tests_passed"]]
        merged = a_df.merge(b_df, on=["repo_id", "trial_index"], suffixes=("_a", "_b"))
        # Win = succeed when opponent fails, or both succeed but more tests passed
        a_wins = (merged["success_a"] & ~merged["success_b"]) | (
            merged["success_a"]
            & merged["success_b"]
            & (merged["tests_passed_a"].fillna(0) > merged["tests_passed_b"].fillna(0))
        )
        b_wins = (~merged["success_a"] & merged["success_b"]) | (
            merged["success_a"]
            & merged["success_b"]
            & (merged["tests_passed_b"].fillna(0) > merged["tests_passed_a"].fillna(0))
        )
        n = len(merged)
        win_matrix.loc[cfg_a, cfg_b] = a_wins.sum() / n if n > 0 else 0.5
        win_matrix.loc[cfg_b, cfg_a] = b_wins.sum() / n if n > 0 else 0.5

    # Fill diagonal with 0.5
    for c in configs:
        win_matrix.loc[c, c] = 0.5

    fig_heatmap = go.Figure(
        data=go.Heatmap(
            z=win_matrix.values,
            x=configs,
            y=configs,
            text=np.round(win_matrix.values, 3).astype(str),
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmin=0,
            zmax=1,
        )
    )
    fig_heatmap.update_layout(
        title="Pairwise Win Rate (row beats column)",
        xaxis_title="Opponent",
        yaxis_title="Config",
    )

    mo.md("## Head-to-Head Pairwise Comparison")
    mo.ui.plotly(fig_heatmap)
    return configs, fig_heatmap, win_matrix


# ---------------------------------------------------------------------------
# Cell 5: Failure mode analysis
# ---------------------------------------------------------------------------
@app.cell
def _(df, mo, pd, px, re):
    failed = df[~df["success"]].copy()

    def _categorize_failure(row: pd.Series) -> str:
        text = " ".join(
            [str(row.get("error_message", "") or ""), str(row.get("summary", "") or "")]
        ).lower()
        if re.search(r"time.?out|timed.?out", text):
            return "Timeout"
        if re.search(r"docker|image.?build|container", text):
            return "Docker/Build"
        if re.search(r"install|dependency|pip|npm|package", text):
            return "Dependency"
        if re.search(r"test.?(setup|config|discover)|no tests|collection", text):
            return "Test Setup"
        if re.search(r"syntax|parse|import.?error|module.?not.?found", text):
            return "Code Error"
        if re.search(r"permission|access|auth", text):
            return "Permission"
        return "Other"

    failed["failure_mode"] = failed.apply(_categorize_failure, axis=1)

    failure_counts = (
        failed.groupby(["config_name", "failure_mode"]).size().reset_index(name="count")
    )

    fig_failures = px.bar(
        failure_counts,
        x="config_name",
        y="count",
        color="failure_mode",
        title="Failure Mode Distribution by Config",
        labels={"count": "Count", "config_name": "Config"},
        barmode="stack",
    )

    mo.md(
        f"""
        ## Failure Mode Analysis

        Total failed runs: {len(failed)} / {len(df)} ({len(failed) / len(df):.1%})
        """
    )
    mo.ui.plotly(fig_failures)
    return failed, failure_counts, fig_failures


# ---------------------------------------------------------------------------
# Cell 6: Agent personality / cheating detection
# ---------------------------------------------------------------------------
@app.cell
def _(df, mo, pd, px, re):
    personality_df = df.copy()
    summaries = personality_df["summary"].fillna("").str.lower()

    # Laziness signals
    _lazy_pat = re.compile(r"\b(skip|mock|stub|placeholder|todo|hack|shortcut|dummy)\b")
    personality_df["lazy_signals"] = summaries.apply(lambda s: len(_lazy_pat.findall(s)))
    personality_df["has_lazy"] = personality_df["lazy_signals"] > 0

    # Cheating signals
    _cheat_pat = re.compile(
        r"(comment.?out|disable|remove.?test|mark.?skip|xfail|"
        r"skip.?test|delete.?test|pytest\.skip|@skip)"
    )
    personality_df["cheat_signals"] = summaries.apply(lambda s: len(_cheat_pat.findall(s)))
    personality_df["has_cheat"] = personality_df["cheat_signals"] > 0

    # High effort signals
    personality_df["summary_len"] = personality_df["summary"].fillna("").str.len()
    _effort_pat = re.compile(r"\b(debug|iterate|investigat|fix|refactor|analyz|trace|diagnos)\w*")
    personality_df["effort_signals"] = summaries.apply(lambda s: len(_effort_pat.findall(s)))
    personality_df["has_effort"] = personality_df["effort_signals"] > 0

    # Aggregate by config
    _pers_rows = []
    for cfg in sorted(personality_df["config_name"].unique()):
        cdf = personality_df[personality_df["config_name"] == cfg]
        _pers_rows.append(
            {
                "config": cfg,
                "lazy_rate": cdf["has_lazy"].mean(),
                "cheat_rate": cdf["has_cheat"].mean(),
                "effort_rate": cdf["has_effort"].mean(),
                "median_summary_len": cdf["summary_len"].median(),
            }
        )
    pers_summary = pd.DataFrame(_pers_rows)

    # Melt for grouped bar chart
    pers_melted = pers_summary.melt(
        id_vars="config",
        value_vars=["lazy_rate", "cheat_rate", "effort_rate"],
        var_name="signal",
        value_name="rate",
    )
    fig_pers = px.bar(
        pers_melted,
        x="config",
        y="rate",
        color="signal",
        barmode="group",
        title="Agent Personality Signal Rates by Config",
        labels={"rate": "Fraction of Runs", "config": "Config"},
    )
    fig_pers.update_yaxes(tickformat=".0%")

    mo.md(
        f"""
        ## Agent Personality / Cheating Detection

        Signal detection via keyword matching in agent summaries.

        {
            mo.as_html(
                pers_summary.style.format(
                    {
                        "lazy_rate": "{:.1%}",
                        "cheat_rate": "{:.1%}",
                        "effort_rate": "{:.1%}",
                        "median_summary_len": "{:.0f}",
                    }
                )
            )
        }
        """
    )
    mo.ui.plotly(fig_pers)
    return pers_melted, pers_summary, personality_df, fig_pers


# ---------------------------------------------------------------------------
# Cell 7: Cost and efficiency
# ---------------------------------------------------------------------------
@app.cell
def _(df, mo, px):
    fig_cost = px.box(
        df,
        x="config_name",
        y="cost_usd",
        color="config_name",
        title="Cost (USD) Distribution by Config",
        labels={"cost_usd": "Cost (USD)", "config_name": "Config"},
    )

    fig_time = px.box(
        df,
        x="config_name",
        y="agent_walltime_seconds",
        color="config_name",
        title="Agent Walltime (seconds) Distribution by Config",
        labels={
            "agent_walltime_seconds": "Walltime (s)",
            "config_name": "Config",
        },
    )

    df_eff = df.copy()
    df_eff["tests_per_dollar"] = df_eff["tests_passed"].fillna(0) / df_eff["cost_usd"].replace(
        0, float("nan")
    )

    fig_scatter = px.scatter(
        df_eff,
        x="cost_usd",
        y="tests_passed",
        color="config_name",
        title="Cost vs Tests Passed",
        labels={"cost_usd": "Cost (USD)", "tests_passed": "Tests Passed"},
        opacity=0.5,
    )

    fig_efficiency = px.box(
        df_eff,
        x="config_name",
        y="tests_per_dollar",
        color="config_name",
        title="Efficiency: Tests Passed per Dollar",
        labels={"tests_per_dollar": "Tests / $", "config_name": "Config"},
    )

    mo.md("## Cost and Efficiency")
    mo.ui.plotly(fig_cost)
    mo.ui.plotly(fig_time)
    mo.ui.plotly(fig_scatter)
    mo.ui.plotly(fig_efficiency)
    return df_eff, fig_cost, fig_efficiency, fig_scatter, fig_time


# ---------------------------------------------------------------------------
# Cell 8: Hypothesis formation and validation
# ---------------------------------------------------------------------------
@app.cell
def _(df, df_holdout, df_train, mo, np, pd):
    def _success_rate(frame: pd.DataFrame, config: str) -> float:
        subset = frame[frame["config_name"] == config]
        return subset["success"].mean() if len(subset) > 0 else np.nan

    def _median_tests(frame: pd.DataFrame, config: str) -> float:
        subset = frame[frame["config_name"] == config]
        return float(subset["tests_passed"].median()) if len(subset) > 0 else np.nan

    def _cheat_rate(frame: pd.DataFrame, config: str) -> float:
        import re as _re

        subset = frame[frame["config_name"] == config]
        pat = _re.compile(r"(comment.?out|disable|remove.?test|mark.?skip|xfail|skip.?test)")
        summaries = subset["summary"].fillna("").str.lower()
        return summaries.apply(lambda s: bool(pat.search(s))).mean()

    hypotheses = [
        {
            "hypothesis": "opus-4.6 has highest success rate",
            "train_evidence": f"opus-4.6: {_success_rate(df_train, 'opus-4.6'):.1%}, "
            f"next: {max(_success_rate(df_train, c) for c in df['config_name'].unique() if c != 'opus-4.6'):.1%}",
            "holdout_evidence": f"opus-4.6: {_success_rate(df_holdout, 'opus-4.6'):.1%}, "
            f"next: {max(_success_rate(df_holdout, c) for c in df['config_name'].unique() if c != 'opus-4.6'):.1%}",
            "confirmed": _success_rate(df_holdout, "opus-4.6")
            == max(_success_rate(df_holdout, c) for c in df["config_name"].unique()),
        },
        {
            "hypothesis": "opencode-codex has lowest success rate",
            "train_evidence": f"{_success_rate(df_train, 'opencode-codex'):.1%}",
            "holdout_evidence": f"{_success_rate(df_holdout, 'opencode-codex'):.1%}",
            "confirmed": _success_rate(df_holdout, "opencode-codex")
            == min(_success_rate(df_holdout, c) for c in df["config_name"].unique()),
        },
        {
            "hypothesis": "opus-4.6 discovers more tests (higher median tests_passed)",
            "train_evidence": f"median passed: {_median_tests(df_train, 'opus-4.6'):.0f}",
            "holdout_evidence": f"median passed: {_median_tests(df_holdout, 'opus-4.6'):.0f}",
            "confirmed": _median_tests(df_holdout, "opus-4.6")
            == max(_median_tests(df_holdout, c) for c in df["config_name"].unique()),
        },
        {
            "hypothesis": "opencode-codex has highest cheating signal rate",
            "train_evidence": f"{_cheat_rate(df_train, 'opencode-codex'):.1%}",
            "holdout_evidence": f"{_cheat_rate(df_holdout, 'opencode-codex'):.1%}",
            "confirmed": _cheat_rate(df_holdout, "opencode-codex")
            == max(_cheat_rate(df_holdout, c) for c in df["config_name"].unique()),
        },
        {
            "hypothesis": "Train and holdout success rates agree within 5pp per config",
            "train_evidence": ", ".join(
                f"{c}: {_success_rate(df_train, c):.1%}" for c in sorted(df["config_name"].unique())
            ),
            "holdout_evidence": ", ".join(
                f"{c}: {_success_rate(df_holdout, c):.1%}"
                for c in sorted(df["config_name"].unique())
            ),
            "confirmed": all(
                abs(_success_rate(df_train, c) - _success_rate(df_holdout, c)) < 0.05
                for c in df["config_name"].unique()
            ),
        },
    ]
    hyp_df = pd.DataFrame(hypotheses)

    mo.md(
        f"""
        ## Hypothesis Formation & Validation

        Hypotheses formed from trials 0+1 (train), validated on trial 2 (holdout).

        {mo.as_html(hyp_df)}
        """
    )
    return hyp_df, hypotheses


# ---------------------------------------------------------------------------
# Cell 9: Per-repo deep dive — repos with most disagreement
# ---------------------------------------------------------------------------
@app.cell
def _(df, mo, pd):
    # For each repo+trial, compute variance of success across configs
    repo_var = (
        df.groupby(["repo_id", "trial_index"])["success"]
        .agg(["mean", "std", "sum", "count"])
        .reset_index()
    )
    # Repos where configs disagree most = highest std averaged across trials
    repo_disagree = (
        repo_var.groupby("repo_id")["std"]
        .mean()
        .sort_values(ascending=False)
        .head(20)
        .reset_index()
    )
    repo_disagree.columns = ["repo_id", "avg_std_success"]

    # Build detail table for top-disagreement repos
    top_repos = repo_disagree["repo_id"].tolist()
    detail_rows = []
    for repo in top_repos:
        rdf = df[df["repo_id"] == repo]
        for cfg in sorted(rdf["config_name"].unique()):
            cdf = rdf[rdf["config_name"] == cfg]
            detail_rows.append(
                {
                    "repo_id": repo,
                    "config": cfg,
                    "success_rate": cdf["success"].mean(),
                    "median_tests_passed": cdf["tests_passed"].median(),
                    "trials": len(cdf),
                }
            )
    detail_df = pd.DataFrame(detail_rows)

    mo.md(
        f"""
        ## Per-Repo Deep Dive: Most Disagreed Repos

        Top 20 repos where configs disagree most (by std of success across configs).

        {mo.as_html(repo_disagree)}

        ### Detail for top disagreement repos
        {
            mo.as_html(
                detail_df.style.format(
                    {
                        "success_rate": "{:.0%}",
                        "median_tests_passed": "{:.0f}",
                    }
                )
            )
        }
        """
    )
    return detail_df, repo_disagree, top_repos


if __name__ == "__main__":
    app.run()
