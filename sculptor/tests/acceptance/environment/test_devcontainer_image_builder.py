import json
from pathlib import Path

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import DevcontainerError
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    build_local_devcontainer_image,
)
from tests.conftest import directory_containing_tarball_of_initial_commit_repo

_IMPORTED_FIXTURES = (directory_containing_tarball_of_initial_commit_repo,)

TEST_DATA_DIR = Path(__file__).parent / "test_data"


def test_build_local_devcontainer_image(
    project_id_for_default_docker_image: ProjectID,
    directory_containing_tarball_of_initial_commit_repo: Path,
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings: SculptorSettings,
) -> None:
    """Test building a devcontainer image with a simple dockerfile configuration."""
    test_data_path = TEST_DATA_DIR / "devcontainer.json"
    assert test_data_path.exists()

    config = LocalDevcontainerImageConfig(devcontainer_json_path=str(test_data_path))
    result = build_local_devcontainer_image(
        config,
        project_id=project_id_for_default_docker_image,
        image_repo="test-tag",
        cached_repo_tarball_parent_directory=directory_containing_tarball_of_initial_commit_repo,
        concurrency_group=test_root_concurrency_group,
        image_metadata=ImageMetadataV1.from_testing(),
    )

    assert result.image_id.startswith("sha256:")


def test_build_local_devcontainer_image_missing_file(
    project_id_for_default_docker_image: ProjectID,
    directory_containing_tarball_of_initial_commit_repo: Path,
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings: SculptorSettings,
) -> None:
    """Test FileNotFoundError when devcontainer.json doesn't exist."""
    config = LocalDevcontainerImageConfig(devcontainer_json_path="/nonexistent/devcontainer.json")
    with pytest.raises(FileNotFoundError, match="devcontainer.json not found"):
        build_local_devcontainer_image(
            config,
            project_id=project_id_for_default_docker_image,
            image_repo="test-tag",
            cached_repo_tarball_parent_directory=directory_containing_tarball_of_initial_commit_repo,
            concurrency_group=test_root_concurrency_group,
            image_metadata=ImageMetadataV1.from_testing(),
        )


def test_build_local_devcontainer_image_missing_dockerfile_field(
    tmp_path: Path,
    project_id_for_default_docker_image: ProjectID,
    directory_containing_tarball_of_initial_commit_repo: Path,
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings: SculptorSettings,
) -> None:
    """Test ValueError when devcontainer.json doesn't have dockerfile field."""
    devcontainer_json_path = tmp_path / "devcontainer.json"
    devcontainer_json_path.write_text(json.dumps({"name": "test"}))

    config = LocalDevcontainerImageConfig(devcontainer_json_path=str(devcontainer_json_path))
    with pytest.raises(DevcontainerError, match="devcontainer.json must contain a 'build.dockerfile' field"):
        build_local_devcontainer_image(
            config,
            project_id=project_id_for_default_docker_image,
            image_repo="test-tag",
            cached_repo_tarball_parent_directory=directory_containing_tarball_of_initial_commit_repo,
            concurrency_group=test_root_concurrency_group,
            image_metadata=ImageMetadataV1.from_testing(),
        )
