#!/usr/bin/env python3
"""Dump per-repo eval results into a directory structure for analysis.

Reads a Keystone eval parquet file and writes one directory per
(repo_id, config_name, trial_index) containing:
  - passing_tests.txt  (one test name per line, only passing tests)
  - Dockerfile
  - run_all_tests.sh

Usage:
    uv run python evals/scripts/dump_per_repo_results.py
    uv run python evals/scripts/dump_per_repo_results.py /path/to/results.parquet
    uv run python evals/scripts/dump_per_repo_results.py --output-dir /tmp/my_output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

_DEFAULT_PARQUET = Path.home() / "keystone_eval" / "blog.parquet"
_DEFAULT_OUTPUT = Path("/tmp/keystone/per_repo_results")


def _extract_passing_tests(raw_json: str) -> list[str]:
    """Return sorted list of passing test names from raw_json."""
    data = json.loads(raw_json)
    bootstrap = data.get("bootstrap_result") or {}
    verification = bootstrap.get("verification") or {}
    test_results: list[dict[str, object]] = verification.get("test_results") or []
    return sorted(str(t.get("name", "")) for t in test_results if t.get("passed") is True)


def _extract_generated_file(raw_json: str, field: str) -> str | None:
    """Extract a generated file (dockerfile, run_all_tests_sh) from raw_json."""
    data = json.loads(raw_json)
    bootstrap = data.get("bootstrap_result") or {}
    generated = bootstrap.get("generated_files") or {}
    return generated.get(field)


def dump_results(parquet_path: Path, output_dir: Path) -> None:
    """Read parquet and dump per-repo results."""
    print(f"Reading {parquet_path}")
    df = pl.read_parquet(parquet_path)

    required_cols = {"repo_id", "config_name", "trial_index", "raw_json"}
    missing = required_cols - set(df.columns)
    if missing:
        print(f"Error: parquet missing columns: {missing}", file=sys.stderr)
        sys.exit(1)

    total_rows = len(df)
    written = 0
    skipped = 0

    for row in df.iter_rows(named=True):
        repo_id: str = row["repo_id"]
        config_name: str = row["config_name"] or "unknown"
        trial_index: int = row["trial_index"] if row["trial_index"] is not None else 0
        raw_json: str | None = row.get("raw_json")

        if not raw_json:
            skipped += 1
            continue

        trial_dir = output_dir / repo_id / config_name / f"trial_{trial_index}"
        trial_dir.mkdir(parents=True, exist_ok=True)

        # Write passing tests
        passing = _extract_passing_tests(raw_json)
        (trial_dir / "passing_tests.txt").write_text("\n".join(passing) + ("\n" if passing else ""))

        # Write Dockerfile
        dockerfile = _extract_generated_file(raw_json, "dockerfile")
        if dockerfile:
            (trial_dir / "Dockerfile").write_text(dockerfile)

        # Write run_all_tests.sh
        run_script = _extract_generated_file(raw_json, "run_all_tests_sh")
        if run_script:
            (trial_dir / "run_all_tests.sh").write_text(run_script)

        written += 1

    print(f"Processed {total_rows} rows: {written} written, {skipped} skipped")
    print(f"Output: {output_dir}")

    # Print summary of repos
    repo_ids = sorted(df["repo_id"].unique().to_list())
    print(f"Repos ({len(repo_ids)}): {', '.join(repo_ids)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump per-repo eval results into a directory structure.",
    )
    parser.add_argument(
        "parquet",
        nargs="?",
        type=Path,
        default=_DEFAULT_PARQUET,
        help=f"Path to parquet file (default: {_DEFAULT_PARQUET})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output directory (default: {_DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if not args.parquet.exists():
        print(f"Error: parquet file not found: {args.parquet}", file=sys.stderr)
        sys.exit(1)

    dump_results(args.parquet, args.output_dir)


if __name__ == "__main__":
    main()
