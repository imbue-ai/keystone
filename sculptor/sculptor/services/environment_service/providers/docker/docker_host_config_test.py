"""Unit tests for docker_host_config module."""

import os
from unittest.mock import patch

from sculptor.services.environment_service.providers.docker.docker_host_config import get_docker_host


def test_get_docker_host_with_tcp_url() -> None:
    """Test extracting host from tcp:// URL."""
    with patch.dict(os.environ, {"DOCKER_HOST": "tcp://100.78.204.7:2375"}):
        assert get_docker_host() == "100.78.204.7"


def test_get_docker_host_with_tcp_url_different_port() -> None:
    """Test extracting host from tcp:// URL with different port."""
    with patch.dict(os.environ, {"DOCKER_HOST": "tcp://192.168.1.100:2376"}):
        assert get_docker_host() == "192.168.1.100"


def test_get_docker_host_with_unix_socket() -> None:
    with patch.dict(os.environ, {"DOCKER_HOST": "unix:///var/run/docker.sock"}):
        assert get_docker_host() == "127.0.0.1"


def test_get_docker_host_with_ssh_url() -> None:
    """Test extracting host from ssh:// URL."""
    with patch.dict(os.environ, {"DOCKER_HOST": "ssh://user@example.com"}):
        assert get_docker_host() == "example.com"


def test_get_docker_host_with_ssh_url_with_port() -> None:
    """Test extracting host from ssh:// URL with port."""
    with patch.dict(os.environ, {"DOCKER_HOST": "ssh://user@example.com:22"}):
        assert get_docker_host() == "example.com"


def test_get_docker_host_not_set() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert get_docker_host() == "127.0.0.1"


def test_get_docker_host_empty_string() -> None:
    with patch.dict(os.environ, {"DOCKER_HOST": ""}):
        assert get_docker_host() == "127.0.0.1"


def test_get_docker_host_with_hostname() -> None:
    """Test extracting hostname from tcp:// URL."""
    with patch.dict(os.environ, {"DOCKER_HOST": "tcp://docker.example.com:2375"}):
        assert get_docker_host() == "docker.example.com"


# NOTE(bowei): i dont think this is exactly right, you cant just pass urls like this to docker without []'ing them
# def test_get_docker_host_with_ipv6() -> None:
#    """Test extracting IPv6 address from tcp:// URL."""
#    with patch.dict(os.environ, {"DOCKER_HOST": "tcp://[::1]:2375"}):
#        assert get_docker_host() == "::1"
#
#
# def test_get_docker_host_with_ipv6_full() -> None:
#    """Test extracting full IPv6 address from tcp:// URL."""
#    with patch.dict(os.environ, {"DOCKER_HOST": "tcp://[2001:db8::1]:2375"}):
#        assert get_docker_host() == "2001:db8::1"


def test_get_docker_host_malformed_url_fallback() -> None:
    with patch.dict(os.environ, {"DOCKER_HOST": "not-a-valid-url"}):
        assert get_docker_host() == "127.0.0.1"
