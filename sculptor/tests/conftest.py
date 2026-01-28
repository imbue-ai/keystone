import os
from pathlib import Path
from typing import Generator

import pytest
from conftest import TEST_JWT_PUBLIC_KEY_PATH
from loguru import logger
from pytest import Session

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.common import generate_id
from imbue_core.common import get_temp_dir
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.fixtures import initial_commit_repo
from imbue_core.git import get_repo_base_path
from imbue_core.testing_utils import temp_dir
from sculptor.config.settings import SculptorSettings
from sculptor.config.settings import TEST_LOG_PATH
from sculptor.config.settings import TestingConfig
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.services.environment_service.default_implementation import create_archived_repo
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    build_local_devcontainer_image,
)
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    start_control_plane_background_setup,
)
from sculptor.services.environment_service.providers.docker.environment_utils import destroy_outdated_docker_containers
from sculptor.services.environment_service.providers.docker.environment_utils import destroy_outdated_docker_images
from sculptor.services.environment_service.providers.docker.errors import DockerError
from sculptor.services.environment_service.providers.local.environment_utils import destroy_outdated_local_environments
from sculptor.testing.resources import pure_local_repo_  # noqa: F401
from sculptor.testing.server_utils import TEST_ENVIRONMENT_PREFIX
from sculptor.testing.server_utils import get_testing_container_prefix

_IMPORTED_FIXTURES = (initial_commit_repo,)


# TODO(andy): https://linear.app/imbue/issue/PROD-1614/testing-constrain-docker-cleanup-to-docker-invoking-test-runs
_env_cleanup_warning = """Error cleaning up environments on {operation} (maybe docker not running?)

IF YOU HAVE DOCKER OR ENV FAILURES IN YOUR LOGS THIS IS PROBABLY WHY

Exception:
{exception}
"""


def pytest_configure(config: pytest.Config) -> None:
    is_ide_collection_operation = hasattr(config.option, "collectonly") and config.option.collectonly
    if hasattr(config, "workerinput") or is_ide_collection_operation:
        return
    # first, make sure that any leftover testing environments have been cleaned up
    # note that we have to do this here because of xdist -- we don't want the fixtures being solely responsible for this
    try:
        with ConcurrencyGroup(name="adhoc_testing_concurrency_group") as concurrency_group:
            destroy_outdated_docker_containers(
                lambda x: x.startswith(TEST_ENVIRONMENT_PREFIX), concurrency_group=concurrency_group
            )
            destroy_outdated_docker_images(
                lambda img: img.startswith(TEST_ENVIRONMENT_PREFIX), concurrency_group=concurrency_group
            )
    except DockerError as e:
        logger.info(_env_cleanup_warning, operation="sessionfinish", exception=e)
    destroy_outdated_local_environments(TEST_ENVIRONMENT_PREFIX)
    # note that because modal is a shared public resource, it doesnt make sense to clean them up here
    # they have to be cleaned up separately


def pytest_sessionstart(session: Session) -> None:
    """This function runs once per session in the controller node once execution, and then once again on every worker
    node.
    """
    if not hasattr(session.config, "workerinput"):
        with ConcurrencyGroup(name="setup_once") as concurrency_group:
            setup_once(session, concurrency_group)


def pytest_sessionfinish(session: Session) -> None:
    """This function runs once per xdist session on the controller node and once on every worker."""

    if not hasattr(session.config, "workerinput"):
        teardown_once(session)


def setup_once(session: Session, concurrency_group: ConcurrencyGroup) -> None:
    """This code is guaranteed to run only once on the worker node, prior to any xdist distribution."""

    logger.info("Running setup_once")

    docker_threads = []
    if session.config.getoption("--prefetch-docker-control-plane"):
        docker_threads = start_control_plane_background_setup(
            thread_suffix="PytestSetupOnce", concurrency_group=concurrency_group
        )

    for thread in docker_threads:
        thread.join()

    logger.info("Finished setup_once.")


def teardown_once(session: Session) -> None:
    """This code is guaranteed to run only once on the worker node, after all tests have been executed."""
    # This is a no-op now.


@pytest.fixture
def test_settings(database_url: str) -> SculptorSettings:
    project_path: str | Path | None = os.getenv("PROJECT_PATH")
    if not project_path:
        project_path = get_repo_base_path()
    logger.info("Using project path: {}", project_path)
    settings = SculptorSettings(
        DATABASE_URL=database_url,
        JWT_PUBLIC_KEY_PATH=str(TEST_JWT_PUBLIC_KEY_PATH),
        LOG_PATH=str(TEST_LOG_PATH),
        LOG_LEVEL="TRACE",
        TESTING=TestingConfig(CONTAINER_PREFIX=get_testing_container_prefix()),
        IMBUE_GATEWAY_BASE_URL="",
        DOCKER_PROVIDER_ENABLED=True,
        LOCAL_PROVIDER_ENABLED=False,
        MODAL_PROVIDER_ENABLED=False,
        # TODO(andrew.laack): Fix once snapshotting is enabled for imbue_cli
        IS_IMBUE_VERIFY_CHECK_ENABLED=False,
    )
    return settings


@pytest.fixture
def directory_containing_tarball_of_initial_commit_repo(
    initial_commit_repo: tuple[Path, str], test_root_concurrency_group: ConcurrencyGroup
) -> Generator[Path, None, None]:
    with temp_dir(get_temp_dir()) as place_to_put_archived_mock_repo:
        create_archived_repo(
            initial_commit_repo[0], place_to_put_archived_mock_repo / "repo.tar", test_root_concurrency_group
        )
        yield place_to_put_archived_mock_repo


@pytest.fixture
def project_id_for_default_docker_image() -> ProjectID:
    return ProjectID()


@pytest.fixture
def default_docker_image(
    project_id_for_default_docker_image: ProjectID,
    directory_containing_tarball_of_initial_commit_repo: Path,
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings: SculptorSettings,
) -> LocalDockerImage:
    """Create a Docker image for testing."""
    image_name = f"{TEST_ENVIRONMENT_PREFIX}-{generate_id()}"
    TEST_DATA_DIR = (
        Path(__file__).parent.parent
        / "sculptor"
        / "services"
        / "environment_service"
        / "providers"
        / "docker"
        / "default_devcontainer"
    )
    test_data_path = TEST_DATA_DIR / "devcontainer.json"
    assert test_data_path.exists()

    config = LocalDevcontainerImageConfig(devcontainer_json_path=str(test_data_path))
    return build_local_devcontainer_image(
        config,
        project_id=project_id_for_default_docker_image,
        image_repo=image_name,
        cached_repo_tarball_parent_directory=directory_containing_tarball_of_initial_commit_repo,
        concurrency_group=test_root_concurrency_group,
        image_metadata=ImageMetadataV1.from_testing(),
    )
