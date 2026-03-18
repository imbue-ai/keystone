"""Codex Eval CDF Analysis — marimo notebook.

Generates CDF plots for agent walltime and inference cost, saves them as
self-contained HTML files for embedding in blog posts.

Run interactively::

    uv run marimo edit evals/eda/time_cdf_analysis.py

Render to static HTML::

    uv run marimo export html evals/eda/time_cdf_analysis.py -o time_cdf_analysis.html
"""

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    mo.md(
        """
        # Codex Eval — CDF Analysis

        CDF plots of agent walltime and inference cost for each codex
        configuration.  Failing / timed-out runs are marked with a red **✕**.

        Hover over a point to highlight the same repo across all configs.

        HTML files for blog embedding are saved alongside this notebook.
        """
    )
    return (mo,)


@app.cell
def _(mo):
    import sys
    from pathlib import Path

    # Ensure the evals package root is importable when run standalone via marimo
    _evals_root = str(Path(__file__).resolve().parents[1])
    if _evals_root not in sys.path:
        sys.path.insert(0, _evals_root)

    from eda.time_cdf_plot import (  # noqa: E402
        DEFAULT_PARQUET,
        build_cost_figure,
        build_figure,
        export_html,
        load_codex_data,
    )

    pdf = load_codex_data(DEFAULT_PARQUET)
    mo.md(f"Loaded **{len(pdf)}** codex rows from `{DEFAULT_PARQUET.name}`")
    return Path, build_cost_figure, build_figure, export_html, pdf


@app.cell
def _(mo):
    mo.md("## CDF — Agent Wall-clock Time")
    return


@app.cell
def _(Path, build_figure, export_html, mo, pdf):
    fig_time = build_figure(pdf)

    _out = Path(__file__).parent / "output" / "codex_walltime_cdf.html"
    _out.parent.mkdir(parents=True, exist_ok=True)
    export_html(fig_time, _out, div_id="walltime-cdf")
    mo.md(f"Saved → `{_out}`")
    return (fig_time,)


@app.cell
def _(fig_time, mo):
    mo.ui.plotly(fig_time)
    return


@app.cell
def _(mo):
    mo.md("## CDF — Inference Cost")
    return


@app.cell
def _(Path, build_cost_figure, export_html, mo, pdf):
    fig_cost = build_cost_figure(pdf)

    _out = Path(__file__).parent / "output" / "codex_cost_cdf.html"
    _out.parent.mkdir(parents=True, exist_ok=True)
    export_html(fig_cost, _out, div_id="cost-cdf")
    mo.md(f"Saved → `{_out}`")
    return (fig_cost,)


@app.cell
def _(fig_cost, mo):
    mo.ui.plotly(fig_cost)
    return


@app.cell
def _(mo, pdf):
    import polars as pl

    _stats = (
        pl.from_pandas(pdf)
        .group_by("config_name")
        .agg(
            pl.col("agent_walltime_seconds").mean().alias("mean_time_s"),
            pl.col("agent_walltime_seconds").median().alias("median_time_s"),
            pl.col("cost_usd").mean().alias("mean_cost_usd"),
            pl.col("cost_usd").sum().alias("total_cost_usd"),
            pl.col("success").mean().alias("success_rate"),
            pl.len().alias("n"),
        )
        .sort("median_time_s")
    )
    mo.md("## Summary stats by config")
    mo.ui.table(_stats, selection=None)
    return


if __name__ == "__main__":
    app.run()
