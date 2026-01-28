"""Shared fixtures and utilities for environment acceptance tests."""

import contextlib
from pathlib import Path
from typing import Generator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.common import generate_id
from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_default_devcontainer_json_path,
)
from sculptor.services.environment_service.providers.docker.docker_provider import DockerProvider
from sculptor.services.environment_service.providers.docker.environment_utils import build_docker_environment
from sculptor.testing.server_utils import TEST_ENVIRONMENT_PREFIX

# Directory containing our sample devcontainers
SAMPLE_DEVCONTAINERS_DIR = Path(__file__).parent / "sample_devcontainers"

# List of all sample devcontainer directory names
DEVCONTAINER_NAMES = [
    pytest.param("DEFAULT_DEVCONTAINER", marks=pytest.mark.integration),
    "empty_alpine",
    "empty_ubuntu",
    "empty_debian",
    # TODO(https://linear.app/imbue/issue/PROD-2275): Add this back.
    # "empty_amazonlinux",
    "python_slim",
    "go",
    "rust",
    "npm_bug",
    "devcontainer_lifecycle_commands",
    "devcontainer_uses_non_standard_build_context",
    "alpine_with_test_user",
    "alpine_with_test_user_in_json",
]


def create_docker_environment_from_devcontainer(
    devcontainer_name: str,
    directory_containing_tarball_of_initial_commit_repo: Path,
    concurrency_group: ConcurrencyGroup,
) -> DockerEnvironment:
    """Create a Docker environment from a devcontainer configuration.

    Args:
        devcontainer_name: Name of the devcontainer (or "DEFAULT_DEVCONTAINER")
        directory_containing_tarball_of_initial_commit_repo: Path to cached repo tarball
        concurrency_group: Concurrency group for the environment

    Returns:
        Created DockerEnvironment instance
    """
    # Get devcontainer.json path
    if devcontainer_name == "DEFAULT_DEVCONTAINER":
        devcontainer_json_path = get_default_devcontainer_json_path()
    else:
        devcontainer_json_path = SAMPLE_DEVCONTAINERS_DIR / devcontainer_name / "devcontainer.json"

    assert devcontainer_json_path.exists(), f"devcontainer.json not found at {devcontainer_json_path}"

    # Create the docker image
    config = LocalDevcontainerImageConfig(devcontainer_json_path=str(devcontainer_json_path))
    docker_provider = DockerProvider(concurrency_group=concurrency_group)
    agent_docker_image = docker_provider.create_image(
        project_id=ProjectID(),
        config=config,
        secrets={},
        cached_repo_tarball_parent_directory=directory_containing_tarball_of_initial_commit_repo,
        environment_prefix=f"{TEST_ENVIRONMENT_PREFIX}{generate_id()}",
        image_metadata=ImageMetadataV1.from_testing(),
    )

    # Create the docker environment
    docker_sandbox_config = LocalDockerEnvironmentConfig()
    docker_environment, create_command = build_docker_environment(
        agent_docker_image,
        name=None,
        config=docker_sandbox_config,
        concurrency_group=concurrency_group,
    )
    del create_command  # Don't complain about unused variable.

    return docker_environment


@pytest.fixture(params=DEVCONTAINER_NAMES)
def docker_environment(
    request: pytest.FixtureRequest,
    directory_containing_tarball_of_initial_commit_repo: Path,
    test_root_concurrency_group: ConcurrencyGroup,
) -> Generator[DockerEnvironment, None, None]:
    """Parametrized fixture that creates Docker environments for all sample devcontainers."""
    devcontainer_name = request.param

    docker_environment = create_docker_environment_from_devcontainer(
        devcontainer_name=devcontainer_name,
        directory_containing_tarball_of_initial_commit_repo=directory_containing_tarball_of_initial_commit_repo,
        concurrency_group=test_root_concurrency_group,
    )

    with contextlib.closing(docker_environment):
        assert docker_environment.is_alive()
        yield docker_environment
