#!/usr/bin/env python3
"""Validate canonical test patterns against per-repo trial results.

For each trial, reports:
  - Which tests each regex pattern matches (ideally 1 per pattern)
  - Patterns that matched nothing (agent missed these tests)
  - Tests that no pattern matched (unaccounted tests)

Usage:
    uv run python evals/scripts/validate_canonical_tests.py \
        /tmp/keystone/per_repo_results/chat \
        evals/semi_manual_labels/chat/canonical_test_names.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _load_patterns(json_path: Path) -> list[dict[str, str | int]]:
    """Load pattern objects from canonical_test_names.json."""
    data = json.loads(json_path.read_text())
    return data.get("patterns", [])


def _load_passing_tests(trial_dir: Path) -> list[str]:
    """Load passing test names from a trial directory."""
    tests_file = trial_dir / "passing_tests.txt"
    if not tests_file.exists():
        return []
    return [line for line in tests_file.read_text().splitlines() if line.strip()]


def _find_trials(repo_dir: Path) -> list[tuple[str, Path]]:
    """Find all config/trial directories, returning (label, path) pairs."""
    trials: list[tuple[str, Path]] = []
    for config_dir in sorted(repo_dir.iterdir()):
        if not config_dir.is_dir():
            continue
        for trial_dir in sorted(config_dir.iterdir()):
            if not trial_dir.is_dir() or not trial_dir.name.startswith("trial_"):
                continue
            label = f"{config_dir.name}/{trial_dir.name}"
            trials.append((label, trial_dir))
    return trials


def validate_trial(
    tests: list[str],
    patterns: list[dict[str, str | int]],
    *,
    verbose: bool = False,
) -> tuple[int, int, int, list[str], list[str]]:
    """Validate patterns against a trial's test names.

    Returns:
        (total_tests, matched_count, unmatched_test_count,
         unmatched_pattern_regexes, unmatched_test_names)
    """
    compiled: list[tuple[dict[str, str | int], re.Pattern[str]]] = []
    for pat in patterns:
        regex_str = str(pat.get("regex", ""))
        try:
            compiled.append((pat, re.compile(regex_str)))
        except re.error as e:
            print(f"  WARNING: invalid regex {regex_str!r}: {e}", file=sys.stderr)

    # Track which tests are matched and which patterns match something
    matched_tests: set[int] = set()
    pattern_matches: dict[int, list[str]] = {i: [] for i in range(len(compiled))}

    for test_idx, test_name in enumerate(tests):
        for pat_idx, (_pat, rx) in enumerate(compiled):
            if rx.search(test_name):
                matched_tests.add(test_idx)
                pattern_matches[pat_idx].append(test_name)

    # Report per-pattern matches if verbose
    if verbose:
        for pat_idx, (pat, _rx) in enumerate(compiled):
            matches = pattern_matches[pat_idx]
            regex_str = str(pat.get("regex", ""))
            desc = str(pat.get("description", ""))
            if len(matches) == 0:
                print(f"  ❌ UNMATCHED PATTERN: {regex_str}  ({desc})")
            elif len(matches) <= 3:
                print(f"  ✓ {regex_str}  →  {matches}")
            else:
                print(f"  ✓ {regex_str}  →  {len(matches)} matches")

    unmatched_pattern_regexes = [
        str(compiled[i][0].get("regex", ""))
        for i in range(len(compiled))
        if len(pattern_matches[i]) == 0
    ]
    unmatched_test_names = [tests[i] for i in range(len(tests)) if i not in matched_tests]

    return (
        len(tests),
        len(matched_tests),
        len(unmatched_test_names),
        unmatched_pattern_regexes,
        unmatched_test_names,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate canonical test patterns against trial results.",
    )
    parser.add_argument(
        "repo_dir",
        type=Path,
        help="Path to repo results dir (e.g. /tmp/keystone/per_repo_results/chat)",
    )
    parser.add_argument(
        "json_file",
        type=Path,
        help="Path to canonical_test_names.json",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show per-pattern match details for each trial",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only show the summary table, not per-trial details",
    )
    args = parser.parse_args()

    if not args.repo_dir.is_dir():
        print(f"Error: not a directory: {args.repo_dir}", file=sys.stderr)
        sys.exit(1)
    if not args.json_file.exists():
        print(f"Error: file not found: {args.json_file}", file=sys.stderr)
        sys.exit(1)

    patterns = _load_patterns(args.json_file)
    print(f"Loaded {len(patterns)} patterns from {args.json_file}")

    trials = _find_trials(args.repo_dir)
    print(f"Found {len(trials)} trials in {args.repo_dir}\n")

    # Collect summaries for the final table
    summaries: list[tuple[str, int, int, int, int]] = []

    for label, trial_dir in trials:
        tests = _load_passing_tests(trial_dir)
        if not tests:
            if not args.summary_only:
                print(f"=== {label}: no passing tests ===\n")
            summaries.append((label, 0, 0, 0, len(patterns)))
            continue

        total, matched, unmatched_t, unmatched_p, unmatched_names = validate_trial(
            tests, patterns, verbose=args.verbose and not args.summary_only
        )

        if not args.summary_only:
            print(f"=== {label} ===")
            print(
                f"  Tests: {total}  |  Matched: {matched}  |  "
                f"Unmatched tests: {unmatched_t}  |  Unmatched patterns: {len(unmatched_p)}"
            )
            if unmatched_names:
                shown = unmatched_names[:10]
                print(f"  Unmatched tests (first {len(shown)}):")
                for name in shown:
                    print(f"    {name}")
                if len(unmatched_names) > 10:
                    print(f"    ... and {len(unmatched_names) - 10} more")
            if unmatched_p and not args.verbose:
                shown_p = unmatched_p[:10]
                print(f"  Unmatched patterns (first {len(shown_p)}):")
                for regex in shown_p:
                    print(f"    {regex}")
                if len(unmatched_p) > 10:
                    print(f"    ... and {len(unmatched_p) - 10} more")
            print()

        summaries.append((label, total, matched, unmatched_t, len(unmatched_p)))

    # Print summary table
    print("=" * 90)
    print(f"{'Trial':<50} {'Tests':>6} {'Match':>6} {'Unmat':>6} {'MissP':>6}")
    print("-" * 90)
    for label, total, matched, unmatched_t, unmatched_p in summaries:
        print(f"{label:<50} {total:>6} {matched:>6} {unmatched_t:>6} {unmatched_p:>6}")
    print("=" * 90)

    # Overall stats
    trials_with_tests = [row for row in summaries if row[1] > 0]
    if trials_with_tests:
        avg_coverage = sum(m for _, _, m, _, _ in trials_with_tests) / sum(
            t for _, t, _, _, _ in trials_with_tests
        )
        avg_pattern_hit = 1.0 - (
            sum(p for _, _, _, _, p in trials_with_tests) / (len(patterns) * len(trials_with_tests))
        )
        print(f"\nOverall test coverage: {avg_coverage:.1%} of tests matched a pattern")
        print(
            f"Overall pattern hit rate: {avg_pattern_hit:.1%} of patterns matched at least one test"
        )


if __name__ == "__main__":
    main()
