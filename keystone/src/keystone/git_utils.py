"""Git utilities for working with versioned code trees."""

import subprocess
from pathlib import Path


class GitError(Exception):
    """Raised when a git operation fails."""

    pass


def get_git_tree_hash(repo_path: Path) -> str:
    """Get the git tree hash for HEAD of the given repository.

    This returns the tree hash, not the commit hash, so it only depends
    on the file contents, not commit metadata.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to get git tree hash: {e.stderr}") from e


def create_git_archive_bytes(repo_path: Path) -> bytes:
    """Create a tarball of the repository and return as bytes.

    If the repository contains submodules, initializes them and builds the
    tarball from ``git ls-files --recurse-submodules`` so that submodule
    contents are included.  Repositories without submodules use the faster
    ``git archive`` path.
    """
    try:
        has_submodules = (repo_path / ".gitmodules").exists()
        if has_submodules:
            return _create_archive_with_submodules(repo_path)

        result = subprocess.run(
            ["git", "archive", "--format=tar.gz", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        stderr = e.stderr if isinstance(e.stderr, str) else e.stderr.decode()
        raise GitError(f"Failed to create git archive: {stderr}") from e


def _create_archive_with_submodules(repo_path: Path) -> bytes:
    """Build a tar.gz that includes submodule contents.

    Unlike ``git archive`` (which archives committed state), this archives
    the working tree, so we require a clean tree to avoid capturing
    uncommitted changes.

    1. Verify the working tree is clean.
    2. Ensure submodules are initialised and checked out.
    3. List every tracked file (including inside submodules) with
       null-delimited output for safe filename handling.
    4. Feed that list to ``tar`` to produce the archive.
    """
    if is_git_dirty(repo_path):
        raise GitError(
            "Cannot create archive with submodules from a dirty working tree. "
            "Please commit or stash your changes first."
        )

    subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )

    ls_result = subprocess.run(
        ["git", "ls-files", "--recurse-submodules", "-z"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )

    result = subprocess.run(
        ["tar", "-czf", "-", "--null", "-T", "-"],
        input=ls_result.stdout,
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    return result.stdout


def is_git_repo(path: Path) -> bool:
    """Check if the given path is inside a git repository."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=path,
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def is_git_dirty(repo_path: Path) -> bool:
    """Check if the git repository has uncommitted changes.

    Returns True if there are staged, unstaged, or untracked files.
    """
    try:
        # Check for staged/unstaged changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False
