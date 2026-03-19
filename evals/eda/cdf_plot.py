#!/usr/bin/env python3
"""Generate self-contained HTML pages with CDF plots of eval metrics.

The core ``build_cdf_figure`` function is generic — pass any pandas DataFrame
with a ``config_name`` column plus an x-axis metric column and it will produce
a Plotly CDF with cross-trace repo highlighting and red ✕ markers for failures.

Usage (CLI, backwards-compatible)::

    uv run python evals/eda/cdf_plot.py                        # writes time_cdf.html
    uv run python evals/eda/cdf_plot.py -o /tmp/my_plot.html
    uv run python evals/eda/cdf_plot.py --parquet /path/to.parquet
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import polars as pl

if TYPE_CHECKING:
    from collections.abc import Sequence

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

CLAUDE_CONFIGS: list[str] = [
    "claude-opus-effort_max",
    "claude-opus",
    "claude-opus-effort_medium",
    "claude-opus-no_agents_md",
    "claude-opus-no_agents_md-no_guardrail",
    "claude-haiku",
]

CLAUDE_CONFIG_COLORS: dict[str, str] = {
    "claude-opus-effort_max": "#636EFA",
    "claude-opus": "#00CC96",
    "claude-opus-effort_medium": "#AB63FA",
    "claude-opus-no_agents_md": "#FFA15A",
    "claude-opus-no_agents_md-no_guardrail": "#FF6692",
    "claude-haiku": "#EF553B",
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
        // Cache original sizes per trace for reset
        var origSizes = el.data.map(function(t) {
            return Array.isArray(t.marker.size) ? t.marker.size.slice() : t.marker.size;
        });
        el.on('plotly_hover', function(evData) {
            var pt = evData.points[0];
            if (!pt.customdata) return;
            var repo = pt.customdata[0];
            var indices = [], sizes = [];
            for (var i = 0; i < el.data.length; i++) {
                var cd = el.data[i].customdata;
                if (!cd) continue;
                var orig = origSizes[i];
                indices.push(i);
                if (Array.isArray(orig)) {
                    sizes.push(cd.map(function(r, j) {
                        return r[0] === repo ? orig[j] + 8 : orig[j];
                    }));
                } else {
                    sizes.push(cd.map(function(r) {
                        return r[0] === repo ? orig + 8 : orig;
                    }));
                }
            }
            if (indices.length) Plotly.restyle(el, {'marker.size': sizes}, indices);
        });
        el.on('plotly_unhover', function() {
            var indices = [], sizes = [];
            for (var i = 0; i < el.data.length; i++) {
                var cd = el.data[i].customdata;
                if (!cd) continue;
                indices.push(i);
                sizes.push(Array.isArray(origSizes[i]) ? origSizes[i].slice() : origSizes[i]);
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


def load_claude_data(parquet_path: Path) -> pd.DataFrame:
    """Load parquet and return a pandas DataFrame filtered to Claude configs."""
    df = pl.read_parquet(parquet_path)
    df = df.filter(pl.col("config_name").is_in(CLAUDE_CONFIGS))
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
    logx: bool = False,
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

        # Per-point marker: red ✕ for failures, colored circle for passes
        symbols = ["x" if f else "circle" for f in sub["failed"]]
        colors = ["#ef4444" if f else color for f in sub["failed"]]
        sizes = [10 if f else 6 for f in sub["failed"]]

        # Build customdata: [repo_id, failed_flag, extra1, extra2, ...]
        extra_keys = list(hover_extra_cols.keys())
        customdata_cols = [
            sub["repo_id"].values,
            sub["failed"].astype(str).values,
        ] + [sub[c].fillna(0).values for c in extra_keys]
        customdata = np.column_stack(customdata_cols)

        # Build hover template — customdata[1] is the failed flag
        hover_lines = [
            "<b>%{customdata[0]}</b>"
            "%{customdata[1]}"  # injected via text transform below
            f"<br>{x_label}: %{{x}}"
        ]
        for i, (_col, label) in enumerate(hover_extra_cols.items(), start=2):
            hover_lines.append(f"{label}: %{{customdata[{i}]}}")
        hover_lines.append("CDF: %{y:.0%}")
        hover_template = "<br>".join(hover_lines) + f"<extra>{config}</extra>"

        # Replace the failed flag placeholder with a visible label
        # customdata[1] will be "True" or "False" — we map in the template
        hover_template = hover_template.replace(
            "%{customdata[1]}",
            "",  # we'll use a simpler approach below
        )
        # Build per-point hover text to append fail marker
        hover_texts = []
        for _, row in sub.iterrows():
            fail_label = " ✕ FAIL" if row["failed"] else ""
            x_val = f"${row[x_col]:.2f}" if "cost" in x_col else f"{row[x_col]}"
            lines = [f"<b>{row['repo_id']}</b>{fail_label}", f"{x_label}: {x_val}"]
            for _ci, (col_name, label) in enumerate(hover_extra_cols.items()):
                val = row[col_name] if pd.notna(row[col_name]) else 0
                formatted = f"{val:.2f}" if "cost" in col_name else f"{val}"
                lines.append(f"{label}{formatted}")
            lines.append(f"CDF: {row['cdf']:.0%}")
            hover_texts.append("<br>".join(lines))

        fig.add_trace(
            go.Scatter(
                x=sub[x_col],
                y=sub["cdf"],
                mode="lines+markers",
                name=config,
                legendgroup=config,
                marker={
                    "size": sizes,
                    "color": colors,
                    "symbol": symbols,
                    "line": {"width": [2 if f else 0 for f in sub["failed"]]},
                },
                line={"color": color, "width": 2},
                customdata=customdata,
                text=hover_texts,
                hovertemplate="%{text}<extra>" + config + "</extra>",
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
        legend={"font": {"size": 11}},
    )
    if x_format:
        fig.update_layout(xaxis_tickformat=x_format)
    if logx:
        fig.update_xaxes(
            type="log",
            dtick=1,
            minor={"ticks": "inside", "ticklen": 0, "showgrid": True, "dtick": "D1"},
        )
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
        logx=True,
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
        logx=True,
        hover_extra_cols={"agent_walltime_seconds": "Time"},
    )


def build_claude_figure(pdf: pd.DataFrame) -> go.Figure:
    """Build the walltime CDF figure for Claude configs."""
    return build_cdf_figure(
        pdf,
        "agent_walltime_seconds",
        title="CDF — Agent Wall-clock Time by Claude Config",
        x_label="Agent walltime (seconds)",
        logx=True,
        hover_extra_cols={"cost_usd": "Cost: $"},
        config_order=CLAUDE_CONFIGS,
        config_colors=CLAUDE_CONFIG_COLORS,
    )


def build_claude_cost_figure(pdf: pd.DataFrame) -> go.Figure:
    """Build the inference cost CDF figure for Claude configs."""
    return build_cdf_figure(
        pdf,
        "cost_usd",
        title="CDF — Inference Cost by Claude Config",
        x_label="Inference cost (USD)",
        x_format="$,.2f",
        logx=True,
        hover_extra_cols={"agent_walltime_seconds": "Time"},
        config_order=CLAUDE_CONFIGS,
        config_colors=CLAUDE_CONFIG_COLORS,
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


XHTML_TEMPLATE: str = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" \
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>__TITLE__</title>
<script src="https://cdn.plot.ly/plotly-__CDN_VERSION__.min.js">//</script>
<style type="text/css">
/*<![CDATA[*/
    body {{ margin: 0; padding: 0; background: #fff; }}
    #__DIV_ID__ {{ width: 100%; height: 100vh; }}
/*]]>*/
</style>
</head>
<body>
  <div id="__DIV_ID__">&#160;</div>
  <script type="text/javascript">
//<![CDATA[
    var data = __DATA_JSON__;
    var layout = __LAYOUT_JSON__;
    Plotly.newPlot('__DIV_ID__', data, layout, {{ responsive: true }});
__CROSS_HIGHLIGHT_JS__
//]]>
  </script>
</body>
</html>
"""

# Cross-highlight JS for XHTML (no <script> wrapper — it lives inside the CDATA block).
_XHTML_CROSS_HIGHLIGHT_JS: str = """
    (function() {
        var el = document.getElementById('__DIV_ID__');
        if (!el) return;
        var attempts = 0;
        var timer = setInterval(function() {
            if (el.data && el.data.length > 0) {
                clearInterval(timer);
                attachHL(el);
            }
            if (++attempts > 100) clearInterval(timer);
        }, 100);
        function attachHL(el) {
            var xRange = el.layout.xaxis.range.slice();
            var yRange = el.layout.yaxis.range.slice();
            Plotly.relayout(el, {
                'xaxis.autorange': false, 'xaxis.range': xRange,
                'yaxis.autorange': false, 'yaxis.range': yRange
            });
            var origSizes = el.data.map(function(t) {
                return Array.isArray(t.marker.size) ? t.marker.size.slice() : t.marker.size;
            });
            el.on('plotly_hover', function(evData) {
                var pt = evData.points[0];
                if (!pt.customdata) return;
                var repo = pt.customdata[0];
                var indices = [], sizes = [];
                for (var i = 0; i < el.data.length; i++) {
                    var cd = el.data[i].customdata;
                    if (!cd) continue;
                    var orig = origSizes[i];
                    indices.push(i);
                    if (Array.isArray(orig)) {
                        sizes.push(cd.map(function(r, j) {
                            return r[0] === repo ? orig[j] + 8 : orig[j];
                        }));
                    } else {
                        sizes.push(cd.map(function(r) {
                            return r[0] === repo ? orig + 8 : orig;
                        }));
                    }
                }
                if (indices.length) Plotly.restyle(el, {'marker.size': sizes}, indices);
            });
            el.on('plotly_unhover', function() {
                var indices = [], sizes = [];
                for (var i = 0; i < el.data.length; i++) {
                    var cd = el.data[i].customdata;
                    if (!cd) continue;
                    indices.push(i);
                    sizes.push(Array.isArray(origSizes[i]) ? origSizes[i].slice() : origSizes[i]);
                }
                if (indices.length) Plotly.restyle(el, {'marker.size': sizes}, indices);
            });
        }
    })();
"""


def export_xhtml(
    fig: go.Figure,
    output_path: Path,
    *,
    title: str = "Plot",
    div_id: str = PLOT_DIV_ID,
) -> None:
    """Write a self-contained XHTML file suitable for blog embedding."""
    data_json = json.dumps(fig.to_dict()["data"], default=str)
    layout_json = json.dumps(fig.to_dict()["layout"], default=str)
    js = _XHTML_CROSS_HIGHLIGHT_JS.replace("__DIV_ID__", div_id)
    xhtml = (
        XHTML_TEMPLATE.replace("__TITLE__", title)
        .replace("__CDN_VERSION__", PLOTLY_CDN_VERSION)
        .replace("__DIV_ID__", div_id)
        .replace("__DATA_JSON__", data_json)
        .replace("__LAYOUT_JSON__", layout_json)
        .replace("__CROSS_HIGHLIGHT_JS__", js)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xhtml)


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
