"""Utilities for disabling external dependencies in tests."""

import os
from enum import StrEnum
from pathlib import Path

import pytest


class DependencyState(StrEnum):
    """Possible states for a dependency in tests."""

    # binary not found (exit 127)
    NOT_INSTALLED = "NOT_INSTALLED"
    # binary found but not working (exit 1)
    NOT_RUNNING = "NOT_RUNNING"


# Stub script content for disabled dependencies
# These scripts shadow the real binaries and fail with a clear error message

# Exit code 1 simulates "installed but not running/working"
# This stub is command-aware: --version succeeds (Docker is installed) but other commands fail (daemon not running)
DOCKER_NOT_RUNNING_STUB = """#!/bin/bash
case "$1" in
    --version|-v)
        echo "Docker version 24.0.0, build abc123"
        exit 0
        ;;
    *)
        echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2
        exit 1
        ;;
esac
"""

# Exit code 127 simulates "command not found" (not installed)
DOCKER_NOT_INSTALLED_STUB = """#!/bin/bash
echo "docker: command not found" >&2
exit 127
"""

GIT_NOT_INSTALLED_STUB = """#!/bin/bash
echo "git: command not found" >&2
exit 127
"""

DEPENDENCY_STUB_SCRIPTS: dict[tuple[str, DependencyState], str] = {
    ("docker", DependencyState.NOT_RUNNING): DOCKER_NOT_RUNNING_STUB,
    ("docker", DependencyState.NOT_INSTALLED): DOCKER_NOT_INSTALLED_STUB,
    ("git", DependencyState.NOT_INSTALLED): GIT_NOT_INSTALLED_STUB,
}


def create_disabled_dependency_stub(
    stub_dir: Path, binary_name: str, state: DependencyState = DependencyState.NOT_RUNNING
) -> None:
    """Create a stub script that shadows a binary and fails when called.

    This is used to simulate a dependency being unavailable by placing a failing
    stub script earlier in PATH than the real binary.
    """
    stub_path = stub_dir / binary_name
    script_content = DEPENDENCY_STUB_SCRIPTS[(binary_name, state)]
    stub_path.write_text(script_content)
    stub_path.chmod(0o755)


class DisabledDependencies:
    """Container for tracking which dependencies should be disabled in tests.

    Uses PATH shadowing to simulate missing dependencies: we prepend a directory
    containing stub scripts that fail appropriately.

    Usage:
        @disable_dependency("docker")  # Default: NOT_RUNNING (exit 1)
        def test_works_without_docker_running(sculptor_page_):
            # Docker commands will fail as if daemon isn't running
            ...

        @disable_dependency("docker", state="not_installed")  # NOT_INSTALLED (exit 127)
        def test_works_without_docker_installed(sculptor_page_):
            # Docker commands will fail as if not installed
            ...

        @disable_dependency("git", state="not_installed")
        def test_works_without_git(sculptor_page_):
            # Git commands will fail as if not installed
            ...
    """

    def __init__(self) -> None:
        self._disabled: dict[str, DependencyState] = {}
        self._stub_dir: Path | None = None

    @classmethod
    def from_request(cls, request: pytest.FixtureRequest) -> "DisabledDependencies":
        """Extract disabled dependencies from pytest markers on the test."""
        disabled = cls()
        markers_found = list(request.node.iter_markers(disable_dependency.name))
        for marker in markers_found:
            if marker.args:
                dep_name = marker.args[0].lower()
                state_str = marker.kwargs.get("state", DependencyState.NOT_RUNNING)
                state = DependencyState(state_str)
                disabled._disabled[dep_name] = state
        return disabled

    def apply_to_environment(self, environment: dict[str, str | None], tmp_path: Path) -> None:
        """Modify the environment dict to disable the specified dependencies.

        Creates stub scripts in a temporary directory and prepends that directory
        to PATH so the stubs shadow the real binaries.
        """
        if not self._disabled:
            return

        # Create a stub directory for disabled dependencies
        stub_dir = tmp_path / "disabled_dependency_stubs"
        stub_dir.mkdir(exist_ok=True)
        self._stub_dir = stub_dir

        for dep_name, state in self._disabled.items():
            create_disabled_dependency_stub(stub_dir, dep_name, state)

        # Prepend stub directory to PATH so stubs are found first
        original_path = environment.get("PATH") or os.environ.get("PATH", "")
        new_path = f"{stub_dir}{os.pathsep}{original_path}"
        environment["PATH"] = new_path


# Pytest marker for disabling dependencies in tests
# Usage: @disable_dependency("docker") or @disable_dependency("docker", state="not_installed")
disable_dependency = pytest.mark.disable_dependency
