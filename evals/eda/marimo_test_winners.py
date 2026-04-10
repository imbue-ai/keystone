"""Mutation Winner Analysis — marimo notebook.

Computes the "mutation winner" flag per (config, repo) and compares model
performance across languages and cost/time dimensions.

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
        # Mutation Winner Analysis

        Computes the "mutation winner" flag per (config, repo) and compares
        model performance across languages and cost/time dimensions.

        **Mutation winner criteria** (per repo, across configs):
        1. `success == True`
        2. `num_broken_branches > 0`
        3. `unexpected_broken_commit_passes < num_broken_branches` (caught at least one mutation)
        4. **Restoration verified** — the post-broken-commits verification must
           exist *and* succeed. If the restoration check was never run (e.g. the
           runner crashed) or explicitly failed, the run is ineligible. Without a
           successful restoration, mutation counts are unreliable because the first
           broken commit may have left residual damage that cascades through all
           subsequent mutations.
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

    PARQUET_PATH = Path(__file__).resolve().parent / "2026-04-01_thad_eval_v1.parquet"
    if not PARQUET_PATH.exists():
        mo.md(
            f"""
            ⚠️ **Parquet not found** at `{PARQUET_PATH}`.

            Generate it first:
            ```bash
            uv run python evals/eda/eval_to_parquet_cli.py \\
                s3://int8-datasets/keystone/evals/2026-04-01_thad_eval_v1 \\
                evals/eda/2026-04-01_thad_eval_v1.parquet
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
                # Treat missing post-restoration verification as a restoration failure:
                # without evidence tests still pass after running broken commits,
                # mutation counts are unreliable (first breakage may cascade).
                "restoration_check_failed": (
                    r.restoration_check_failed
                    or r.bootstrap_result is None
                    or r.bootstrap_result.post_broken_commits_verification is None
                ),
                "num_broken_branches": len(r.repo_entry.broken_branches),
            }
        )

    df = pl.DataFrame(rows)

    # Display names: pretty labels with version numbers and proper capitalization
    DISPLAY_NAMES: dict[str, str] = {
        "claude-opus": "Claude Opus 4.6",
        "gpt-5.4": "GPT-5.4",
        "claude-sonnet": "Claude Sonnet 4.5",
        "codex-gpt-5.3": "Codex GPT-5.3",
        "claude-haiku": "Claude Haiku 4.5",
        "codex-mini-gpt-5.1": "Codex Mini GPT-5.1",
    }

    # Per-model colors (matches default plotly sequence in MODEL_ORDER)
    MODEL_COLORS: dict[str, str] = {
        "claude-opus": "#636EFA",
        "gpt-5.4": "#EF553B",
        "claude-sonnet": "#00CC96",
        "codex-gpt-5.3": "#AB63FA",
        "claude-haiku": "#FFA15A",
        "codex-mini-gpt-5.1": "#19D3F3",
    }

    # Canonical display order (best first, cheapest last)
    MODEL_ORDER: list[str] = [
        "claude-opus",
        "gpt-5.4",
        "claude-sonnet",
        "codex-gpt-5.3",
        "claude-haiku",
        "codex-mini-gpt-5.1",
    ]

    mo.md(
        f"""
        **Loaded** `{PARQUET_PATH.name}`: **{len(df)}** rows,
        **{df["config_name"].n_unique()}** configs,
        **{df["repo_id"].n_unique()}** repos.
        """
    )
    return (DISPLAY_NAMES, MODEL_COLORS, MODEL_ORDER, df, json, pl)


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
def _(mo, wdf, pl, DISPLAY_NAMES, MODEL_ORDER):
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
    )

    available = set(stats["config_name"].to_list())
    models = [m for m in MODEL_ORDER if m in available]
    display_labels = [DISPLAY_NAMES.get(m, m) for m in models]
    # Reorder stats to match
    stats = (
        stats.filter(pl.col("config_name").is_in(models))
        .with_columns(pl.col("config_name").cast(pl.Enum(models)))
        .sort("config_name")
    )
    success_pcts = stats["success_pct"].to_list()
    winner_pcts = stats["winner_pct"].to_list()

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Completed %",
            x=display_labels,
            y=success_pcts,
            marker_color="#4f46e5",
            text=[f"{v:.0f}%" for v in success_pcts],
            textposition="outside",
            textfont={"size": 12, "color": "#4338ca"},
        )
    )
    fig.add_trace(
        go.Bar(
            name="Mutation Win %",
            x=display_labels,
            y=winner_pcts,
            marker_color="#22c55e",
            text=[f"{v:.0f}%" for v in winner_pcts],
            textposition="outside",
            textfont={"size": 12, "color": "#16a34a"},
        )
    )
    fig.update_layout(
        barmode="group",
        title="Completed Rate & Mutation Win Rate per Model",
        yaxis_title="Percentage",
        yaxis_range=[0, 105],
        template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "center", "x": 0.5},
        height=400,
        xaxis_tickangle=-30,
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
def _(mo, wdf, pl, DISPLAY_NAMES, MODEL_COLORS, MODEL_ORDER):
    """Stacked bar chart: completed & test-winner rate per language, top 4 models."""
    import pathlib as _pathlib

    import plotly.graph_objects as _go2

    # Top 4 models only
    _top4 = MODEL_ORDER[:4]

    # Compute per (language, config) stats
    _lang_stats = (
        wdf.filter(pl.col("config_name").is_in(_top4))
        .group_by("language", "config_name")
        .agg(
            pl.col("success").sum().alias("n_success"),
            pl.col("test_winner").sum().alias("n_winner"),
            pl.len().alias("n_total"),
        )
        .with_columns(
            (100.0 * pl.col("n_success") / pl.col("n_total")).round(1).alias("success_pct"),
            (100.0 * pl.col("n_winner") / pl.col("n_total")).round(1).alias("winner_pct"),
        )
    )

    # Only keep languages with enough repos; order by avg success rate (descending)
    _lang_agg = (
        wdf.filter(pl.col("config_name").is_in(_top4))
        .group_by("language")
        .agg(
            pl.col("repo_id").n_unique().alias("n_repos"),
            pl.col("success").mean().alias("avg_success"),
        )
        .filter(pl.col("n_repos") >= 5)
        .sort("avg_success", descending=True)
    )
    _languages = _lang_agg["language"].to_list()
    _lang_n_repos = dict(
        zip(_lang_agg["language"].to_list(), _lang_agg["n_repos"].to_list(), strict=True)
    )
    _lang_labels = [f"{lang}\n(N={_lang_n_repos[lang]})" for lang in _languages]

    fig_lang = _go2.Figure()

    def _hex_to_rgba(hex_color: str, alpha: float) -> str:
        """Convert #RRGGBB to rgba(r, g, b, a)."""
        _h = hex_color.lstrip("#")
        _r, _g, _b = int(_h[0:2], 16), int(_h[2:4], 16), int(_h[4:6], 16)
        return f"rgba({_r}, {_g}, {_b}, {alpha})"

    for _cfg in _top4:
        _display = DISPLAY_NAMES.get(_cfg, _cfg)
        _color_hex = MODEL_COLORS.get(_cfg, "#888888")
        _color_solid = _hex_to_rgba(_color_hex, 1.0)
        _color_faded = _hex_to_rgba(_color_hex, 0.3)
        _cfg_data = _lang_stats.filter(pl.col("config_name") == _cfg)

        # Build per-language values in display order
        _winner_vals: list[float] = []
        _completed_extra_vals: list[float] = []
        for _lang in _languages:
            _row = _cfg_data.filter(pl.col("language") == _lang)
            if _row.height == 0:
                _winner_vals.append(0.0)
                _completed_extra_vals.append(0.0)
            else:
                _w = _row["winner_pct"][0]
                _s = _row["success_pct"][0]
                _winner_vals.append(_w)
                _completed_extra_vals.append(_s - _w)

        # Bottom bar: test winner (solid color)
        fig_lang.add_trace(
            _go2.Bar(
                name=f"{_display} Mutation Win %",
                x=_lang_labels,
                y=_winner_vals,
                marker_color=_color_solid,
                legendgroup=_cfg,
                offsetgroup=_cfg,
            )
        )
        # Top bar: completed minus winner (faded via RGBA alpha)
        fig_lang.add_trace(
            _go2.Bar(
                name=f"{_display} Completed %",
                x=_lang_labels,
                y=_completed_extra_vals,
                marker_color=_color_faded,
                legendgroup=_cfg,
                offsetgroup=_cfg,
            )
        )

    fig_lang.update_layout(
        barmode="stack",
        title="Completed & Mutation Win Rate by Language (Top 4 Models)",
        yaxis_title="Percentage",
        yaxis_range=[0, 105],
        template="plotly_white",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 10},
        },
        height=500,
        xaxis_tickangle=-30,
    )

    # Save
    _plot_dir2 = _pathlib.Path(__file__).parent / "blog_plots"
    _plot_dir2.mkdir(parents=True, exist_ok=True)
    _cdn3 = "https://cdn.plot.ly/plotly-2.35.2.min.js"
    fig_lang.write_html(str(_plot_dir2 / "lang_completed_and_winner.html"), include_plotlyjs=_cdn3)
    fig_lang.write_image(str(_plot_dir2 / "lang_completed_and_winner.png"), scale=2)

    mo.md("## Completed & Mutation Win by Language")
    fig_lang


@app.cell
def _(total_eligible_repos, wdf, winner_counts, pl):
    """Compute pivot and gpt-5.4-only winner repos (hidden cell)."""

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
        _gpt_only_repos = pl.DataFrame()
    else:
        _gpt_only_repos = pivot.filter(pl.col(gpt_col) & ~pl.col(opus_col)).select(
            "repo_id", "language"
        )


@app.cell
def _(mo, df, pl, DISPLAY_NAMES, MODEL_COLORS, MODEL_ORDER):
    """Box plots for agent wall-clock time and inference cost."""
    import pathlib as _pathlib

    import plotly.express as px
    import plotly.graph_objects as _go

    # Prepare pandas df with all runs for box plots
    box_df = df.to_pandas()

    configs = [c for c in MODEL_ORDER if c in box_df["config_name"].unique()]
    import pandas as pd

    box_df["agent_walltime_minutes"] = box_df["agent_walltime_seconds"] / 60.0
    box_df["config_name"] = pd.Categorical(box_df["config_name"], categories=configs, ordered=True)

    # Count non-null values per config for N= labels
    def make_box(
        data: pd.DataFrame,
        y: str,
        title: str,
        y_label: str,
        y_tickformat: str | None = None,
        annotation_format: str = ".0f",
    ) -> "plotly.graph_objects.Figure":  # noqa: F821
        counts = data.dropna(subset=[y]).groupby("config_name", observed=True).size()
        label_map = {c: f"{DISPLAY_NAMES.get(c, c)}\n(N={int(counts.get(c, 0))})" for c in configs}
        color_map = {label_map[c]: MODEL_COLORS.get(c, "#888888") for c in configs}
        plot_df = data.copy()
        plot_df["config_label"] = plot_df["config_name"].map(label_map)
        ordered_labels = [label_map[c] for c in configs]

        fig = px.box(
            plot_df,
            x="config_label",
            y=y,
            color="config_label",
            color_discrete_map=color_map,
            points="all",
            hover_data=["repo_id"],
            category_orders={"config_label": ordered_labels},
            title=title,
            labels={"config_label": "Config", y: y_label},
        )
        # Semi-transparent points with thin black outlines
        fig.update_traces(
            marker={
                "opacity": 0.4,
                "size": 4,
                "line": {"width": 0.5, "color": "black"},
            },
        )
        fig.update_layout(
            showlegend=False, template="plotly_white", height=450, xaxis_tickangle=-30
        )
        if y_tickformat:
            fig.update_layout(yaxis_tickformat=y_tickformat)

        # Add median annotations above each box
        medians = data.dropna(subset=[y]).groupby("config_name", observed=True)[y].median()
        for cfg in configs:
            if cfg not in medians:
                continue
            med = medians[cfg]
            label = label_map[cfg]
            fmt = y_tickformat or ""
            text = f"${med:.2f}" if fmt.startswith("$") else f"{med:{annotation_format}}"
            fig.add_annotation(
                x=label,
                y=med,
                text=f"<b>{text}</b>",
                showarrow=False,
                yshift=12,
                font=_go.layout.annotation.Font(size=11, color="#333"),
            )

        return fig

    fig_time = make_box(
        box_df,
        y="agent_walltime_minutes",
        title="Agent Wall-clock Time by Config (per repo)",
        y_label="Wall-clock Time (min)",
        annotation_format=".1f",
    )

    fig_cost = make_box(
        box_df,
        y="cost_usd",
        title="Inference Cost by Config (per repo)",
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
    mo.output.append(mo.ui.plotly(fig_cost))
    mo.output.append(mo.ui.plotly(fig_time))


if __name__ == "__main__":
    app.run()
