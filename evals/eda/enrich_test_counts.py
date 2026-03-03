"""Enrich selected_repos.jsonl with estimated test file counts.

Uses the GitHub Git Trees API (recursive) to fetch the full file listing for
each repo, then counts files matching common test patterns. Results are cached
to .tree_cache/ so reruns are instant.

Usage:
    export GITHUB_TOKEN=ghp_...
    uv run python evals/eda/enrich_test_counts.py
    uv run python evals/eda/enrich_test_counts.py --input my_repos.jsonl --output enriched.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

TREE_CACHE_DIR = Path(__file__).parent / ".tree_cache"

# Patterns that indicate a test file (case-insensitive matching on the filename)
# Covers Python, JS/TS, Go, Rust, Java, Kotlin, Ruby, Elixir, C/C++, Lua
TEST_FILE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^test_.*\.py$", re.IGNORECASE),  # test_foo.py
    re.compile(r"^.*_test\.py$", re.IGNORECASE),  # foo_test.py
    re.compile(r"^.*_test\.go$", re.IGNORECASE),  # foo_test.go
    re.compile(r"^.*\.test\.[jt]sx?$", re.IGNORECASE),  # foo.test.js, foo.test.tsx
    re.compile(r"^.*\.spec\.[jt]sx?$", re.IGNORECASE),  # foo.spec.ts
    re.compile(r"^.*Test\.java$"),  # FooTest.java
    re.compile(r"^.*Test\.kt$"),  # FooTest.kt
    re.compile(r"^.*_spec\.rb$", re.IGNORECASE),  # foo_spec.rb
    re.compile(r"^test_.*\.rb$", re.IGNORECASE),  # test_foo.rb
    re.compile(r"^.*_test\.exs?$", re.IGNORECASE),  # foo_test.exs
    re.compile(r"^.*_test\.rs$", re.IGNORECASE),  # foo_test.rs
    re.compile(r"^test_.*\.c$", re.IGNORECASE),  # test_foo.c
    re.compile(r"^test_.*\.cpp$", re.IGNORECASE),  # test_foo.cpp
    re.compile(r"^.*_test\.cpp$", re.IGNORECASE),  # foo_test.cpp
    re.compile(r"^.*_test\.lua$", re.IGNORECASE),  # foo_test.lua
    re.compile(r"^test_.*\.lua$", re.IGNORECASE),  # test_foo.lua
]

# Directories that conventionally contain tests
TEST_DIR_SEGMENTS = {"test", "tests", "__tests__", "spec", "specs", "test_suite", "testing"}


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("Set GITHUB_TOKEN env var (needs public repo read scope)")
    return token


def _owner_repo(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL."""
    # https://github.com/owner/repo -> owner/repo
    return "/".join(url.rstrip("/").split("/")[-2:])


def _cache_path(owner_repo: str) -> Path:
    key = hashlib.sha256(owner_repo.encode()).hexdigest()[:16]
    return TREE_CACHE_DIR / f"{key}.json"


def fetch_tree(owner_repo: str, token: str, *, use_cache: bool = True) -> list[dict] | None:
    """Fetch the full recursive tree for a repo's default branch.

    Returns a list of tree entries (each has 'path', 'type', 'size') or None on error.
    The GitHub API truncates trees with >100k entries; we still use what we get.
    """
    cache_file = _cache_path(owner_repo)

    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    # First get the default branch SHA
    url = f"https://api.github.com/repos/{owner_repo}/git/trees/HEAD?recursive=1"
    resp = requests.get(
        url,
        headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )

    if resp.status_code == 409:
        # Empty repo
        return []
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    data = resp.json()
    tree = data.get("tree", [])

    if data.get("truncated"):
        print(f"  (tree truncated for {owner_repo}, using partial listing)")

    # Cache the tree entries
    TREE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(tree))

    return tree


def count_test_files(tree: list[dict]) -> dict[str, int]:
    """Count test files using filename patterns and directory heuristics.

    Returns a dict with:
      - test_files_by_name: files matching test naming patterns
      - files_in_test_dirs: files inside test directories (regardless of name)
      - test_files_total: union of both (deduplicated)
    """
    by_name: set[str] = set()
    in_test_dir: set[str] = set()

    for entry in tree:
        if entry.get("type") != "blob":
            continue

        path = entry["path"]
        filename = path.rsplit("/", 1)[-1]

        # Check filename patterns
        for pattern in TEST_FILE_PATTERNS:
            if pattern.match(filename):
                by_name.add(path)
                break

        # Check if any path segment is a test directory
        segments = set(path.lower().split("/")[:-1])  # exclude filename itself
        if segments & TEST_DIR_SEGMENTS:
            in_test_dir.add(path)

    total = by_name | in_test_dir

    return {
        "test_files_by_name": len(by_name),
        "files_in_test_dirs": len(in_test_dir),
        "test_files_total": len(total),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=str,
        default=str(Path(__file__).parent / "selected_repos.jsonl"),
        help="Input JSONL file",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSONL file (default: overwrites input)",
    )
    parser.add_argument("--no-cache", action="store_true", help="Bypass tree cache")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path

    repos = [json.loads(line) for line in input_path.read_text().splitlines() if line.strip()]
    token = _token()
    total = len(repos)

    for i, repo in enumerate(repos, 1):
        owner_repo = _owner_repo(repo["repo"])
        print(f"[{i}/{total}] {owner_repo}", end=" ", flush=True)

        tree = fetch_tree(owner_repo, token, use_cache=not args.no_cache)

        if tree is None:
            print("  SKIP (not found)")
            repo["test_files_by_name"] = 0
            repo["files_in_test_dirs"] = 0
            repo["test_files_total"] = 0
            continue

        counts = count_test_files(tree)
        repo.update(counts)

        cache_hit = _cache_path(owner_repo).exists()
        total_files = sum(1 for e in tree if e.get("type") == "blob")
        print(
            f"  {'(cached)' if cache_hit else ''} "
            f"files={total_files} test_by_name={counts['test_files_by_name']} "
            f"in_test_dirs={counts['files_in_test_dirs']} total={counts['test_files_total']}"
        )

        # Rate limiting (only when not cached)
        if not cache_hit:
            time.sleep(0.3)

    # Write output
    with output_path.open("w") as f:
        for repo in repos:
            f.write(json.dumps(repo) + "\n")

    print(f"\nWrote {len(repos)} repos to {output_path}")


if __name__ == "__main__":
    main()
