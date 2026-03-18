"""Syrupy golden-file test for the time CDF HTML output.

The snapshot is stored as a plain ``.html`` file that you can open directly
in a browser to eyeball the plot.

Run::

    uv run pytest evals/eda/test_time_cdf_snapshot.py -v

Update the snapshot after intentional changes::

    uv run pytest evals/eda/test_time_cdf_snapshot.py --snapshot-update
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension

from evals.eda.time_cdf_plot import (
    DEFAULT_PARQUET,
    build_figure,
    export_html,
    load_codex_data,
)


class HTMLSnapshotExtension(SingleFileSnapshotExtension):
    """Store each snapshot as an individual ``.html`` file."""

    file_extension = "html"


@pytest.fixture()
def snapshot_html(snapshot: object) -> object:
    """Provide a snapshot fixture that writes ``.html`` golden files."""
    return snapshot.use_extension(HTMLSnapshotExtension)


def test_time_cdf_html_snapshot(snapshot_html: object) -> None:
    """The generated HTML should match the golden snapshot."""
    pdf = load_codex_data(DEFAULT_PARQUET)
    fig = build_figure(pdf)

    # Write to a temp file so export_html can do its work, then read back
    tmp = Path(tempfile.mktemp(suffix=".html"))
    try:
        export_html(fig, tmp)
        html = tmp.read_text()
    finally:
        tmp.unlink(missing_ok=True)

    assert html.encode() == snapshot_html
