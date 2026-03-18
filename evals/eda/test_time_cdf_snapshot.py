"""Syrupy golden-file test + Playwright interaction tests for the time CDF plot.

The snapshot is stored as a plain ``.html`` file that you can open directly
in a browser to eyeball the plot.  The HTML is generated once per session and
reused for both the snapshot assertion and all Playwright tests.

Run::

    uv run pytest evals/eda/test_time_cdf_snapshot.py -v

Update the snapshot after intentional changes::

    uv run pytest evals/eda/test_time_cdf_snapshot.py --snapshot-update

Requires: ``uv run playwright install chromium`` (one-time setup).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import Page
from syrupy.extensions.single_file import SingleFileSnapshotExtension

from evals.eda.cdf_plot import (
    DEFAULT_PARQUET,
    PLOT_DIV_ID,
    build_figure,
    export_html,
    load_codex_data,
)


class HTMLSnapshotExtension(SingleFileSnapshotExtension):
    """Store each snapshot as an individual ``.html`` file."""

    file_extension = "html"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def generated_html() -> tuple[str, Path]:
    """Generate the HTML once and write to a temp file for Playwright.

    Returns (html_string, path_to_temp_file).
    """
    pdf = load_codex_data(DEFAULT_PARQUET)
    fig = build_figure(pdf)
    tmp = Path(tempfile.mkdtemp()) / "time_cdf.html"
    export_html(fig, tmp)
    html = tmp.read_text()
    return html, tmp


@pytest.fixture()
def snapshot_html(snapshot: object) -> object:
    """Provide a snapshot fixture that writes ``.html`` golden files."""
    return snapshot.use_extension(HTMLSnapshotExtension)


# ---------------------------------------------------------------------------
# Golden snapshot test
# ---------------------------------------------------------------------------
def test_time_cdf_html_snapshot(
    generated_html: tuple[str, Path], snapshot_html: object
) -> None:
    """The generated HTML should match the golden snapshot."""
    html, _ = generated_html
    assert html.encode() == snapshot_html


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------
def _open_and_wait(page: Page, html_path: Path) -> None:
    """Navigate to the HTML file and wait for Plotly to render."""
    page.goto(f"file://{html_path}")
    page.wait_for_function(
        f"""() => {{
            var el = document.getElementById('{PLOT_DIV_ID}');
            return el && el.data && el.data.length > 0;
        }}""",
        timeout=15_000,
    )
    # Let the cross-highlight setInterval attach handlers
    page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# Playwright tests (all load the same generated HTML)
# ---------------------------------------------------------------------------
def test_plotly_renders_with_expected_traces(
    page: Page, generated_html: tuple[str, Path]
) -> None:
    """The plot should have at least 6 traces (one per config, plus fail traces)."""
    _, html_path = generated_html
    _open_and_wait(page, html_path)

    trace_count: int = page.evaluate(
        f"() => document.getElementById('{PLOT_DIV_ID}').data.length"
    )
    assert trace_count >= 6, f"Expected ≥6 traces, got {trace_count}"


def test_cdn_version_is_pinned(generated_html: tuple[str, Path]) -> None:
    """The HTML should load Plotly from a pinned CDN URL, not 'latest'."""
    html, _ = generated_html
    assert "plotly-3.3.1.min.js" in html
    assert "plotly-latest" not in html


def test_cross_highlight_enlarges_markers(
    page: Page, generated_html: tuple[str, Path]
) -> None:
    """Hovering a point should enlarge same-repo markers on other traces."""
    _, html_path = generated_html
    _open_and_wait(page, html_path)

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

    # Emit plotly_hover directly (Plotly.Fx.hover does NOT fire the event)
    page.evaluate(f"""() => {{
        var el = document.getElementById('{PLOT_DIV_ID}');
        var cd = el.data[{trace_idx}].customdata[0];
        el.emit('plotly_hover', {{points: [{{
            curveNumber: {trace_idx}, pointNumber: 0, customdata: cd
        }}]}});
    }}""")
    page.wait_for_timeout(300)

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
        f"Hovering repo '{repo}' on trace {trace_idx} "
        "did not enlarge markers on other traces"
    )


def test_unhover_resets_markers(
    page: Page, generated_html: tuple[str, Path]
) -> None:
    """After unhover, all markers should return to their base sizes."""
    _, html_path = generated_html
    _open_and_wait(page, html_path)

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
