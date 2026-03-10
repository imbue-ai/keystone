"""Tests that evals/examples/repos.jsonl validates against RepoEntry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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


# FIXME: Use a loop inside the test, don't create ~200 tests.
@pytest.mark.parametrize(
    "entry",
    _load_lines() if REPOS_JSONL.exists() else [],
    ids=lambda e: e.get("id", e.get("repo", "unknown")),
)
def test_repo_entry_validates(entry: dict) -> None:
    """Each line in repos.jsonl must validate as a RepoEntry."""
    try:
        RepoEntry(**entry)
    except ValidationError as exc:
        pytest.fail(f"Validation failed for {entry.get('repo', 'unknown')}:\n{exc}")


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
