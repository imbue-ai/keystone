"""Tests that evals/examples/repos.jsonl validates against RepoEntry."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from evals.eval_schema import RepoEntry

REPOS_JSONL = Path(__file__).parent / "examples" / "repos.jsonl"


def _load_lines() -> list[dict]:
    """Load all non-empty lines from repos.jsonl."""
    text = REPOS_JSONL.read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_repos_jsonl_exists() -> None:
    assert REPOS_JSONL.exists(), f"{REPOS_JSONL} not found"


def test_repos_jsonl_not_empty() -> None:
    entries = _load_lines()
    assert len(entries) > 0, "repos.jsonl is empty"


def test_all_repo_entries_validate() -> None:
    """Every line in repos.jsonl must validate as a RepoEntry."""
    errors: list[str] = []
    for entry in _load_lines():
        try:
            RepoEntry(**entry)
        except ValidationError as exc:
            repo = entry.get("repo", "unknown")
            errors.append(f"{repo}:\n{exc}")
    assert not errors, f"Validation failed for {len(errors)} entries:\n" + "\n".join(errors)


def test_no_duplicate_ids() -> None:
    """All repo entries must have unique ids."""
    entries = _load_lines()
    ids = [e.get("id") for e in entries if "id" in e]
    assert len(ids) == len(entries), "Some entries are missing 'id' field"
    dupes = [x for x in ids if ids.count(x) > 1]
    assert not dupes, f"Duplicate ids found: {set(dupes)}"


def test_no_duplicate_repos() -> None:
    """All repo entries must have unique repo URLs."""
    entries = _load_lines()
    repos = [e["repo"] for e in entries]
    dupes = [x for x in repos if repos.count(x) > 1]
    assert not dupes, f"Duplicate repo URLs found: {set(dupes)}"
