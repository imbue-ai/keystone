"""Canonical Tests Analysis — marimo notebook.

Computes the union of all deduplicated test names per repo across configs,
identifies naming-scheme partitions, and lets the user explore overlaps
via an UpSet-style visualization.

Run interactively::

    uv run marimo edit evals/eda/marimo_canonical_tests.py
"""

import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    mo.md(
        """
        # Canonical Tests Analysis

        Identifies "canonical" test names per repo by computing the union of all
        test names discovered across configs, then surfacing repos with naming
        disagreements (partition score).
        """
    )
    return (mo,)


@app.cell
def _(mo):
    """Extract test names from raw_json for every row."""
    import sys
    from collections import defaultdict
    from pathlib import Path

    import polars as pl

    _evals_root = str(Path(__file__).resolve().parents[1])
    if _evals_root not in sys.path:
        sys.path.insert(0, _evals_root)

    from eval_schema import KeystoneRepoResult

    PARQUET_PATH = Path.home() / "keystone_eval" / "blog.parquet"
    raw_df = pl.read_parquet(PARQUET_PATH).select(
        "config_name", "repo_id", "trial_index", "raw_json"
    )

    # repo_id -> "config tN" -> set of normalized test names (one entry per trial)
    repo_tests: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for row in raw_df.iter_rows(named=True):
        try:
            r = KeystoneRepoResult.model_validate_json(row["raw_json"])
            tr = r.bootstrap_result.verification.test_results
        except Exception:
            tr = None
        if not tr:
            continue
        _names: set[str] = set()
        for t in tr:
            _n = t.name[:-2] if t.name.endswith("()") else t.name
            _names.add(_n)
        _key = f"{row['config_name']} t{row['trial_index']}"
        repo_tests[row["repo_id"]][_key] |= _names

    mo.md(f"Extracted test names for **{len(repo_tests)}** repos from **{len(raw_df)}** rows.")
    return (repo_tests,)


@app.cell
def _(mo, repo_tests: dict[str, dict[str, set[str]]]):
    """Compute partition scores per repo."""
    import pandas as _pd

    _records = []
    for _repo_id, _config_map in repo_tests.items():
        # Build test_name -> set of configs
        _test_configs: dict[str, set[str]] = {}
        for _cfg, _names in _config_map.items():
            for _name in _names:
                if _name not in _test_configs:
                    _test_configs[_name] = set()
                _test_configs[_name].add(_cfg)

        _n_configs = len(_config_map)
        _total_tests = len(_test_configs)
        if _total_tests == 0 or _n_configs == 0:
            continue

        _coverages = [len(_cfgs) / _n_configs for _cfgs in _test_configs.values()]
        _mean_cov = sum(_coverages) / len(_coverages)
        _sorted_cov = sorted(_coverages)
        _median_cov = _sorted_cov[len(_sorted_cov) // 2]
        _min_cov = _sorted_cov[0]
        _partition_score = 1.0 - _mean_cov

        _core_tests = sum(1 for _c in _coverages if _c >= 0.5)
        _core_fraction = _core_tests / _total_tests if _total_tests > 0 else 0.0

        _records.append(
            {
                "repo_id": _repo_id,
                "total_tests": _total_tests,
                "n_configs": _n_configs,
                "mean_coverage": round(_mean_cov, 3),
                "median_coverage": round(_median_cov, 3),
                "min_coverage": round(_min_cov, 3),
                "partition_score": round(_partition_score, 3),
                "core_tests": _core_tests,
                "core_fraction": round(_core_fraction, 3),
            }
        )

    scores_df = _pd.DataFrame(_records).sort_values("partition_score", ascending=False)

    _n_gt_50 = (scores_df["partition_score"] > 0.5).sum()
    _n_gt_30 = (scores_df["partition_score"] > 0.3).sum()
    _n_gt_10 = (scores_df["partition_score"] > 0.1).sum()

    mo.md(
        f"## Partition Scores\n\n"
        f"- Score > 0.5: **{_n_gt_50}** repos\n"
        f"- Score > 0.3: **{_n_gt_30}** repos\n"
        f"- Score > 0.1: **{_n_gt_10}** repos\n"
    )
    return (scores_df,)


@app.cell
def _(mo, scores_df):
    """Display sortable table of partition scores."""
    scores_table = mo.ui.table(scores_df, selection="single", page_size=20)
    scores_table
    return (scores_table,)


@app.cell
def _(mo, scores_df, scores_table):
    """Repo selector — driven by table selection or dropdown."""
    repo_options = scores_df["repo_id"].tolist()
    # If user selected a row in the table, use that; otherwise default to most partitioned
    _table_val = scores_table.value
    selected_from_table = (
        _table_val.iloc[0]["repo_id"]
        if hasattr(_table_val, "__len__") and len(_table_val) > 0
        else None
    )
    default_repo = selected_from_table or repo_options[0]

    repo_dropdown = mo.ui.dropdown(
        options=repo_options,
        value=default_repo,
        label="Select repo",
    )
    repo_dropdown
    return (repo_dropdown,)


@app.cell
def _(mo, repo_dropdown, repo_tests: dict[str, dict[str, set[str]]]):
    """Build UpSet-style visualization for the selected repo."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    _selected_repo = repo_dropdown.value
    _config_map = repo_tests.get(_selected_repo, {})

    _output = None
    if not _config_map:
        _output = mo.md(f"No test data for **{_selected_repo}**.")
    else:
        # Sort configs by number of tests discovered (descending)
        _configs = sorted(_config_map.keys(), key=lambda c: len(_config_map[c]), reverse=True)

        # Compute all non-empty intersections — for efficiency, limit to
        # intersections that actually appear in the data rather than
        # enumerating all 2^N subsets.
        # Strategy: for each test, record its membership tuple.
        _test_membership: dict[str, frozenset[str]] = {}
        for _cfg, _names in _config_map.items():
            for _name in _names:
                if _name not in _test_membership:
                    _test_membership[_name] = frozenset()
                _test_membership[_name] = _test_membership[_name] | {_cfg}

        # Count how many tests have each membership pattern
        from collections import Counter

        _pattern_counts = Counter(_test_membership.values())

        # Sort by count descending, take top 30
        _top_patterns = _pattern_counts.most_common(30)

        if len(_top_patterns) == 0:
            _output = mo.md(f"No intersection patterns for **{_selected_repo}**.")
        else:
            # Build the UpSet plot: bar chart on top, dot matrix below
            _bar_labels = []
            _bar_values = []
            _dot_data: list[tuple[int, int]] = []  # (pattern_idx, config_idx)

            for _i, (_pattern, _count) in enumerate(_top_patterns):
                _bar_labels.append(str(_i))
                _bar_values.append(_count)
                for _cfg in _pattern:
                    if _cfg in _configs:
                        _dot_data.append((_i, _configs.index(_cfg)))

            _n_patterns = len(_top_patterns)
            _n_configs = len(_configs)

            _fig = make_subplots(
                rows=2,
                cols=1,
                row_heights=[0.33, 0.67],
                shared_xaxes=True,
                vertical_spacing=0.02,
            )

            # Bar chart (top)
            _fig.add_trace(
                go.Bar(
                    x=list(range(_n_patterns)),
                    y=_bar_values,
                    text=_bar_values,
                    textposition="outside",
                    marker_color="steelblue",
                    hovertext=[
                        f"{_count} tests<br>Configs: {', '.join(sorted(_pat))}"
                        for _pat, _count in _top_patterns
                    ],
                    hoverinfo="text",
                ),
                row=1,
                col=1,
            )

            # Dot matrix (bottom) — connected dots for active configs
            # First, draw grey dots for all positions
            _all_x = []
            _all_y = []
            for _i in range(_n_patterns):
                for _j in range(_n_configs):
                    _all_x.append(_i)
                    _all_y.append(_j)

            _fig.add_trace(
                go.Scatter(
                    x=_all_x,
                    y=_all_y,
                    mode="markers",
                    marker={"size": 8, "color": "lightgrey"},
                    hoverinfo="skip",
                    showlegend=False,
                ),
                row=2,
                col=1,
            )

            # Active dots (dark) and vertical lines connecting them
            for _i, (_pattern, _count) in enumerate(_top_patterns):
                _active_indices = sorted([_configs.index(_c) for _c in _pattern if _c in _configs])
                if _active_indices:
                    _fig.add_trace(
                        go.Scatter(
                            x=[_i] * len(_active_indices),
                            y=_active_indices,
                            mode="markers",
                            marker={"size": 10, "color": "black"},
                            hoverinfo="skip",
                            showlegend=False,
                        ),
                        row=2,
                        col=1,
                    )
                    # Vertical line connecting min to max active config
                    if len(_active_indices) > 1:
                        _fig.add_trace(
                            go.Scatter(
                                x=[_i, _i],
                                y=[min(_active_indices), max(_active_indices)],
                                mode="lines",
                                line={"color": "black", "width": 2},
                                hoverinfo="skip",
                                showlegend=False,
                            ),
                            row=2,
                            col=1,
                        )

            _fig.update_layout(
                title=f"UpSet Plot — {_selected_repo} (top {_n_patterns} intersections)",
                height=400 + _n_configs * 25,
                showlegend=False,
            )
            _fig.update_yaxes(
                tickvals=list(range(_n_configs)),
                ticktext=_configs,
                row=2,
                col=1,
            )
            _fig.update_yaxes(title_text="# Tests", row=1, col=1)
            _fig.update_xaxes(showticklabels=False, row=1, col=1)
            _fig.update_xaxes(showticklabels=False, row=2, col=1)

            _output = mo.ui.plotly(_fig)
    _output
    return


@app.cell
def _(mo, repo_dropdown, repo_tests: dict[str, dict[str, set[str]]]):
    """Test name detail table for the selected repo."""
    import pandas as _pd

    _selected_repo = repo_dropdown.value
    _config_map = repo_tests.get(_selected_repo, {})

    _output = None
    if not _config_map:
        _output = mo.md("No test data.")
    else:
        # Build test_name -> set of configs
        _test_configs: dict[str, set[str]] = {}
        for _cfg, _names in _config_map.items():
            for _name in _names:
                if _name not in _test_configs:
                    _test_configs[_name] = set()
                _test_configs[_name].add(_cfg)

        _n_configs = len(_config_map)
        _detail_records = [
            {
                "test_name": _name,
                "n_configs": len(_cfgs),
                "coverage": round(len(_cfgs) / _n_configs, 2),
                "configs": ", ".join(sorted(_cfgs)),
            }
            for _name, _cfgs in _test_configs.items()
        ]
        _detail_df = _pd.DataFrame(_detail_records).sort_values("n_configs", ascending=False)

        _output = mo.vstack(
            [
                mo.md(
                    f"## Test Names — {_selected_repo}\n\n"
                    f"**{len(_detail_df)}** unique tests across **{_n_configs}** configs"
                ),
                mo.ui.table(_detail_df, page_size=20),
            ]
        )
    _output
    return


@app.cell
def _(mo, repo_dropdown, repo_tests: dict[str, dict[str, set[str]]]):
    """Canonical test candidates with configurable threshold."""
    _selected_repo = repo_dropdown.value
    _config_map = repo_tests.get(_selected_repo, {})

    threshold_slider = mo.ui.slider(
        start=1,
        stop=max(len(_config_map), 1),
        value=max(len(_config_map) // 2, 1),
        label="Min configs to be canonical",
    )
    threshold_slider
    return (threshold_slider,)


@app.cell
def _(
    mo,
    repo_dropdown,
    repo_tests: dict[str, dict[str, set[str]]],
    threshold_slider,
):
    """Show canonical tests based on threshold."""
    _selected_repo = repo_dropdown.value
    _config_map = repo_tests.get(_selected_repo, {})
    _threshold = threshold_slider.value

    _output = None
    if not _config_map:
        _output = mo.md("No test data.")
    else:
        _test_configs: dict[str, set[str]] = {}
        for _cfg, _names in _config_map.items():
            for _name in _names:
                if _name not in _test_configs:
                    _test_configs[_name] = set()
                _test_configs[_name].add(_cfg)

        _canonical = {_name for _name, _cfgs in _test_configs.items() if len(_cfgs) >= _threshold}
        _total = len(_test_configs)

        _output = mo.md(
            f"## Canonical Tests — {_selected_repo}\n\n"
            f"Threshold: ≥ **{_threshold}** configs\n\n"
            f"**{len(_canonical)}** / {_total} tests "
            f"({100 * len(_canonical) / _total:.1f}%) are canonical"
        )
    _output
    return


if __name__ == "__main__":
    app.run()
