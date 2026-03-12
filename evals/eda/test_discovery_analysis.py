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
        """
    )
    return (mo,)


@app.cell
def _(mo):
    import polars as pl
    import json
    from pathlib import Path

    PARQUET_PATH = Path.home() / "keystone_eval" / "2026-03-11_cat_v8.parquet"
    df = pl.read_parquet(PARQUET_PATH)
    mo.md(f"Loaded **{len(df)}** rows from `{PARQUET_PATH.name}`")
    return df, json, pl, Path, PARQUET_PATH


@app.cell
def _(df, json, pl):
    # Extract test names from raw_json for every row
    def _extract_test_names(raw_json: str) -> list[str]:
        data = json.loads(raw_json)
        br = data.get("bootstrap_result")
        if br is None:
            return []
        v = br.get("verification")
        if v is None:
            return []
        return [t["name"] for t in (v.get("test_results") or [])]

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
                "tests_discovered": len(set(_names)),
                "test_names": _names,
            }
        )

    tests_df = pl.DataFrame(rows)
    tests_df
    return tests_df, rows


@app.cell
def _(tests_df, pl):
    # Build the universe of test names per repo
    repo_universes: dict[str, set[str]] = {}
    for _i in range(len(tests_df)):
        _row = tests_df.row(_i, named=True)
        _repo = _row["repo_id"]
        if _repo not in repo_universes:
            repo_universes[_repo] = set()
        repo_universes[_repo].update(_row["test_names"])

    universe_sizes = pl.DataFrame(
        [{"repo_id": repo, "universe_size": len(names)} for repo, names in repo_universes.items()]
    )
    universe_sizes.sort("universe_size", descending=True)
    return repo_universes, universe_sizes


@app.cell
def _(tests_df, repo_universes, pl):
    # Compute test_discovered_fraction for each row
    fractions: list[float | None] = []
    for _i in range(len(tests_df)):
        _row = tests_df.row(_i, named=True)
        _universe = repo_universes[_row["repo_id"]]
        if len(_universe) == 0:
            fractions.append(None)
        else:
            fractions.append(len(set(_row["test_names"])) / len(_universe))

    result_df = tests_df.drop("test_names").with_columns(
        pl.Series("test_discovered_fraction", fractions)
    )
    result_df.sort("test_discovered_fraction")
    return result_df, fractions


@app.cell
def _(result_df, mo):
    mo.md("## Lowest discovery fractions (potential issues)")
    return ()


@app.cell
def _(result_df):
    # Show rows with lowest discovery fraction (excluding repos with no tests at all)
    (
        result_df.filter(result_df["test_discovered_fraction"].is_not_null())
        .sort("test_discovered_fraction")
        .head(20)
    )
    return ()


@app.cell
def _(result_df, mo):
    mo.md("## CDF of `test_discovered_fraction` by model")
    return ()


@app.cell
def _(result_df, pl):
    import plotly.express as px

    plot_df = result_df.filter(pl.col("test_discovered_fraction").is_not_null()).sort(
        "test_discovered_fraction"
    )

    fig = px.ecdf(
        plot_df.to_pandas(),
        x="test_discovered_fraction",
        color="config_name",
        labels={
            "test_discovered_fraction": "Fraction of repo test universe discovered",
            "config_name": "Model",
        },
        title="CDF of Test Discovery Fraction by Model",
    )
    fig.update_layout(xaxis_tickformat=".0%")
    fig
    return fig, plot_df, px


@app.cell
def _(result_df, mo):
    mo.md("## Summary stats by model")
    return ()


@app.cell
def _(result_df, pl):
    (
        result_df.filter(pl.col("test_discovered_fraction").is_not_null())
        .group_by("config_name")
        .agg(
            pl.col("test_discovered_fraction").mean().alias("mean"),
            pl.col("test_discovered_fraction").median().alias("median"),
            pl.col("test_discovered_fraction").quantile(0.25).alias("p25"),
            pl.col("test_discovered_fraction").quantile(0.75).alias("p75"),
            pl.len().alias("n"),
        )
        .sort("mean", descending=True)
    )
    return ()


if __name__ == "__main__":
    app.run()
