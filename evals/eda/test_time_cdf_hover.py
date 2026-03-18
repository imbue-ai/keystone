"""Playwright test: cross-highlight hover on the time CDF plot.

Run::

    uv run pytest evals/eda/test_time_cdf_hover.py -v

Requires: ``uv run playwright install chromium`` (one-time setup).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import Page

SCRIPT = Path(__file__).parent / "time_cdf_plot.py"
PARQUET = Path.home() / "keystone_eval" / "2026-03-14.parquet"
PLOT_DIV_ID = "time-cdf-plot"


@pytest.fixture(scope="module")
def html_path() -> Path:
    """Generate the HTML file once for all tests in this module."""
    out = Path(tempfile.mkdtemp()) / "time_cdf.html"
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPT), "--parquet", str(PARQUET), "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Generation failed:\n{result.stderr}"
    assert out.exists()
    return out


def _wait_for_plotly(page: Page) -> None:
    """Wait until Plotly has rendered traces on the plot div."""
    page.wait_for_function(
        f"""() => {{
            var el = document.getElementById('{PLOT_DIV_ID}');
            return el && el.data && el.data.length > 0;
        }}""",
        timeout=15_000,
    )
    # Let the cross-highlight setInterval attach handlers
    page.wait_for_timeout(500)


def test_plotly_renders_with_expected_traces(page: Page, html_path: Path) -> None:
    """The plot should have at least 6 traces (one per config, plus fail traces)."""
    page.goto(f"file://{html_path}")
    _wait_for_plotly(page)

    trace_count: int = page.evaluate(
        f"() => document.getElementById('{PLOT_DIV_ID}').data.length"
    )
    # 6 configs → 6 pass traces + up to 6 fail traces
    assert trace_count >= 6, f"Expected ≥6 traces, got {trace_count}"


def test_cdn_version_is_pinned(html_path: Path) -> None:
    """The HTML should load Plotly from a pinned CDN URL, not 'latest'."""
    html = html_path.read_text()
    assert "plotly-3.3.1.min.js" in html
    assert "plotly-latest" not in html


def test_cross_highlight_enlarges_markers(page: Page, html_path: Path) -> None:
    """Hovering a point should enlarge same-repo markers on other traces."""
    page.goto(f"file://{html_path}")
    _wait_for_plotly(page)

    # Pick the repo_id of the first point on the first trace that has customdata
    info: dict = page.evaluate(f"""() => {{
        var el = document.getElementById('{PLOT_DIV_ID}');
        for (var i = 0; i < el.data.length; i++) {{
            var cd = el.data[i].customdata;
            if (cd && cd.length > 0) return {{ traceIndex: i, repo: cd[0][0] }};
        }}
        return null;
    }}""")
    assert info is not None, "No trace with customdata found"

    trace_idx = info["traceIndex"]
    repo = info["repo"]

    # Trigger hover by emitting the plotly_hover event directly
    # (Plotly.Fx.hover shows the tooltip but does NOT fire the plotly_hover event)
    page.evaluate(f"""() => {{
        var el = document.getElementById('{PLOT_DIV_ID}');
        var cd = el.data[{trace_idx}].customdata[0];
        el.emit('plotly_hover', {{points: [{{
            curveNumber: {trace_idx}, pointNumber: 0, customdata: cd
        }}]}});
    }}""")
    page.wait_for_timeout(300)

    # Check that at least one OTHER trace has enlarged markers for this repo
    enlarged: bool = page.evaluate(f"""() => {{
        var el = document.getElementById('{PLOT_DIV_ID}');
        for (var i = 0; i < el.data.length; i++) {{
            if (i === {trace_idx}) continue;
            var cd = el.data[i].customdata;
            if (!cd) continue;
            var sizes = el.data[i].marker.size;
            if (!Array.isArray(sizes)) continue;
            var base = el.data[i].marker.symbol === 'x' ? 10 : 6;
            for (var j = 0; j < cd.length; j++) {{
                if (cd[j][0] === '{repo}' && sizes[j] > base) return true;
            }}
        }}
        return false;
    }}""")
    assert enlarged, (
        f"Hovering repo '{repo}' on trace {trace_idx} did not enlarge markers on other traces"
    )


def test_unhover_resets_markers(page: Page, html_path: Path) -> None:
    """After unhover, all markers should return to their base sizes."""
    page.goto(f"file://{html_path}")
    _wait_for_plotly(page)

    # Hover then unhover via event emission
    page.evaluate(f"""() => {{
        var el = document.getElementById('{PLOT_DIV_ID}');
        var cd = el.data[0].customdata[0];
        el.emit('plotly_hover', {{points: [{{
            curveNumber: 0, pointNumber: 0, customdata: cd
        }}]}});
    }}""")
    page.wait_for_timeout(300)
    page.evaluate(f"""() => {{
        var el = document.getElementById('{PLOT_DIV_ID}');
        el.emit('plotly_unhover', {{}});
    }}""")
    page.wait_for_timeout(300)

    # All traces should have uniform marker sizes
    uniform: bool = page.evaluate(f"""() => {{
        var el = document.getElementById('{PLOT_DIV_ID}');
        for (var i = 0; i < el.data.length; i++) {{
            var sizes = el.data[i].marker.size;
            if (!Array.isArray(sizes)) continue;
            var s0 = sizes[0];
            for (var j = 1; j < sizes.length; j++) {{
                if (sizes[j] !== s0) return false;
            }}
        }}
        return true;
    }}""")
    assert uniform, "Marker sizes were not uniform after unhover"
