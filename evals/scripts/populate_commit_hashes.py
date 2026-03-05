#!/usr/bin/env python3
"""Populate commit_hash fields in repo JSONL files.

For each repo entry, queries the GitHub API (or falls back to git ls-remote)
to resolve the current default-branch HEAD commit, then writes it back to the
JSONL file in-place.

Usage:
    uv run python evals/scripts/populate_commit_hashes.py evals/examples/repos.jsonl
    uv run python evals/scripts/populate_commit_hashes.py evals/eda/filtered_repos.jsonl
    uv run python evals/scripts/populate_commit_hashes.py --all  # both files
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Rate-limit: GitHub unauthenticated API allows 60 req/hr
_GITHUB_API = "https://api.github.com"
_REQUEST_DELAY_SECS = 1.0  # polite delay between API calls


def _github_api_head_sha(owner: str, repo: str) -> str | None:
    """Try to get HEAD SHA via GitHub API (no auth required for public repos)."""
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/commits?per_page=1"
    req = Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data and isinstance(data, list):
                return data[0]["sha"]
    except Exception:
        return None
    return None


def _git_ls_remote_head(repo_url: str) -> str | None:
    """Fall back to git ls-remote to get HEAD SHA."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", repo_url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except Exception:
        pass
    return None


def _parse_github_owner_repo(repo_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL."""
    parsed = urlparse(repo_url)
    if "github.com" not in (parsed.hostname or ""):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2:
        return parts[0], parts[1].removesuffix(".git")
    return None


def resolve_commit_hash(repo_url: str) -> str | None:
    """Resolve the current HEAD commit hash for a repo URL."""
    gh = _parse_github_owner_repo(repo_url)
    if gh:
        sha = _github_api_head_sha(*gh)
        if sha:
            return sha
    # Fallback to git ls-remote
    return _git_ls_remote_head(repo_url)


def _derive_id(repo_url: str) -> str:
    """Derive a short id from a repo URL (last path component, lowercased)."""
    return repo_url.rstrip("/").split("/")[-1].removesuffix(".git").lower()


def process_jsonl(path: Path, *, force: bool = False) -> None:
    """Read a JSONL file, populate id and commit_hash, write back in-place."""
    lines = path.read_text().splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]

    total = len(entries)
    updated = 0
    skipped = 0
    failed = 0
    ids_added = 0

    for i, entry in enumerate(entries):
        repo_url = entry.get("repo", "")

        # Populate id if missing
        if "id" not in entry:
            entry["id"] = _derive_id(repo_url)
            ids_added += 1

        existing = entry.get("commit_hash")

        if existing and not force:
            skipped += 1
            print(f"  [{i + 1}/{total}] SKIP (already has {existing[:12]}): {repo_url}")
            continue

        sha = resolve_commit_hash(repo_url)
        if sha:
            entry["commit_hash"] = sha
            updated += 1
            print(f"  [{i + 1}/{total}] OK   {sha[:12]}: {repo_url}")
        else:
            failed += 1
            print(f"  [{i + 1}/{total}] FAIL could not resolve: {repo_url}", file=sys.stderr)

        time.sleep(_REQUEST_DELAY_SECS)

    # Write back
    with path.open("w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"\nDone: {updated} updated, {skipped} skipped, {failed} failed out of {total}")
    if ids_added:
        print(f"  ({ids_added} entries had 'id' field added)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate commit_hash in repo JSONL files")
    parser.add_argument(
        "files",
        nargs="*",
        help="JSONL files to process",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process both evals/examples/repos.jsonl and evals/eda/filtered_repos.jsonl",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing commit_hash values",
    )
    args = parser.parse_args()

    if args.all:
        files = [
            Path("evals/examples/repos.jsonl"),
            Path("evals/eda/filtered_repos.jsonl"),
        ]
    elif args.files:
        files = [Path(f) for f in args.files]
    else:
        parser.error("Specify files or use --all")

    for path in files:
        if not path.exists():
            print(f"ERROR: {path} not found", file=sys.stderr)
            sys.exit(1)
        print(f"\nProcessing {path}...")
        process_jsonl(path, force=args.force)


if __name__ == "__main__":
    main()
