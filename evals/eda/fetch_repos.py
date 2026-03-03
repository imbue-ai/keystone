"""Fetch GitHub repository metrics via the GraphQL API into a DataFrame.

Usage:
    export GITHUB_TOKEN=ghp_...
    uv run python evals/eda/fetch_repos.py            # writes evals/eda/repos.parquet
    uv run python evals/eda/fetch_repos.py --csv       # also writes repos.csv

API responses are cached to evals/eda/.api_cache/ so reruns are fast.
Use --no-cache to bypass the cache.

The script samples repos stratified across star-count buckets and languages
to avoid pure-popularity bias.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# Languages to sample across (matches the eval set spread)
LANGUAGES = [
    "Python",
    "JavaScript",
    "TypeScript",
    "Go",
    "Rust",
    "C",
    "C++",
    "Java",
    "Kotlin",
    "Ruby",
    "Elixir",
    "Lua",
]

# Star buckets - we sample from each to avoid pure-popularity bias
STAR_BUCKETS = [
    (10, 100),
    (100, 500),
    (500, 2000),
    (2000, 10000),
    (10000, 50000),
    (50000, 500000),
]

REPOS_PER_QUERY = 30  # GitHub search returns max 100

CACHE_DIR = Path(__file__).parent / ".api_cache"

SEARCH_QUERY_TEMPLATE = """
query($queryString: String!, $first: Int!, $after: String) {
  search(query: $queryString, type: REPOSITORY, first: $first, after: $after) {
    repositoryCount
    pageInfo { endCursor hasNextPage }
    edges {
      node {
        ... on Repository {
          nameWithOwner
          url
          description
          primaryLanguage { name }
          stargazerCount
          forkCount
          diskUsage
          isArchived
          isFork
          openIssues: issues(states: OPEN) { totalCount }
          openPRs: pullRequests(states: OPEN) { totalCount }
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
                history(since: "$SINCE_DATE") { totalCount }
              }
            }
          }
          licenseInfo { spdxId }
          repositoryTopics(first: 5) {
            nodes { topic { name } }
          }
        }
      }
    }
  }
  rateLimit { remaining resetAt }
}
"""


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("Set GITHUB_TOKEN env var (needs public repo read scope)")
    return token


def _graphql(query: str, variables: dict, token: str) -> dict:
    """Execute a GraphQL query against GitHub."""
    resp = requests.post(
        GITHUB_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data


def _cache_path_for(lang: str, star_min: int, star_max: int, per_query: int) -> Path:
    """Stable cache path keyed on the search parameters (not the timestamp)."""
    key = hashlib.sha256(f"{lang}:{star_min}:{star_max}:{per_query}".encode()).hexdigest()[:16]
    return CACHE_DIR / f"{key}.json"


def _since_iso(days_ago: int = 90) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_repo(node: dict) -> dict | None:
    if node.get("isArchived") or node.get("isFork"):
        return None

    total_commits = 0
    if node.get("defaultBranchRef") and node["defaultBranchRef"].get("target"):
        total_commits = node["defaultBranchRef"]["target"].get("history", {}).get("totalCount", 0)

    recent_commits = 0
    if node.get("recentCommits") and node["recentCommits"].get("target"):
        recent_commits = node["recentCommits"]["target"].get("history", {}).get("totalCount", 0)

    topics = []
    if node.get("repositoryTopics"):
        topics = [t["topic"]["name"] for t in node["repositoryTopics"]["nodes"]]

    return {
        "repo": node["nameWithOwner"],
        "url": node["url"],
        "description": (node.get("description") or "")[:200],
        "language": node.get("primaryLanguage", {}).get("name")
        if node.get("primaryLanguage")
        else None,
        "stars": node.get("stargazerCount", 0),
        "forks": node.get("forkCount", 0),
        "disk_usage_kb": node.get("diskUsage", 0),
        "size_mb": round(node.get("diskUsage", 0) / 1024, 1),
        "open_issues": node.get("openIssues", {}).get("totalCount", 0),
        "open_prs": node.get("openPRs", {}).get("totalCount", 0),
        "total_commits": total_commits,
        "recent_commits_90d": recent_commits,
        "license": node.get("licenseInfo", {}).get("spdxId") if node.get("licenseInfo") else None,
        "topics": ",".join(topics),
    }


def fetch_repos(
    languages: list[str] | None = None,
    star_buckets: list[tuple[int, int]] | None = None,
    per_query: int = REPOS_PER_QUERY,
    max_size_kb: int = 500_000,  # skip repos > 500 MB
    recent_days: int = 90,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch repos stratified by language x star bucket."""
    languages = languages or LANGUAGES
    star_buckets = star_buckets or STAR_BUCKETS
    token = _token()
    since = _since_iso(recent_days)

    # Bake the since date into the query template
    query = SEARCH_QUERY_TEMPLATE.replace("$SINCE_DATE", since)

    all_repos: list[dict] = []
    seen: set[str] = set()

    total_queries = len(languages) * len(star_buckets)
    done = 0

    for lang in languages:
        for star_min, star_max in star_buckets:
            done += 1
            cache_file = _cache_path_for(lang, star_min, star_max, per_query)
            print(
                f"[{done}/{total_queries}] {lang} stars:{star_min}..{star_max}", end=" ", flush=True
            )

            # Check disk cache first
            if use_cache and cache_file.exists():
                data = json.loads(cache_file.read_text())
                search = data["data"]["search"]
                print(
                    f"  (cached) found={search['repositoryCount']} fetched={len(search['edges'])}"
                )
            else:
                q = f"language:{lang} stars:{star_min}..{star_max} archived:false fork:false sort:updated"
                try:
                    data = _graphql(
                        query,
                        {"queryString": q, "first": per_query, "after": None},
                        token,
                    )
                except Exception as e:
                    print(f"  ERROR: {e}")
                    continue

                search = data["data"]["search"]
                rate = data["data"]["rateLimit"]
                print(
                    f"  found={search['repositoryCount']} fetched={len(search['edges'])} rate_remaining={rate['remaining']}"
                )

                # Write cache
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(data))

                # Be nice to the API
                if rate["remaining"] < 100:
                    print(f"  Rate limit low ({rate['remaining']}), sleeping 60s...")
                    time.sleep(60)
                else:
                    time.sleep(0.5)

            for edge in search["edges"]:
                parsed = _parse_repo(edge["node"])
                if parsed is None:
                    continue
                if parsed["repo"] in seen:
                    continue
                if parsed["disk_usage_kb"] > max_size_kb:
                    continue
                seen.add(parsed["repo"])
                all_repos.append(parsed)

    df = pd.DataFrame(all_repos)
    print(f"\nTotal unique repos fetched: {len(df)}")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", action="store_true", help="Also write CSV output")
    parser.add_argument(
        "--no-cache", action="store_true", help="Bypass the disk cache for API calls"
    )
    parser.add_argument(
        "--per-query", type=int, default=REPOS_PER_QUERY, help="Repos per search query"
    )
    parser.add_argument(
        "--max-size-mb", type=int, default=500, help="Skip repos larger than this (MB)"
    )
    parser.add_argument(
        "--recent-days", type=int, default=90, help="Window for recent commit count"
    )
    parser.add_argument(
        "--out", type=str, default=None, help="Output path (default: evals/eda/repos.parquet)"
    )
    args = parser.parse_args()

    out = Path(args.out) if args.out else Path(__file__).parent / "repos.parquet"

    df = fetch_repos(
        per_query=args.per_query,
        max_size_kb=args.max_size_mb * 1024,
        recent_days=args.recent_days,
        use_cache=not args.no_cache,
    )

    df.to_parquet(out, index=False)
    print(f"Wrote {out}")

    if args.csv:
        csv_path = out.with_suffix(".csv")
        df.to_csv(csv_path, index=False)
        print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
