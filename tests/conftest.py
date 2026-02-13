"""Pytest fixtures and test utilities for keystone tests."""

import shutil
import subprocess
from pathlib import Path

import pytest

# Path to samples directory
SAMPLES_DIR = Path(__file__).parent.parent.parent / "samples"


class GitError(Exception):
    """Raised when a git operation fails."""

    pass


def init_git_repo(path: Path, add_all: bool = True, commit: bool = True) -> None:
    """Initialize a git repository and optionally add/commit all files.

    This is useful for tests that need a git repo from a non-git directory.
    Uses config that doesn't depend on global git settings.
    """
    try:
        subprocess.run(
            ["git", "init"],
            cwd=path,
            capture_output=True,
            check=True,
        )
        # Configure user for this repo only (doesn't require global config)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=path,
            capture_output=True,
            check=True,
        )
        if add_all:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=path,
                capture_output=True,
                check=True,
            )
        if commit:
            subprocess.run(
                ["git", "commit", "-m", "Initial commit", "--allow-empty"],
                cwd=path,
                capture_output=True,
                check=True,
            )
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to initialize git repo: {e.stderr}") from e


@pytest.fixture
def project_root(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Create a temporary copy of a sample project initialized as a git repo.

    Use with indirect parametrization to specify the sample name:
        @pytest.mark.parametrize("project_root", ["python_project"], indirect=True)

    Or use the default "python_project" sample.
    """
    # Get sample name from parameter, default to python_project
    sample_name = getattr(request, "param", "python_project")
    original_project_root = SAMPLES_DIR / sample_name
    project_dir = tmp_path / "project"
    shutil.copytree(original_project_root, project_dir)
    init_git_repo(project_dir)
    return project_dir
