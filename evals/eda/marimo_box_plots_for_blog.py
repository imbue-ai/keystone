"""Box Plot Analysis — marimo notebook.

Generates box plots for agent walltime, inference cost, and normalized tests
passed, comparing selected eval configurations.

Run interactively::

    uv run marimo edit evals/eda/marimo_box_plots_for_blog.py

Render to static HTML::

    uv run marimo export html evals/eda/marimo_box_plots_for_blog.py -o marimo_box_plots_for_blog.html
"""

import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    mo.md(
        """
        # Eval Box Plots

        Box plots comparing agent wall-clock time, inference cost, and fraction
        of max tests passed across selected configurations.
        """
    )
    return (mo,)


@app.cell
def _(mo):
    from pathlib import Path

    import pandas as pd
    import plotly.express as px
    import polars as pl

    PARQUET_PATH = Path.home() / "keystone_eval" / "blog.parquet"

    CONFIGS = [
        "codex-mini-gpt-5.1",
        "codex-gpt-5.3",
        "gpt-5.4",
        "opus-4.6",
        "claude-haiku",
    ]

    # Parquet already has deduplicated tests_passed, tests_failed, tests_discovered
    all_df = pl.read_parquet(PARQUET_PATH).select(
        "config_name",
        "repo_id",
        "trial_index",
        "success",
        "agent_walltime_seconds",
        "cost_usd",
        "agent_timed_out",
        "tests_passed",
        "tests_discovered",
    )

    # Collapse multiple trials per (repo, config) into a single row:
    # pick the first successful trial (lowest trial_index where success=True).
    # If no trial succeeded, fall back to the lowest trial_index.
    all_df = all_df.sort(
        ["config_name", "repo_id", "success", "trial_index"],
        descending=[False, False, True, False],
        nulls_last=True,
    )
    all_df = all_df.group_by(["config_name", "repo_id"], maintain_order=True).first()

    # Failed runs that never reached verification have null test counts;
    # treat them as 0 so they appear in plots rather than being silently dropped.
    all_df = all_df.with_columns(
        pl.col("tests_passed").fill_null(0).alias("tests_passed"),
        pl.col("tests_discovered").fill_null(0).alias("tests_discovered"),
    )

    # Compute repo_max_tests across ALL configs (deduplicated)
    repo_max = all_df.group_by("repo_id").agg(
        pl.col("tests_discovered").max().alias("repo_max_tests")
    )
    all_df = all_df.join(repo_max, on="repo_id")
    all_df = all_df.with_columns(
        pl.when(pl.col("repo_max_tests") > 0)
        .then(pl.col("tests_passed") / pl.col("repo_max_tests"))
        .otherwise(0.0)
        .alias("norm_tests_passed")
    )

    # Filter to target configs
    df = all_df.filter(pl.col("config_name").is_in(CONFIGS)).to_pandas()

    # Enforce config ordering
    df["config_name"] = pd.Categorical(df["config_name"], categories=CONFIGS, ordered=True)

    def make_box_plot(
        data: pd.DataFrame,
        y: str,
        title: str,
        y_label: str,
        configs: list[str],
        y_tickformat: str | None = None,
    ) -> "plotly.graph_objects.Figure":  # noqa: F821
        """Create a box plot with per-config (N=...) labels on the x-axis."""
        # Count non-null y values per config for the N= labels
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
            hover_data=["repo_id", "trial_index", "tests_passed"],
            category_orders={"config_label": ordered_labels},
            title=title,
            labels={"config_label": "Config", y: y_label},
        )
        fig.update_layout(showlegend=False)
        if y_tickformat:
            fig.update_layout(yaxis_tickformat=y_tickformat)
        return fig

    mo.md(f"Loaded **{len(df)}** rows for {len(CONFIGS)} configs from `{PARQUET_PATH.name}`")
    return CONFIGS, Path, df, make_box_plot, px


@app.cell
def _(mo):
    mo.md("""
    ## Agent Wall-clock Time
    """)
    return


@app.cell
def _(CONFIGS, Path, df, make_box_plot, mo):
    fig_time = make_box_plot(
        df,
        y="agent_walltime_seconds",
        title="Agent Wall-clock Time by Config",
        y_label="Wall-clock Time (s)",
        configs=CONFIGS,
    )

    _out = Path(__file__).parent / "output" / "box_walltime.html"
    _out.parent.mkdir(parents=True, exist_ok=True)
    fig_time.write_html(str(_out), include_plotlyjs="cdn")
    mo.md(f"Saved → `{_out}`")
    return (fig_time,)


@app.cell
def _(fig_time, mo):
    mo.ui.plotly(fig_time)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Inference Cost
    """)
    return


@app.cell
def _(CONFIGS, Path, df, make_box_plot, mo):
    fig_cost = make_box_plot(
        df,
        y="cost_usd",
        title="Inference Cost by Config",
        y_label="Cost (USD)",
        configs=CONFIGS,
    )

    _out = Path(__file__).parent / "output" / "box_cost.html"
    _out.parent.mkdir(parents=True, exist_ok=True)
    fig_cost.write_html(str(_out), include_plotlyjs="cdn")
    mo.md(f"Saved → `{_out}`")
    return (fig_cost,)


@app.cell
def _(fig_cost, mo):
    mo.ui.plotly(fig_cost)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Tests Passed (fraction of max discovered)
    """)
    return


@app.cell
def _(CONFIGS, Path, df, make_box_plot, mo):
    fig_tests = make_box_plot(
        df,
        y="norm_tests_passed",
        title="Tests Passed (fraction of max discovered per repo)",
        y_label="Fraction of Max Tests Passed",
        configs=CONFIGS,
        y_tickformat=".0%",
    )

    _out = Path(__file__).parent / "output" / "box_norm_tests.html"
    _out.parent.mkdir(parents=True, exist_ok=True)
    fig_tests.write_html(str(_out), include_plotlyjs="cdn")
    mo.md(f"Saved → `{_out}`")
    return (fig_tests,)


@app.cell
def _(fig_tests, mo):
    mo.ui.plotly(fig_tests)
    return


if __name__ == "__main__":
    app.run()
