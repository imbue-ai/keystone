"""Utility for extracting Docker host configuration from environment variables."""

import os
from urllib.parse import urlparse


def get_docker_host() -> str:
    """
    Get the Docker daemon host from DOCKER_HOST environment variable.

    Returns the host IP/hostname extracted from DOCKER_HOST if set,
    otherwise defaults to 'localhost'.

    DOCKER_HOST can be in formats like:
    - tcp://100.78.204.7:2375
    - unix:///var/run/docker.sock
    - ssh://user@host

    Returns:
        str: The docker host IP or hostname

    Examples:
        >>> os.environ['DOCKER_HOST'] = 'tcp://100.78.204.7:2375'
        >>> get_docker_host()
        '100.78.204.7'

        >>> os.environ.pop('DOCKER_HOST', None)
        >>> get_docker_host()
        'localhost'
    """
    docker_host_env = os.environ.get("DOCKER_HOST")

    default_val = "127.0.0.1"
    if not docker_host_env:
        return default_val

    # Parse the DOCKER_HOST URL
    parsed = urlparse(docker_host_env)

    if parsed.scheme == "unix":
        return default_val

    # For tcp:// or ssh:// URLs, extract the hostname
    if parsed.hostname:
        return parsed.hostname

    return default_val
