"""Docker utility functions for Keystone."""

import subprocess


def check_docker_available() -> bool:
    """Check if Docker CLI is installed and daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
