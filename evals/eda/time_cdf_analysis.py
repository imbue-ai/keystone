"""Time CDF Analysis — marimo notebook.

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
        # Time Spent per Repo — CDF by Codex Config

        CDF of `agent_walltime_seconds` for each codex configuration.
        Failing / timed-out runs are marked with a red **✕** marker.

        Hover over a point to highlight the same repo across all configs.
        """
    )
    return (mo,)


@app.cell
def _(mo):
    from pathlib import Path

    import polars as pl

    PARQUET_PATH = Path.home() / "keystone_eval" / "2026-03-14.parquet"
    df_all = pl.read_parquet(PARQUET_PATH)

    CODEX_CONFIGS = [
        "codex-gpt-5.3-reasoning_xhigh",
        "codex-gpt-5.3",
        "codex-gpt-5.3-reasoning_medium",
        "codex-mini-gpt-5.1",
        "codex-gpt-5.3-no_agents_md",
        "codex-gpt-5.3-no_agents_md-no_guardrail",
    ]

    df = df_all.filter(pl.col("config_name").is_in(CODEX_CONFIGS))
    mo.md(f"Loaded **{len(df)}** codex rows from `{PARQUET_PATH.name}`")
    return CODEX_CONFIGS, df, pl


@app.cell
def _(CODEX_CONFIGS, df, mo, pl):
    import numpy as np
    import plotly.graph_objects as go

    # Preserve config order
    config_order = {c: i for i, c in enumerate(CODEX_CONFIGS)}

    # Colors for each config
    COLORS = [
        "#636EFA",  # reasoning_xhigh
        "#00CC96",  # gpt-5.3
        "#AB63FA",  # reasoning_medium
        "#EF553B",  # mini
        "#FFA15A",  # no_agents_md
        "#FF6692",  # no_agents_md-no_guardrail
    ]

    pdf = df.select(
        "config_name", "repo_id", "agent_walltime_seconds", "cost_usd", "success", "agent_timed_out"
    ).to_pandas()

    pdf["failed"] = ~pdf["success"] | pdf["agent_timed_out"].fillna(False)

    fig = go.Figure()

    for idx, config in enumerate(CODEX_CONFIGS):
        sub = pdf[pdf["config_name"] == config].copy()
        if sub.empty:
            continue
        sub = sub.sort_values("agent_walltime_seconds").reset_index(drop=True)
        n = len(sub)
        sub["cdf"] = (np.arange(n) + 1) / n

        # Split pass vs fail
        passing = sub[~sub["failed"]]
        failing = sub[sub["failed"]]

        color = COLORS[idx]

        # Passing points — circles
        fig.add_trace(
            go.Scatter(
                x=passing["agent_walltime_seconds"],
                y=passing["cdf"],
                mode="lines+markers",
                name=config,
                legendgroup=config,
                marker=dict(size=6, color=color, symbol="circle"),
                line=dict(color=color, width=2),
                customdata=np.column_stack(
                    [
                        passing["repo_id"].values,
                        passing["cost_usd"].fillna(0).values,
                    ]
                ),
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Time: %{x:.0f}s<br>"
                    "Cost: $%{customdata[1]:.3f}<br>"
                    "CDF: %{y:.0%}<br>"
                    "<extra>" + config + "</extra>"
                ),
            )
        )

        # Failing points — red X
        if not failing.empty:
            fig.add_trace(
                go.Scatter(
                    x=failing["agent_walltime_seconds"],
                    y=failing["cdf"],
                    mode="markers",
                    name=f"{config} (fail)",
                    legendgroup=config,
                    showlegend=False,
                    marker=dict(
                        size=10, color="#ef4444", symbol="x", line=dict(width=2, color="#ef4444")
                    ),
                    customdata=np.column_stack(
                        [
                            failing["repo_id"].values,
                            failing["cost_usd"].fillna(0).values,
                        ]
                    ),
                    hovertemplate=(
                        "<b>%{customdata[0]}</b> ✕ FAIL<br>"
                        "Time: %{x:.0f}s<br>"
                        "Cost: $%{customdata[1]:.3f}<br>"
                        "CDF: %{y:.0%}<br>"
                        "<extra>" + config + "</extra>"
                    ),
                )
            )

    fig.update_layout(
        title="CDF — Agent Wall-clock Time by Codex Config",
        xaxis_title="Agent walltime (seconds)",
        yaxis_title="CDF",
        yaxis_tickformat=".0%",
        template="plotly_dark",
        height=600,
        hovermode="closest",
        legend=dict(font=dict(size=11)),
    )

    mo.Html(f"""
    <div id="time-cdf-plot">{mo.as_html(fig).text}</div>
    <script>
    // Cross-highlight: hovering a point highlights same repo_id across all traces
    (function() {{
        const el = document.querySelector('#time-cdf-plot .js-plotly-plot');
        if (!el) return;
        el.on('plotly_hover', function(data) {{
            const pt = data.points[0];
            if (!pt.customdata) return;
            const repo = pt.customdata[0];
            // Find all points with same repo across traces
            const updates = [];
            for (let i = 0; i < el.data.length; i++) {{
                const cd = el.data[i].customdata;
                if (!cd) continue;
                const sizes = cd.map(r => r[0] === repo ? 14 : (el.data[i].marker.size || 6));
                updates.push({{ 'marker.size': [sizes] }});
            }}
            // Batch restyle
            const traceIndices = [];
            const sizeArrays = [];
            for (let i = 0; i < el.data.length; i++) {{
                const cd = el.data[i].customdata;
                if (!cd) continue;
                const baseSize = el.data[i].marker.symbol === 'x' ? 10 : 6;
                const sizes = cd.map(r => r[0] === repo ? baseSize + 8 : baseSize);
                traceIndices.push(i);
                sizeArrays.push(sizes);
            }}
            if (traceIndices.length > 0) {{
                Plotly.restyle(el, {{ 'marker.size': sizeArrays }}, traceIndices);
            }}
        }});
        el.on('plotly_unhover', function() {{
            const traceIndices = [];
            const sizeArrays = [];
            for (let i = 0; i < el.data.length; i++) {{
                const cd = el.data[i].customdata;
                if (!cd) continue;
                const baseSize = el.data[i].marker.symbol === 'x' ? 10 : 6;
                traceIndices.push(i);
                sizeArrays.push(cd.map(() => baseSize));
            }}
            if (traceIndices.length > 0) {{
                Plotly.restyle(el, {{ 'marker.size': sizeArrays }}, traceIndices);
            }}
        }});
    }})();
    </script>
    """)
    return


@app.cell
def _(df, mo, pl):
    # Summary stats table
    _stats = (
        df.group_by("config_name")
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
    return (_stats,)


@app.cell
def _(_stats, mo):
    mo.ui.table(_stats, selection=None)
    return


if __name__ == "__main__":
    app.run()
