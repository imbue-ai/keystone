#!/usr/bin/env python3
"""Generate a self-contained HTML page with a CDF of agent walltime by codex config.

Usage::

    uv run python evals/eda/time_cdf_plot.py                        # writes time_cdf.html
    uv run python evals/eda/time_cdf_plot.py -o /tmp/my_plot.html   # custom output
    uv run python evals/eda/time_cdf_plot.py --parquet /path/to.parquet
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import polars as pl

CODEX_CONFIGS = [
    "codex-gpt-5.3-reasoning_xhigh",
    "codex-gpt-5.3",
    "codex-gpt-5.3-reasoning_medium",
    "codex-mini-gpt-5.1",
    "codex-gpt-5.3-no_agents_md",
    "codex-gpt-5.3-no_agents_md-no_guardrail",
]

CONFIG_COLORS = {
    "codex-gpt-5.3-reasoning_xhigh": "#636EFA",
    "codex-gpt-5.3": "#00CC96",
    "codex-gpt-5.3-reasoning_medium": "#AB63FA",
    "codex-mini-gpt-5.1": "#EF553B",
    "codex-gpt-5.3-no_agents_md": "#FFA15A",
    "codex-gpt-5.3-no_agents_md-no_guardrail": "#FF6692",
}

DEFAULT_PARQUET = Path.home() / "keystone_eval" / "2026-03-14.parquet"
PLOT_DIV_ID = "time-cdf-plot"

# JS injected after the Plotly div to enable cross-trace repo highlighting on hover.
CROSS_HIGHLIGHT_JS = """
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


def load_codex_data(parquet_path: Path) -> "pd.DataFrame":  # noqa: F821
    """Load parquet and return a pandas DataFrame filtered to codex configs."""
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


def build_figure(pdf: "pd.DataFrame") -> go.Figure:  # noqa: F821
    """Build the Plotly CDF figure from the filtered dataframe."""
    fig = go.Figure()

    for config in CODEX_CONFIGS:
        sub = pdf[pdf["config_name"] == config].copy()
        if sub.empty:
            continue
        sub = sub.sort_values("agent_walltime_seconds").reset_index(drop=True)
        n = len(sub)
        sub["cdf"] = (np.arange(n) + 1) / n
        color = CONFIG_COLORS[config]

        passing = sub[~sub["failed"]]
        failing = sub[sub["failed"]]

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
                    [passing["repo_id"].values, passing["cost_usd"].fillna(0).values]
                ),
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Time: %{x:.0f}s<br>"
                    "Cost: $%{customdata[1]:.3f}<br>"
                    "CDF: %{y:.0%}<br>"
                    f"<extra>{config}</extra>"
                ),
            )
        )

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
                        size=10,
                        color="#ef4444",
                        symbol="x",
                        line=dict(width=2, color="#ef4444"),
                    ),
                    customdata=np.column_stack(
                        [failing["repo_id"].values, failing["cost_usd"].fillna(0).values]
                    ),
                    hovertemplate=(
                        "<b>%{customdata[0]}</b> ✕ FAIL<br>"
                        "Time: %{x:.0f}s<br>"
                        "Cost: $%{customdata[1]:.3f}<br>"
                        "CDF: %{y:.0%}<br>"
                        f"<extra>{config}</extra>"
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
    return fig


def export_html(fig: go.Figure, output_path: Path) -> None:
    """Write a self-contained HTML file with the Plotly figure and cross-highlight JS."""
    div_id = PLOT_DIV_ID
    plot_html = fig.to_html(
        full_html=True,
        include_plotlyjs="cdn",
        div_id=div_id,
    )
    # Pin the CDN version for reproducibility
    plot_html = re.sub(
        r"https://cdn\.plot\.ly/plotly-[^\"]+\.min\.js",
        "https://cdn.plot.ly/plotly-3.3.1.min.js",
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
