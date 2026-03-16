"""Test Discovery Analysis — marimo notebook.

Run interactively::

    uv run marimo edit evals/eda/test_discovery_analysis.py

Render to static HTML::

    uv run marimo export html evals/eda/test_discovery_analysis.py -o test_discovery_analysis.html
"""

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    mo.md(
        """
        # Test Discovery Analysis

        For each repo, we compute the *universe* of test names discovered across
        **all** configs and trials. Then for each individual run we measure what
        fraction of that universe it discovered (`test_discovered_fraction`).

        Different models produce JUnit XML with different name formats for the
        same test (e.g. `test_get::test_foo (mod.Class.test_foo)` vs
        `mod.Class::test_foo`). We normalize names to `ClassName::method` to
        avoid inflating the universe. Both raw and normalized fractions are shown.

        **Contents:**

        1. [Lowest discovery fractions](#lowest-discovery-fractions)
        2. [CDF by model](#cdf-by-model)
        3. [Summary stats by model](#summary-stats)
        """
    )
    return (mo,)


@app.cell
def _(mo):
    import polars as pl
    import json
    from pathlib import Path

    PARQUET_PATH = Path.home() / "keystone_eval" / "2026-03-14.parquet"
    df = pl.read_parquet(PARQUET_PATH)
    mo.md(f"Loaded **{len(df)}** rows from `{PARQUET_PATH.name}`")
    return df, json, pl


@app.cell
def _(df, json, mo, pl):
    import re

    def _extract_test_names(raw_json: str) -> list[str]:
        data = json.loads(raw_json)
        br = data.get("bootstrap_result")
        if br is None:
            return []
        v = br.get("verification")
        if v is None:
            return []
        return [t["name"] for t in (v.get("test_results") or [])]

    def _normalize_test_name(name: str) -> str:
        """Normalize JUnit test names across different XML formats.

        Models produce different formats for the same test, e.g.:
          - 'test_get::test_foo (test_get.GetTest.test_foo)'
          - 'test.test_get.GetTest::test_foo'
        We canonicalize to 'ClassName::method' (or just 'method' when no class).
        """
        # Format: '... (module.Class.method)' — extract class::method from parens
        _m = re.search(r"\(([^)]+)\)$", name)
        if _m:
            _parts = _m.group(1).split(".")
            if len(_parts) >= 2:
                return f"{_parts[-2]}::{_parts[-1]}"
            return _parts[-1]
        # Format: 'module.Class::method' — find class in prefix
        _pieces = name.split("::")
        if len(_pieces) >= 2:
            _prefix_parts = _pieces[0].split(".")
            _method = _pieces[-1]
            for _p in reversed(_prefix_parts):
                if _p and _p[0].isupper():
                    return f"{_p}::{_method}"
            return f"{_prefix_parts[-1]}::{_method}"
        return name

    rows: list[dict[str, object]] = []
    for _i in range(len(df)):
        _row = df.row(_i, named=True)
        _names = _extract_test_names(_row["raw_json"])
        rows.append(
            {
                "config_name": _row["config_name"],
                "repo_id": _row["repo_id"],
                "trial_index": _row["trial_index"],
                "success": _row["success"],
                "tests_discovered_raw": len(set(_names)),
                "test_names_raw": _names,
                "test_names_normalized": [_normalize_test_name(n) for n in _names],
            }
        )

    tests_df = pl.DataFrame(rows)
    mo.ui.table(tests_df.drop("test_names_raw", "test_names_normalized"), selection=None)
    return (tests_df,)


@app.cell
def _(tests_df):
    # Build the universe of test names per repo (both raw and normalized)
    _raw_universes: dict[str, set[str]] = {}
    _norm_universes: dict[str, set[str]] = {}
    for _i in range(len(tests_df)):
        _row = tests_df.row(_i, named=True)
        _repo = _row["repo_id"]
        if _repo not in _raw_universes:
            _raw_universes[_repo] = set()
            _norm_universes[_repo] = set()
        _raw_universes[_repo].update(_row["test_names_raw"])
        _norm_universes[_repo].update(_row["test_names_normalized"])

    repo_universes = {"raw": _raw_universes, "normalized": _norm_universes}
    return (repo_universes,)


@app.cell
def _(pl, repo_universes, tests_df):
    # Compute test_discovered_fraction for each row (raw and normalized)
    _raw_fracs: list[float | None] = []
    _norm_fracs: list[float | None] = []
    for _i in range(len(tests_df)):
        _row = tests_df.row(_i, named=True)
        _rid = _row["repo_id"]

        _raw_u = repo_universes["raw"][_rid]
        if len(_raw_u) == 0:
            _raw_fracs.append(None)
        else:
            _raw_fracs.append(len(set(_row["test_names_raw"])) / len(_raw_u))

        _norm_u = repo_universes["normalized"][_rid]
        if len(_norm_u) == 0:
            _norm_fracs.append(None)
        else:
            _norm_fracs.append(len(set(_row["test_names_normalized"])) / len(_norm_u))

    result_df = tests_df.drop("test_names_raw", "test_names_normalized").with_columns(
        pl.Series("frac_raw", _raw_fracs),
        pl.Series("frac_normalized", _norm_fracs),
    )
    result_df.sort("frac_normalized")
    return (result_df,)


@app.cell
def _(mo):
    mo.md("""
    <h2 id="lowest-discovery-fractions">Lowest discovery fractions (potential issues)</h2>
    """)
    return


@app.cell
def _(mo, result_df):
    # Show rows with lowest normalized discovery fraction
    _low = (
        result_df.filter(result_df["frac_normalized"].is_not_null())
        .sort("frac_normalized")
        .head(20)
    )
    mo.ui.table(_low, selection=None)
    return


@app.cell
def _(mo):
    mo.md("""
    <h2 id="cdf-by-model">CDF of <code>test_discovered_fraction</code> by model</h2>
    """)
    return


@app.cell
def _(mo, pl, result_df):
    import plotly.express as px

    _plot_df = (
        result_df.filter(pl.col("frac_normalized").is_not_null())
        .sort("frac_normalized")
        .to_pandas()
    )

    fig_raw = px.ecdf(
        _plot_df,
        x="frac_raw",
        color="config_name",
        hover_data=["repo_id", "tests_discovered_raw"],
        labels={"frac_raw": "Fraction (raw names)", "config_name": "Model"},
        title="CDF — Raw test names (before normalization)",
    )
    fig_raw.update_layout(xaxis_tickformat=".0%")

    fig_norm = px.ecdf(
        _plot_df,
        x="frac_normalized",
        color="config_name",
        hover_data=["repo_id", "tests_discovered_raw"],
        labels={"frac_normalized": "Fraction (normalized names)", "config_name": "Model"},
        title="CDF — Normalized test names (after deduplication)",
    )
    fig_norm.update_layout(xaxis_tickformat=".0%")

    mo.vstack([fig_raw, fig_norm])
    return


@app.cell
def _(mo):
    mo.md("""
    <h2 id="summary-stats">Summary stats by model</h2>
    """)
    return


@app.cell
def _(mo, pl, result_df):
    _stats = (
        result_df.filter(pl.col("frac_normalized").is_not_null())
        .group_by("config_name")
        .agg(
            pl.col("frac_normalized").mean().alias("mean_normalized"),
            pl.col("frac_normalized").median().alias("median_normalized"),
            pl.col("frac_raw").mean().alias("mean_raw"),
            pl.col("frac_raw").median().alias("median_raw"),
            pl.len().alias("n"),
        )
        .sort("mean_normalized", descending=True)
    )
    mo.ui.table(_stats, selection=None)
    return


if __name__ == "__main__":
    app.run()
