"""Tests for git archive with submodule support."""

import io
import subprocess
import tarfile
from pathlib import Path

import pytest
from conftest import init_git_repo

from keystone.git_utils import GitError, create_git_archive_bytes


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


@pytest.fixture
def repo_with_submodule(tmp_path: Path) -> Path:
    """Create a parent repo that contains a git submodule."""
    # --- child repo ---
    child = tmp_path / "child_repo"
    child.mkdir()
    (child / "hello.txt").write_text("hello from submodule\n")
    (child / "nested_dir").mkdir()
    (child / "nested_dir" / "deep.txt").write_text("deep file\n")
    init_git_repo(child)

    # --- parent repo ---
    parent = tmp_path / "parent_repo"
    parent.mkdir()
    (parent / "README.md").write_text("# Parent project\n")
    init_git_repo(parent)

    # Add child as submodule (use file:// URI to allow local repo as submodule)
    _run_git(
        ["-c", "protocol.file.allow=always", "submodule", "add", child.as_uri(), "submod"],
        cwd=parent,
    )
    _run_git(["commit", "-m", "add submodule"], cwd=parent)

    return parent


@pytest.fixture
def simple_repo(tmp_path: Path) -> Path:
    """Create a plain repo with no submodules."""
    repo = tmp_path / "simple_repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')\n")
    init_git_repo(repo)
    return repo


def _extract_archive(archive_bytes: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        tar.extractall(path=dest, filter="data")


class TestArchiveWithSubmodules:
    def test_archive_includes_submodule_contents(
        self, repo_with_submodule: Path, tmp_path: Path
    ) -> None:
        archive_bytes = create_git_archive_bytes(repo_with_submodule)

        out = tmp_path / "extracted"
        out.mkdir()
        _extract_archive(archive_bytes, out)

        # Top-level file present
        assert (out / "README.md").exists()
        # Submodule files present
        assert (out / "submod" / "hello.txt").exists()
        assert (out / "submod" / "hello.txt").read_text() == "hello from submodule\n"
        assert (out / "submod" / "nested_dir" / "deep.txt").exists()

    def test_archive_rejects_dirty_tree_with_submodules(
        self, repo_with_submodule: Path
    ) -> None:
        # Dirty the working tree
        (repo_with_submodule / "untracked.txt").write_text("dirty\n")
        _run_git(["add", "untracked.txt"], cwd=repo_with_submodule)

        with pytest.raises(GitError, match="dirty working tree"):
            create_git_archive_bytes(repo_with_submodule)

    def test_archive_without_submodules(
        self, simple_repo: Path, tmp_path: Path
    ) -> None:
        archive_bytes = create_git_archive_bytes(simple_repo)

        out = tmp_path / "extracted"
        out.mkdir()
        _extract_archive(archive_bytes, out)

        assert (out / "main.py").exists()
        assert (out / "main.py").read_text() == "print('hello')\n"
        # No .gitmodules should exist
        assert not (out / ".gitmodules").exists()

    def test_dirty_tree_allowed_without_submodules(
        self, simple_repo: Path, tmp_path: Path
    ) -> None:
        """git archive archives committed state, so dirty tree is fine."""
        (simple_repo / "untracked.txt").write_text("dirty\n")
        _run_git(["add", "untracked.txt"], cwd=simple_repo)

        # Should NOT raise — git archive uses HEAD, not the working tree
        archive_bytes = create_git_archive_bytes(simple_repo)

        out = tmp_path / "extracted"
        out.mkdir()
        _extract_archive(archive_bytes, out)

        # The uncommitted file should NOT be in the archive
        assert not (out / "untracked.txt").exists()
        assert (out / "main.py").exists()
