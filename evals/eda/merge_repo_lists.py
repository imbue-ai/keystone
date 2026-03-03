#!/usr/bin/env python3
"""Merge filtered_repos.jsonl and examples/repos.jsonl into a unified list.

Fetches missing metrics (stars, size, recent commits, test file counts) for
repos in examples/repos.jsonl via the GitHub GraphQL and Git Trees APIs,
using the same cache directories as fetch_repos.py and enrich_test_counts.py.

Usage:
    export GITHUB_TOKEN=ghp_...
    uv run python evals/eda/merge_repo_lists.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
SCRIPT_DIR = Path(__file__).parent
REPO_CACHE_DIR = SCRIPT_DIR / ".api_cache" / "single_repo"
TREE_CACHE_DIR = SCRIPT_DIR / ".tree_cache"

# ---------- paths ----------
FILTERED_PATH = SCRIPT_DIR / "filtered_repos.jsonl"
EXAMPLES_PATH = SCRIPT_DIR.parent / "examples" / "repos.jsonl"
OUTPUT_PATH = SCRIPT_DIR / "merged_repo_list_proposal.jsonl"

# ---------- test file patterns (copied from enrich_test_counts.py) ----------
TEST_NAME_PATTERNS: list[str] = [
    "test_*.py",
    "*_test.py",
    "*_test.go",
    "*.test.ts",
    "*.test.tsx",
    "*.test.js",
    "*.test.jsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*Test.java",
    "*Test.kt",
    "*_spec.rb",
    "*_test.exs",
    "*_test.ex",
    "test_*.lua",
    "*_test.lua",
    "*_spec.lua",
    "*_test.c",
    "*_test.cc",
    "*_test.cpp",
]

TEST_DIR_NAMES: set[str] = {
    "test",
    "tests",
    "testing",
    "__tests__",
    "spec",
    "specs",
    "test_data",
    "testdata",
    "testutil",
    "testutils",
}


def _fnmatch_simple(name: str, pattern: str) -> bool:
    """Minimal fnmatch supporting only leading/trailing * patterns."""
    if pattern.startswith("*") and pattern.endswith("*"):
        return pattern[1:-1] in name
    if pattern.startswith("*"):
        return name.endswith(pattern[1:])
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def _is_test_by_name(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(_fnmatch_simple(name, p) for p in TEST_NAME_PATTERNS)


def _is_in_test_dir(path: str) -> bool:
    parts = path.split("/")
    return any(p.lower() in TEST_DIR_NAMES for p in parts[:-1])


def count_test_files(tree: list[dict]) -> dict[str, int]:
    """Count test files from a tree listing."""
    by_name: set[str] = set()
    in_dirs: set[str] = set()
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        p = entry["path"]
        if _is_test_by_name(p):
            by_name.add(p)
        if _is_in_test_dir(p):
            in_dirs.add(p)
    total = by_name | in_dirs
    return {
        "test_files_by_name": len(by_name),
        "files_in_test_dirs": len(in_dirs),
        "test_files_total": len(total),
    }


# ---------- GitHub API helpers ----------

def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("Set GITHUB_TOKEN env var (needs public repo read scope)")
    return token


def _since_iso(days_ago: int = 90) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


SINGLE_REPO_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    url
    description
    primaryLanguage { name }
    stargazerCount
    forkCount
    diskUsage
    isArchived
    isFork
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 0) { totalCount }
        }
      }
    }
    recentCommits: defaultBranchRef {
      target {
        ... on Commit {
          history(since: $since) { totalCount }
        }
      }
    }
    licenseInfo { spdxId }
    repositoryTopics(first: 5) {
      nodes { topic { name } }
    }
  }
  rateLimit { remaining resetAt }
}
"""


def _repo_cache_path(owner_repo: str) -> Path:
    key = hashlib.sha256(owner_repo.encode()).hexdigest()[:16]
    return REPO_CACHE_DIR / f"{key}.json"


def _tree_cache_path(owner_repo: str) -> Path:
    key = hashlib.sha256(owner_repo.encode()).hexdigest()[:16]
    return TREE_CACHE_DIR / f"{key}.json"


def fetch_repo_metrics(owner_repo: str, token: str) -> dict | None:
    """Fetch repo metrics via GraphQL, with caching."""
    cache = _repo_cache_path(owner_repo)
    if cache.exists():
        return json.loads(cache.read_text())

    owner, name = owner_repo.split("/", 1)
    since = _since_iso(90)

    resp = requests.post(
        GITHUB_GRAPHQL_URL,
        json={"query": SINGLE_REPO_QUERY, "variables": {"owner": owner, "name": name, "since": since}},
        headers={"Authorization": f"bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        print(f"  GraphQL error for {owner_repo}: {data['errors']}")
        return None

    node = data["data"]["repository"]
    if node is None:
        return None

    rl = data.get("data", {}).get("rateLimit", {})
    remaining = rl.get("remaining", "?")
    print(f"  fetched {owner_repo} (rate limit remaining: {remaining})")

    total_commits = 0
    if node.get("defaultBranchRef") and node["defaultBranchRef"].get("target"):
        total_commits = node["defaultBranchRef"]["target"].get("history", {}).get("totalCount", 0)

    recent_commits = 0
    if node.get("recentCommits") and node["recentCommits"].get("target"):
        recent_commits = node["recentCommits"]["target"].get("history", {}).get("totalCount", 0)

    result = {
        "stars": node.get("stargazerCount", 0),
        "size_mb": round(node.get("diskUsage", 0) / 1024, 1),
        "recent_commits_90d": recent_commits,
        "total_commits": total_commits,
        "language": (node.get("primaryLanguage") or {}).get("name"),
        "notes": (node.get("description") or "")[:200],
    }

    REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result))
    return result


def fetch_tree(owner_repo: str, token: str) -> list[dict] | None:
    """Fetch recursive tree, with caching (same cache as enrich_test_counts.py)."""
    cache = _tree_cache_path(owner_repo)
    if cache.exists():
        return json.loads(cache.read_text())

    url = f"https://api.github.com/repos/{owner_repo}/git/trees/HEAD?recursive=1"
    resp = requests.get(
        url,
        headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )

    if resp.status_code in (404, 409):
        return []
    resp.raise_for_status()

    data = resp.json()
    tree = data.get("tree", [])

    if data.get("truncated"):
        print(f"  (tree truncated for {owner_repo})")

    TREE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tree))
    return tree


def _owner_repo_from_url(url: str) -> str:
    return "/".join(url.rstrip("/").split("/")[-2:])


def main() -> None:
    token = _token()

    # Load filtered repos
    filtered: list[dict] = []
    if FILTERED_PATH.exists():
        filtered = [json.loads(line) for line in FILTERED_PATH.read_text().splitlines() if line.strip()]
    print(f"Loaded {len(filtered)} filtered repos")

    # Load examples repos
    examples: list[dict] = []
    if EXAMPLES_PATH.exists():
        examples = [json.loads(line) for line in EXAMPLES_PATH.read_text().splitlines() if line.strip()]
    print(f"Loaded {len(examples)} example repos")

    # Index filtered by repo URL for dedup
    filtered_urls: set[str] = {r["repo"] for r in filtered}

    # Build merged list: filtered first (with source flag), then examples
    merged: list[dict] = []

    for rec in filtered:
        out = {
            "repo": rec["repo"],
            "language": rec.get("language", ""),
            "stars": rec.get("stars", 0),
            "size_mb": rec.get("size_mb", 0),
            "recent_commits_90d": rec.get("recent_commits_90d", 0),
            "test_files_total": rec.get("test_files_total", 0),
            "test_files_by_name": rec.get("test_files_by_name", 0),
            "files_in_test_dirs": rec.get("files_in_test_dirs", 0),
            "notes": rec.get("notes", ""),
            "from_examples": False,
        }
        merged.append(out)

    # Enrich and add examples
    for i, ex in enumerate(examples):
        repo_url = ex["repo"]
        owner_repo = _owner_repo_from_url(repo_url)

        # Check for duplicate
        is_dup = repo_url in filtered_urls
        if is_dup:
            print(f"  [{i + 1}/{len(examples)}] {owner_repo} already in filtered, marking from_examples=True")
            # Find and update the existing entry
            for m in merged:
                if m["repo"] == repo_url:
                    m["from_examples"] = True
                    break
            continue

        print(f"  [{i + 1}/{len(examples)}] Fetching metrics for {owner_repo}...")

        # Fetch repo metrics
        metrics = fetch_repo_metrics(owner_repo, token)
        if metrics is None:
            print(f"    Could not fetch metrics for {owner_repo}, using defaults")
            metrics = {
                "stars": 0,
                "size_mb": 0,
                "recent_commits_90d": 0,
                "language": ex.get("language", ""),
                "notes": ex.get("notes", ""),
            }

        # Fetch tree for test counts
        tree = fetch_tree(owner_repo, token)
        test_counts = count_test_files(tree) if tree is not None else {
            "test_files_by_name": 0,
            "files_in_test_dirs": 0,
            "test_files_total": 0,
        }

        out = {
            "repo": repo_url,
            "language": metrics.get("language") or ex.get("language", ""),
            "stars": metrics.get("stars", 0),
            "size_mb": metrics.get("size_mb", 0),
            "recent_commits_90d": metrics.get("recent_commits_90d", 0),
            "test_files_total": test_counts["test_files_total"],
            "test_files_by_name": test_counts["test_files_by_name"],
            "files_in_test_dirs": test_counts["files_in_test_dirs"],
            "notes": metrics.get("notes") or ex.get("notes", ""),
            "from_examples": True,
        }
        merged.append(out)

        # Be nice to API
        time.sleep(0.5)

    # Sort by language, then stars desc
    merged.sort(key=lambda r: (r.get("language", "").lower(), -r.get("stars", 0)))

    # Write with rank
    with OUTPUT_PATH.open("w") as f:
        for rank, rec in enumerate(merged, 1):
            rec["rank"] = rank
            f.write(json.dumps(rec) + "\n")

    print(f"\nWrote {len(merged)} repos to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
