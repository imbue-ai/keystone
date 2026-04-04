"""Test Winner Analysis — marimo notebook.

Computes the "test winner" flag per (config, repo) using the same logic as
the HTML eval viewer, then compares gpt-5.4 vs claude-opus to find repos
where gpt-5.4 wins but claude-opus does not.

Run interactively::

    uv run marimo edit evals/eda/marimo_test_winners.py
"""

import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    mo.md(
        """
        # Test Winner Analysis

        Replicates the "test winner" badge logic from the HTML eval viewer
        and investigates repos where **gpt-5.4** is a test winner but
        **claude-opus** is not.

        **Test winner criteria** (per repo, across configs):
        1. `success == True`
        2. `num_broken_branches > 0`
        3. `unexpected_broken_commit_passes < num_broken_branches` (caught at least one mutation)
        4. `restoration_check_failed == False`
        5. Among eligible configs, has the minimum `unexpected_broken_commit_passes` (ties allowed)
        """
    )
    return (mo,)


@app.cell
def _(mo):
    """Load parquet and extract mutation-testing fields from raw_json."""
    import json
    import sys
    from pathlib import Path

    import polars as pl

    _evals_root = str(Path(__file__).resolve().parents[1])
    if _evals_root not in sys.path:
        sys.path.insert(0, _evals_root)

    from eval_schema import KeystoneRepoResult

    PARQUET_PATH = Path("/tmp/2026-04-01_thad_eval_v1.parquet")
    if not PARQUET_PATH.exists():
        mo.md(
            f"""
            ⚠️ **Parquet not found** at `{PARQUET_PATH}`.

            Generate it first:
            ```bash
            uv run python evals/eda/eval_to_parquet_cli.py \\
                s3://int8-datasets/keystone/evals/2026-04-01_thad_eval_v1 \\
                {PARQUET_PATH}
            ```
            """
        )
        raise FileNotFoundError(str(PARQUET_PATH))

    raw_df = pl.read_parquet(PARQUET_PATH)

    # Extract fields not in flat parquet columns by deserializing raw_json
    rows: list[dict] = []
    for row in raw_df.iter_rows(named=True):
        r = KeystoneRepoResult.model_validate_json(row["raw_json"])
        rows.append(
            {
                "config_name": row["config_name"],
                "repo_id": row["repo_id"],
                "trial_index": row["trial_index"],
                "success": row["success"],
                "cost_usd": row["cost_usd"],
                "agent_walltime_seconds": row["agent_walltime_seconds"],
                "tests_passed": row["tests_passed"],
                "tests_failed": row["tests_failed"],
                "error_message": row["error_message"],
                "summary": row["summary"],
                "language": r.repo_entry.language or "unknown",
                "unexpected_broken_commit_passes": r.unexpected_broken_commit_passes,
                "restoration_check_failed": r.restoration_check_failed,
                "num_broken_branches": len(r.repo_entry.broken_branches),
            }
        )

    df = pl.DataFrame(rows)

    mo.md(
        f"""
        **Loaded** `{PARQUET_PATH.name}`: **{len(df)}** rows,
        **{df["config_name"].n_unique()}** configs,
        **{df["repo_id"].n_unique()}** repos.
        """
    )
    return (df, json, pl)


@app.cell
def _(df, pl):
    """Compute the test-winner flag for each (config, repo)."""

    # Mark eligible rows
    eligible = df.with_columns(
        (
            pl.col("success")
            & (pl.col("num_broken_branches") > 0)
            & (pl.col("unexpected_broken_commit_passes") < pl.col("num_broken_branches"))
            & (~pl.col("restoration_check_failed"))
        ).alias("eligible")
    )

    # Per-repo minimum ubc among eligible rows
    min_ubc = (
        eligible.filter(pl.col("eligible"))
        .group_by("repo_id")
        .agg(pl.col("unexpected_broken_commit_passes").min().alias("min_ubc"))
    )

    # Join back and compute test_winner
    wdf = eligible.join(min_ubc, on="repo_id", how="left").with_columns(
        (
            pl.col("eligible") & (pl.col("unexpected_broken_commit_passes") == pl.col("min_ubc"))
        ).alias("test_winner")
    )

    # Summary stats
    winner_counts = (
        wdf.filter(pl.col("test_winner"))
        .group_by("config_name")
        .len()
        .sort("len", descending=True)
        .rename({"len": "winner_repos"})
    )

    total_eligible_repos = min_ubc.height

    return (total_eligible_repos, wdf, winner_counts)


@app.cell
def _(mo, wdf, pl):
    """Bar chart: success rate and test-winner rate per model."""
    import pathlib

    import plotly.graph_objects as go

    total_repos = wdf["repo_id"].n_unique()

    stats = (
        wdf.group_by("config_name")
        .agg(
            (pl.col("success").sum()).alias("n_success"),
            (pl.col("test_winner").sum()).alias("n_winner"),
            pl.len().alias("n_total"),
        )
        .with_columns(
            (100.0 * pl.col("n_success") / pl.col("n_total")).round(1).alias("success_pct"),
            (100.0 * pl.col("n_winner") / pl.col("n_total")).round(1).alias("winner_pct"),
        )
        .sort("success_pct", descending=True)
    )

    models = stats["config_name"].to_list()
    success_pcts = stats["success_pct"].to_list()
    winner_pcts = stats["winner_pct"].to_list()

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Completed %",
            x=models,
            y=success_pcts,
            marker_color="#4f46e5",
            text=[f"{v:.0f}%" for v in success_pcts],
            textposition="outside",
            textfont={"size": 12, "color": "#4338ca"},
        )
    )
    fig.add_trace(
        go.Bar(
            name="Test Winner %",
            x=models,
            y=winner_pcts,
            marker_color="#22c55e",
            text=[f"{v:.0f}%" for v in winner_pcts],
            textposition="outside",
            textfont={"size": 12, "color": "#16a34a"},
        )
    )
    fig.update_layout(
        barmode="group",
        title="Completed Rate & Test Winner Rate per Model",
        yaxis_title="Percentage",
        yaxis_range=[0, 105],
        template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "center", "x": 0.5},
        height=400,
    )

    # Save plots
    _plot_dir = pathlib.Path(__file__).parent / "blog_plots"
    _plot_dir.mkdir(parents=True, exist_ok=True)
    _cdn = "https://cdn.plot.ly/plotly-2.35.2.min.js"
    fig.write_html(str(_plot_dir / "completed_and_test_winner.html"), include_plotlyjs=_cdn)
    fig.write_image(str(_plot_dir / "completed_and_test_winner.png"), scale=2)

    mo.md(
        f"""
        ## Model Overview

        **{total_repos}** total repos in the eval run.
        Plots saved to `{_plot_dir}/`.
        """
    )

    fig


@app.cell
def _(mo, total_eligible_repos, wdf, winner_counts, pl):
    """Show overall winner stats and identify gpt-5.4-only wins."""

    mo.md(
        f"""
        ## Test Winner Summary

        **{total_eligible_repos}** repos have at least one eligible config
        (success + caught ≥1 mutation + restoration passed).
        """
    )

    mo.output.append(winner_counts)

    # Pivot: repo x config -> test_winner bool
    pivot = (
        wdf.select("repo_id", "config_name", "test_winner", "language")
        .pivot(on="config_name", index=["repo_id", "language"], values="test_winner")
        .fill_null(False)
    )

    gpt_col = "gpt-5.4"
    opus_col = "claude-opus"

    available_configs = set(pivot.columns)
    if gpt_col not in available_configs or opus_col not in available_configs:
        mo.md(
            f"⚠️ Need both `{gpt_col}` and `{opus_col}` in configs. "
            f"Available: {sorted(available_configs - {'repo_id', 'language'})}"
        )
        gpt_only_repos = pl.DataFrame()
    else:
        gpt_only_repos = pivot.filter(pl.col(gpt_col) & ~pl.col(opus_col)).select(
            "repo_id", "language"
        )

        mo.md(
            f"""
            ### gpt-5.4 winner but NOT claude-opus

            **{len(gpt_only_repos)}** repos where gpt-5.4 is a test winner
            but claude-opus is not.
            """
        )

    return (gpt_only_repos, opus_col, gpt_col)


@app.cell
def _(mo, gpt_only_repos, wdf, pl, gpt_col, opus_col):
    """Deep dive into the gpt-5.4-only winner repos."""
    if gpt_only_repos.is_empty():
        mo.md("_No gpt-5.4-only winner repos to analyze._")
    else:
        repo_ids = gpt_only_repos["repo_id"].to_list()

        # Get full data for these repos, both configs
        detail = wdf.filter(
            pl.col("repo_id").is_in(repo_ids) & pl.col("config_name").is_in([gpt_col, opus_col])
        ).select(
            "repo_id",
            "config_name",
            "language",
            "success",
            "cost_usd",
            "agent_walltime_seconds",
            "tests_passed",
            "tests_failed",
            "unexpected_broken_commit_passes",
            "num_broken_branches",
            "restoration_check_failed",
            "test_winner",
            "error_message",
        )

        mo.md("### Detailed comparison for gpt-5.4-only winner repos")
        mo.output.append(detail.sort("repo_id", "config_name"))

        # Why did claude-opus lose?
        opus_detail = detail.filter(pl.col("config_name") == opus_col)
        opus_not_success = opus_detail.filter(~pl.col("success")).height
        opus_restoration_fail = opus_detail.filter(
            pl.col("success") & pl.col("restoration_check_failed")
        ).height
        opus_no_mutations = opus_detail.filter(
            pl.col("success")
            & ~pl.col("restoration_check_failed")
            & (pl.col("unexpected_broken_commit_passes") >= pl.col("num_broken_branches"))
        ).height
        opus_higher_ubc = opus_detail.filter(
            pl.col("success")
            & ~pl.col("restoration_check_failed")
            & (pl.col("unexpected_broken_commit_passes") < pl.col("num_broken_branches"))
            & ~pl.col("test_winner")
        ).height

        mo.md(
            f"""
            ### Why claude-opus lost in these repos

            | Reason | Count |
            |--------|-------|
            | Run failed (success=False) | {opus_not_success} |
            | Restoration check failed | {opus_restoration_fail} |
            | All mutations slipped through | {opus_no_mutations} |
            | Caught fewer mutations than gpt-5.4 | {opus_higher_ubc} |
            """
        )


@app.cell
def _(mo, gpt_only_repos, wdf, pl):
    """Pattern analysis: language distribution & aggregate stats."""
    if gpt_only_repos.is_empty():
        mo.md("_No gpt-5.4-only winner repos to analyze._")
    else:
        # Language distribution of gpt-5.4-only winner repos
        lang_dist = (
            gpt_only_repos.group_by("language")
            .len()
            .sort("len", descending=True)
            .rename({"len": "count"})
        )
        mo.md("### Language distribution (gpt-5.4-only winner repos)")
        mo.output.append(lang_dist)

        # Compare aggregate stats: gpt-5.4-only-winner repos vs all other repos
        gpt_only_ids = set(gpt_only_repos["repo_id"].to_list())
        all_repos = set(wdf["repo_id"].unique().to_list())
        other_ids = all_repos - gpt_only_ids

        def agg_stats(repo_set: set[str], label: str) -> dict:
            subset = wdf.filter(pl.col("repo_id").is_in(list(repo_set)) & pl.col("success"))
            return {
                "group": label,
                "n_repos": len(repo_set),
                "median_cost_usd": round(subset["cost_usd"].median() or 0, 3),
                "median_duration_s": round(subset["agent_walltime_seconds"].median() or 0, 0),
                "median_tests_passed": subset["tests_passed"].median(),
                "median_tests_failed": subset["tests_failed"].median(),
            }

        stats_df = pl.DataFrame(
            [
                agg_stats(gpt_only_ids, "gpt-5.4-only winners"),
                agg_stats(other_ids, "all other repos"),
            ]
        )
        mo.md("### Aggregate stats comparison (successful runs only)")
        mo.output.append(stats_df)


@app.cell
def _(mo, gpt_only_repos):
    """List repo names where gpt-5.4 is a test winner but claude-opus is not."""
    if gpt_only_repos.is_empty():
        _out = mo.md("_No gpt-5.4-only winner repos._")
    else:
        names = sorted(gpt_only_repos["repo_id"].to_list())
        bullet_list = "\n".join(f"- `{n}`" for n in names)
        _out = mo.md(
            f"""
            ### Repos where gpt-5.4 wins but claude-opus does not ({len(names)})

            {bullet_list}
            """
        )
    _out


@app.cell
def _(mo, df, pl):
    """Box plots for agent wall-clock time and inference cost."""
    import pathlib as _pathlib

    import plotly.express as px

    # Prepare pandas df with all runs for box plots
    box_df = df.to_pandas()

    # Order: claude models, then gpt, then codex models
    configs = [
        "claude-opus",
        "claude-haiku",
        "gpt-5.4",
        "codex-gpt-5.3",
        "codex-mini-gpt-5.1",
    ]
    configs = [c for c in configs if c in box_df["config_name"].unique()]
    import pandas as pd

    box_df["config_name"] = pd.Categorical(box_df["config_name"], categories=configs, ordered=True)

    # Count non-null values per config for N= labels
    def make_box(
        data: pd.DataFrame,
        y: str,
        title: str,
        y_label: str,
        y_tickformat: str | None = None,
    ) -> "plotly.graph_objects.Figure":  # noqa: F821
        counts = data.dropna(subset=[y]).groupby("config_name", observed=True).size()
        label_map = {c: f"{c}\n(N={int(counts.get(c, 0))})" for c in configs}
        plot_df = data.copy()
        plot_df["config_label"] = plot_df["config_name"].map(label_map)
        ordered_labels = [label_map[c] for c in configs]

        fig = px.box(
            plot_df,
            x="config_label",
            y=y,
            color="config_label",
            points="all",
            hover_data=["repo_id"],
            category_orders={"config_label": ordered_labels},
            title=title,
            labels={"config_label": "Config", y: y_label},
        )
        fig.update_layout(showlegend=False, template="plotly_white", height=450)
        if y_tickformat:
            fig.update_layout(yaxis_tickformat=y_tickformat)
        return fig

    fig_time = make_box(
        box_df,
        y="agent_walltime_seconds",
        title="Agent Wall-clock Time by Config",
        y_label="Wall-clock Time (s)",
    )

    fig_cost = make_box(
        box_df,
        y="cost_usd",
        title="Inference Cost by Config",
        y_label="Cost (USD)",
        y_tickformat="$.2f",
    )

    # Save plots
    _bplot_dir = _pathlib.Path(__file__).parent / "blog_plots"
    _bplot_dir.mkdir(parents=True, exist_ok=True)
    _cdn2 = "https://cdn.plot.ly/plotly-2.35.2.min.js"
    fig_time.write_html(str(_bplot_dir / "box_walltime.html"), include_plotlyjs=_cdn2)
    fig_time.write_image(str(_bplot_dir / "box_walltime.png"), scale=2)
    fig_cost.write_html(str(_bplot_dir / "box_cost.html"), include_plotlyjs=_cdn2)
    fig_cost.write_image(str(_bplot_dir / "box_cost.png"), scale=2)

    mo.md("## Box Plots")
    mo.output.append(mo.ui.plotly(fig_time))
    mo.output.append(mo.ui.plotly(fig_cost))


if __name__ == "__main__":
    app.run()
