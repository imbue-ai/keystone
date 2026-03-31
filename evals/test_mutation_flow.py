"""Tests for mutation pipeline (Phase 1).

Unit tests that don't require Modal or Claude Code.
"""

import json
import subprocess
from pathlib import Path

from eval_schema import RepoEntry


def _init_git_repo(path: Path) -> str:
    """Create a minimal git repo and return the HEAD commit hash."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, capture_output=True, check=True
    )

    # Create a simple Python source and test
    src_dir = path / "src"
    src_dir.mkdir()
    (src_dir / "math_utils.py").write_text("def add(a, b):\n    return a + b\n")

    test_dir = path / "tests"
    test_dir.mkdir()
    (test_dir / "test_math.py").write_text(
        "from src.math_utils import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )

    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True, check=True
    )
    # Create main branch
    subprocess.run(["git", "branch", "-M", "main"], cwd=path, capture_output=True, check=True)

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_repo_entry_with_broken_commit_hashes() -> None:
    """RepoEntry accepts broken_commit_hashes field."""
    entry = RepoEntry(
        id="test",
        repo="https://example.com/test",
        commit_hash="abc123",
        broken_commit_hashes=["hash1", "hash2"],
    )
    assert entry.broken_commit_hashes == ["hash1", "hash2"]


def test_repo_entry_broken_commit_hashes_default() -> None:
    """RepoEntry defaults broken_commit_hashes to empty list."""
    entry = RepoEntry(id="test", repo="https://example.com/test", commit_hash="abc123")
    assert entry.broken_commit_hashes == []


def test_amended_jsonl_schema(tmp_path: Path) -> None:
    """Verify repos_with_mutations.jsonl contains broken_commit_hashes."""
    entries = [
        {
            "id": "flask",
            "repo": "https://github.com/pallets/flask",
            "commit_hash": "abc123",
            "broken_commit_hashes": ["h1", "h2", "h3"],
        },
        {
            "id": "requests",
            "repo": "https://github.com/psf/requests",
            "commit_hash": "def456",
            "broken_commit_hashes": [],
        },
    ]
    jsonl_path = tmp_path / "repos_with_mutations.jsonl"
    with jsonl_path.open("w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    # Parse back and validate
    parsed: list[RepoEntry] = []
    with jsonl_path.open() as f:
        for line in f:
            parsed.append(RepoEntry(**json.loads(line.strip())))

    assert len(parsed) == 2
    assert parsed[0].broken_commit_hashes == ["h1", "h2", "h3"]
    assert parsed[1].broken_commit_hashes == []


def test_bare_tarball_structure(tmp_path: Path) -> None:
    """Verify bare git tarball has expected branch layout."""
    # Create a repo with broken branches
    repo_path = tmp_path / "test_repo"
    _init_git_repo(repo_path)

    # Simulate mutation: create broken-1 and broken-2 branches
    for i in range(1, 3):
        subprocess.run(
            ["git", "checkout", "-b", f"broken-{i}", "main"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        src_file = repo_path / "src" / "math_utils.py"
        src_file.write_text(f"def add(a, b):\n    raise AssertionError('mutation {i}')\n")
        subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"mutation {i}"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(["git", "checkout", "main"], cwd=repo_path, capture_output=True, check=True)

    # Create bare clone
    bare_path = tmp_path / "test_repo.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo_path), str(bare_path)],
        capture_output=True,
        check=True,
    )

    # Verify branches exist in bare repo
    result = subprocess.run(
        ["git", "branch"], cwd=bare_path, capture_output=True, text=True, check=True
    )
    branches = [b.strip().lstrip("* ") for b in result.stdout.strip().split("\n")]
    assert "main" in branches
    assert "broken-1" in branches
    assert "broken-2" in branches

    # Verify each broken branch is a single commit off main
    for i in range(1, 3):
        # Count commits between main and broken-i
        result = subprocess.run(
            ["git", "log", "--oneline", f"main..broken-{i}"],
            cwd=bare_path,
            capture_output=True,
            text=True,
            check=True,
        )
        commits = [line for line in result.stdout.strip().split("\n") if line.strip()]
        assert len(commits) == 1, f"broken-{i} should be exactly 1 commit off main"
