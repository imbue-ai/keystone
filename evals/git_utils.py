"""Git utilities for resolving the bootstrap_devcontainer version to use."""

import subprocess
from pathlib import Path


class GitRepoError(Exception):
    """Error related to git repository state."""

    pass


def get_repo_root() -> Path | None:
    """Get the git repository root, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_git_info(repo_root: Path) -> tuple[str, bool]:
    """Get current git commit hash and check if repo is clean.

    Args:
        repo_root: Root of the git repository

    Returns:
        (commit_hash, is_clean) tuple
    """
    # Get current commit hash
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    commit_hash = result.stdout.strip()

    # Check if repo is clean
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    is_clean = result.stdout.strip() == ""

    return commit_hash, is_clean


def check_commit_pushed(repo_root: Path, commit_hash: str) -> bool:
    """Check if commit exists on origin/main."""
    # Fetch latest from origin
    subprocess.run(
        ["git", "fetch", "origin", "main"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )

    # Check if commit is ancestor of origin/main
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit_hash, "origin/main"],
        cwd=repo_root,
        capture_output=True,
    )
    return result.returncode == 0


def resolve_git_ref(require_pushed: bool = True) -> str:
    """Resolve the git ref to use for bootstrap_devcontainer.

    If running from within a git repo (bootstrap_devcontainer itself),
    requires a clean tree and returns the current commit hash.

    Args:
        require_pushed: If True, also verify the commit is pushed to origin/main

    Returns:
        Git ref (commit hash) to use

    Raises:
        GitRepoError: If repo is dirty or commit not pushed (when required)
    """
    # Check if we're in the bootstrap_devcontainer repo
    repo_root = get_repo_root()
    if repo_root is None:
        # Not in a git repo - fall back to prod branch
        return "prod"

    # Check if this looks like the bootstrap_devcontainer repo
    if not (repo_root / "bootstrap_devcontainer").is_dir():
        # Not the bootstrap_devcontainer repo - fall back to prod
        return "prod"

    # We're in the bootstrap_devcontainer repo - require clean tree
    commit_hash, is_clean = get_git_info(repo_root)

    if not is_clean:
        raise GitRepoError(
            "Git repo has uncommitted changes. Commit and push before running evals."
        )

    if require_pushed and not check_commit_pushed(repo_root, commit_hash):
        raise GitRepoError(
            f"Commit {commit_hash[:8]} not pushed to origin/main. "
            "Push before running evals, or use --no-require-pushed."
        )

    return commit_hash
