#!/usr/bin/env python3
"""Generate self-contained HTML pages with CDF plots of eval metrics.

The core ``build_cdf_figure`` function is generic — pass any pandas DataFrame
with a ``config_name`` column plus an x-axis metric column and it will produce
a Plotly CDF with cross-trace repo highlighting and red ✕ markers for failures.

Usage (CLI, backwards-compatible)::

    uv run python evals/eda/time_cdf_plot.py                        # writes time_cdf.html
    uv run python evals/eda/time_cdf_plot.py -o /tmp/my_plot.html
    uv run python evals/eda/time_cdf_plot.py --parquet /path/to.parquet
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import polars as pl

CODEX_CONFIGS: list[str] = [
    "codex-gpt-5.3-reasoning_xhigh",
    "codex-gpt-5.3",
    "codex-gpt-5.3-reasoning_medium",
    "codex-mini-gpt-5.1",
    "codex-gpt-5.3-no_agents_md",
    "codex-gpt-5.3-no_agents_md-no_guardrail",
]

CONFIG_COLORS: dict[str, str] = {
    "codex-gpt-5.3-reasoning_xhigh": "#636EFA",
    "codex-gpt-5.3": "#00CC96",
    "codex-gpt-5.3-reasoning_medium": "#AB63FA",
    "codex-mini-gpt-5.1": "#EF553B",
    "codex-gpt-5.3-no_agents_md": "#FFA15A",
    "codex-gpt-5.3-no_agents_md-no_guardrail": "#FF6692",
}

DEFAULT_PARQUET: Path = Path.home() / "keystone_eval" / "2026-03-14.parquet"
PLOT_DIV_ID: str = "time-cdf-plot"
PLOTLY_CDN_VERSION: str = "3.3.1"

# JS injected after the Plotly div to enable cross-trace repo highlighting on hover.
CROSS_HIGHLIGHT_JS: str = """
<script>
(function() {
    var el = document.getElementById('__PLOT_DIV_ID__');
    if (!el) return;
    var attempts = 0;
    var timer = setInterval(function() {
        if (el.data && el.data.length > 0) {
            clearInterval(timer);
            attach(el);
        }
        if (++attempts > 100) clearInterval(timer);
    }, 100);

    function attach(el) {
        // Freeze axis ranges so marker size changes don't trigger autorange jitter
        var xRange = el.layout.xaxis.range.slice();
        var yRange = el.layout.yaxis.range.slice();
        Plotly.relayout(el, {
            'xaxis.autorange': false, 'xaxis.range': xRange,
            'yaxis.autorange': false, 'yaxis.range': yRange
        });
        el.on('plotly_hover', function(evData) {
            var pt = evData.points[0];
            if (!pt.customdata) return;
            var repo = pt.customdata[0];
            var indices = [], sizes = [];
            for (var i = 0; i < el.data.length; i++) {
                var cd = el.data[i].customdata;
                if (!cd) continue;
                var base = el.data[i].marker.symbol === 'x' ? 10 : 6;
                indices.push(i);
                sizes.push(cd.map(function(r) { return r[0] === repo ? base + 8 : base; }));
            }
            if (indices.length) Plotly.restyle(el, {'marker.size': sizes}, indices);
        });
        el.on('plotly_unhover', function() {
            var indices = [], sizes = [];
            for (var i = 0; i < el.data.length; i++) {
                var cd = el.data[i].customdata;
                if (!cd) continue;
                var base = el.data[i].marker.symbol === 'x' ? 10 : 6;
                indices.push(i);
                sizes.push(cd.map(function() { return base; }));
            }
            if (indices.length) Plotly.restyle(el, {'marker.size': sizes}, indices);
        });
    }
})();
</script>
"""


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_codex_data(parquet_path: Path) -> pd.DataFrame:
    """Load parquet and return a pandas DataFrame filtered to codex configs.

    The returned frame always contains: ``config_name``, ``repo_id``,
    ``agent_walltime_seconds``, ``cost_usd``, ``success``, ``agent_timed_out``,
    and a derived ``failed`` column.
    """
    df = pl.read_parquet(parquet_path)
    df = df.filter(pl.col("config_name").is_in(CODEX_CONFIGS))
    pdf = df.select(
        "config_name",
        "repo_id",
        "agent_walltime_seconds",
        "cost_usd",
        "success",
        "agent_timed_out",
    ).to_pandas()
    pdf["failed"] = ~pdf["success"] | pdf["agent_timed_out"].fillna(False)
    return pdf


# ---------------------------------------------------------------------------
# Generic CDF figure builder
# ---------------------------------------------------------------------------
def build_cdf_figure(
    pdf: pd.DataFrame,
    x_col: str,
    *,
    title: str,
    x_label: str,
    x_format: str = "",
    hover_extra_cols: dict[str, str] | None = None,
    config_order: Sequence[str] | None = None,
    config_colors: dict[str, str] | None = None,
    height: int = 600,
) -> go.Figure:
    """Build a Plotly CDF figure from *pdf* over *x_col*, grouped by ``config_name``.

    Parameters
    ----------
    pdf:
        DataFrame with at least ``config_name``, ``repo_id``, ``failed``, and *x_col*.
    x_col:
        Column to plot on the x-axis.
    title:
        Plot title.
    x_label:
        X-axis label.
    x_format:
        d3 tick format for the x-axis (e.g. ``"$,.2f"`` for dollars).
    hover_extra_cols:
        Mapping of ``{column_name: display_label}`` for extra columns to show in
        the hover tooltip.  Each column must exist in *pdf*.
    config_order:
        Ordered list of ``config_name`` values to include.  Defaults to
        :data:`CODEX_CONFIGS`.
    config_colors:
        Mapping of ``config_name`` → CSS color.  Defaults to :data:`CONFIG_COLORS`.
    height:
        Figure height in pixels.
    """
    if config_order is None:
        config_order = CODEX_CONFIGS
    if config_colors is None:
        config_colors = CONFIG_COLORS
    if hover_extra_cols is None:
        hover_extra_cols = {}

    fig = go.Figure()

    for config in config_order:
        sub = pdf[pdf["config_name"] == config].copy()
        if sub.empty:
            continue
        sub = sub.sort_values(x_col).reset_index(drop=True)
        n = len(sub)
        sub["cdf"] = (np.arange(n) + 1) / n
        color = config_colors.get(config, "#888888")

        passing = sub[~sub["failed"]]
        failing = sub[sub["failed"]]

        # Build customdata: [repo_id, extra1, extra2, ...]
        extra_keys = list(hover_extra_cols.keys())
        customdata_cols = [passing["repo_id"].values] + [
            passing[c].fillna(0).values for c in extra_keys
        ]
        customdata_pass = np.column_stack(customdata_cols) if customdata_cols else None

        # Build hover template
        hover_lines = [f"<b>%{{customdata[0]}}</b><br>{x_label}: %{{x}}"]
        for i, (_col, label) in enumerate(hover_extra_cols.items(), start=1):
            hover_lines.append(f"{label}: %{{customdata[{i}]}}")
        hover_lines.append("CDF: %{y:.0%}")
        hover_template = "<br>".join(hover_lines) + f"<extra>{config}</extra>"

        fig.add_trace(
            go.Scatter(
                x=passing[x_col],
                y=passing["cdf"],
                mode="lines+markers",
                name=config,
                legendgroup=config,
                marker=dict(size=6, color=color, symbol="circle"),
                line=dict(color=color, width=2),
                customdata=customdata_pass,
                hovertemplate=hover_template,
            )
        )

        if not failing.empty:
            customdata_fail_cols = [failing["repo_id"].values] + [
                failing[c].fillna(0).values for c in extra_keys
            ]
            customdata_fail = np.column_stack(customdata_fail_cols)

            hover_template_fail = (
                hover_template.replace("<b>%{customdata[0]}</b>", "<b>%{customdata[0]}</b> ✕ FAIL")
            )

            fig.add_trace(
                go.Scatter(
                    x=failing[x_col],
                    y=failing["cdf"],
                    mode="markers",
                    name=f"{config} (fail)",
                    legendgroup=config,
                    showlegend=False,
                    marker=dict(
                        size=10,
                        color="#ef4444",
                        symbol="x",
                        line=dict(width=2, color="#ef4444"),
                    ),
                    customdata=customdata_fail,
                    hovertemplate=hover_template_fail,
                )
            )

    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title="CDF",
        yaxis_tickformat=".0%",
        template="plotly_dark",
        height=height,
        hovermode="closest",
        legend=dict(font=dict(size=11)),
    )
    if x_format:
        fig.update_layout(xaxis_tickformat=x_format)
    return fig


# ---------------------------------------------------------------------------
# Convenience wrappers for specific plots
# ---------------------------------------------------------------------------
def build_figure(pdf: pd.DataFrame) -> go.Figure:
    """Build the walltime CDF figure (backwards-compatible wrapper)."""
    return build_cdf_figure(
        pdf,
        "agent_walltime_seconds",
        title="CDF — Agent Wall-clock Time by Codex Config",
        x_label="Agent walltime (seconds)",
        hover_extra_cols={"cost_usd": "Cost: $"},
    )


def build_cost_figure(pdf: pd.DataFrame) -> go.Figure:
    """Build the inference cost CDF figure."""
    return build_cdf_figure(
        pdf,
        "cost_usd",
        title="CDF — Inference Cost by Codex Config",
        x_label="Inference cost (USD)",
        x_format="$,.2f",
        hover_extra_cols={"agent_walltime_seconds": "Time"},
    )


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------
def export_html(
    fig: go.Figure,
    output_path: Path,
    *,
    div_id: str = PLOT_DIV_ID,
) -> None:
    """Write a self-contained HTML file with the Plotly figure and cross-highlight JS."""
    plot_html = fig.to_html(
        full_html=True,
        include_plotlyjs="cdn",
        div_id=div_id,
    )
    # Pin the CDN version for reproducibility
    plot_html = re.sub(
        r"https://cdn\.plot\.ly/plotly-[^\"]+\.min\.js",
        f"https://cdn.plot.ly/plotly-{PLOTLY_CDN_VERSION}.min.js",
        plot_html,
    )
    # Inject the cross-highlight script right before </body>
    js = CROSS_HIGHLIGHT_JS.replace("__PLOT_DIV_ID__", div_id)
    plot_html = plot_html.replace("</body>", f"{js}\n</body>")
    output_path.write_text(plot_html)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export time CDF plot as self-contained HTML")
    parser.add_argument(
        "--parquet",
        type=Path,
        default=DEFAULT_PARQUET,
        help="Path to the eval parquet file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("time_cdf.html"),
        help="Output HTML path (default: time_cdf.html)",
    )
    args = parser.parse_args()

    pdf = load_codex_data(args.parquet)
    fig = build_figure(pdf)
    export_html(fig, args.output)
    print(f"Wrote {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
