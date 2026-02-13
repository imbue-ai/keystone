"""Git utilities for working with versioned code trees."""

import io
import subprocess
import tarfile
import tempfile
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


def create_git_archive(repo_path: Path, output_path: Path) -> None:
    """Create a tarball of the repository using git archive.

    Uses HEAD to create a clean archive without untracked files.
    """
    try:
        subprocess.run(
            ["git", "archive", "--format=tar.gz", "-o", str(output_path), "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to create git archive: {e.stderr}") from e


def create_git_archive_bytes(repo_path: Path) -> bytes:
    """Create a tarball of the repository and return as bytes.

    TODO: Use `git archive --recurse-submodules` once available in mainline git
    to include submodule contents. Currently submodules are not included.
    """
    try:
        result = subprocess.run(
            ["git", "archive", "--format=tar.gz", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to create git archive: {e.stderr.decode()}") from e


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


def extract_git_archive_to_temp(repo_path: Path) -> Path:
    """Create a git archive and extract it to a temporary directory.

    Returns the path to the temporary directory containing the extracted files.
    The caller is responsible for cleaning up the temporary directory.
    """
    archive_bytes = create_git_archive_bytes(repo_path)
    temp_dir = Path(tempfile.mkdtemp(prefix="git-archive-"))

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        tar.extractall(temp_dir)

    return temp_dir
