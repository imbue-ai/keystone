import tempfile
from pathlib import Path
from typing import Any
from typing import Generator

import pytest
from syrupy.assertion import SnapshotAssertion
from xdist import is_xdist_controller

from imbue_core.async_monkey_patches_test import explode_on_error  # noqa: F401
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.fixtures import empty_temp_git_repo
from imbue_core.fixtures import initial_commit_repo
from sculptor.config.settings import SculptorSettings
from sculptor.config.settings import TEST_LOG_PATH
from sculptor.config.settings import TestingConfig
from sculptor.services.environment_service.providers.docker.image_fetch import DISABLE_DOCKER_IMAGE_DOWNLOADS_ENV_VAR
from sculptor.testing.launch_mode import add_launch_mode_option
from sculptor.testing.port_manager import PortManager
from sculptor.testing.server_utils import get_testing_container_prefix
from sculptor.utils.logs import setup_default_test_logging
from sculptor.utils.shutdown import GLOBAL_SHUTDOWN_EVENT
from sculptor.web.middleware import shutdown_event

# It is important that these fixtures are imported, so that tests in subdirectories have access to them.
# This line is necessary to prevent the formatter from deleting the import statements
EXPLICITLY_IMPORTED_FIXTURES = (empty_temp_git_repo, initial_commit_repo)

TEST_JWT_PUBLIC_KEY_PATH = Path(__file__).parent / "keys" / "public_test.pem"


def pytest_addoption(parser: pytest.Parser, pluginmanager: Any) -> None:
    # There are lots of other options!  If you want to see them, run:
    #
    #    uv run --project sculptor pytest -co --help -sv sculptor/sculptor/
    #
    # In particular, you might be interested in:
    #   --headed  (to see the browser during tests)

    # TODO: This is only here to avoid pytest-xdist starting a bunch of Sculptor that all do this at once.
    parser.addoption(
        "--prefetch-docker-control-plane",
        action="store_true",
        default=False,
        help="If true, prefetch the Docker control plane once first (per pytest-xdist worker node).",
    )
    add_launch_mode_option(parser)


def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        return


# This happens too early in the life-cycle.  The fixtures should clean up containers after the server exits
# Otherwise this can kill containers that are still in use, eg, from ctrl-c
# def pytest_sessionfinish(session: pytest.Session, exitstatus: Any) -> None:
#     if hasattr(session.config, "workerinput"):
#         return
#     # first, make sure that any leftover testing environments have been cleaned up
#     # note that we have to do this here because of xdist -- we don't want the fixtures being solely responsible for this
#     logger.info("Test session finished")
#     try:
#         destroy_outdated_docker_containers(TEST_ENVIRONMENT_PREFIX)
#         destroy_outdated_docker_images(TEST_ENVIRONMENT_PREFIX)
#     except DockerError as e:
#         logger.info(_env_cleanup_warning, operation="sessionfinish", exception=e)
#     destroy_outdated_local_environments(TEST_ENVIRONMENT_PREFIX)
#     # note that because modal is a shared public resource, it doesnt make sense to clean them up here
#     # they have to be cleaned up separately


@pytest.fixture(scope="session")
def sculptor_root_path() -> Path:
    """The root path of the sculptor project."""
    result = Path(__file__).parent
    assert str(result).endswith("sculptor")
    assert not str(result).endswith("sculptor/sculptor")
    return result


@pytest.fixture(scope="function")
def database_url_(request: pytest.FixtureRequest) -> str:
    file_name = tempfile.NamedTemporaryFile(suffix="db").name
    return f"sqlite:///{file_name}"


@pytest.fixture(scope="session")
def root_tmp_dir_() -> Generator[Path, None, None]:
    """TODO: Document when to use this vs. pytest's native tmp_path fixture."""
    root_tmp_dir = tempfile.gettempdir()
    yield Path(root_tmp_dir)


@pytest.fixture(scope="session")
def port_manager_(request: pytest.FixtureRequest) -> Generator[PortManager, None, None]:
    port_manager = PortManager()
    try:
        yield port_manager
    finally:
        if is_xdist_controller(request):
            port_manager.close()


@pytest.fixture
def port_(port_manager_: PortManager) -> Generator[int, None, None]:
    port = port_manager_.get_free_port()
    yield port
    port_manager_.release_port(port)


@pytest.fixture()
def base_repo_storage_path_() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        temp_dir_path.mkdir(parents=True, exist_ok=True)
        yield temp_dir_path


@pytest.fixture
def capabilities_logging_file_path(tmp_path: Path) -> str:
    return str(tmp_path / "capabilities_logging_file.jsonl")


@pytest.fixture
def git_mirror_local_destination_path(tmp_path: Path) -> Path:
    result = tmp_path / "git_local_mirror"
    # This should not exist yet, git will create it.
    return result


@pytest.fixture
def is_updating_snapshots_(snapshot: SnapshotAssertion) -> bool:
    return snapshot.session.update_snapshots


@pytest.fixture
def database_url() -> Generator[str, None, None]:
    """
    Fixture to provide a database URL for tests.
    This will create a temporary SQLite database file for each test function.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        database_path = tmp_file.name
    database_url = f"sqlite:///{database_path}"
    try:
        yield database_url
    finally:
        # Clean up the temporary database file
        Path(database_path).unlink(missing_ok=True)


@pytest.fixture
def test_settings(database_url: str, tmp_path: Path) -> SculptorSettings:
    settings = SculptorSettings(
        DATABASE_URL=database_url,
        JWT_PUBLIC_KEY_PATH=str(TEST_JWT_PUBLIC_KEY_PATH),
        LOG_PATH=str(TEST_LOG_PATH),
        LOG_LEVEL="TRACE",
        TESTING=TestingConfig(CONTAINER_PREFIX=get_testing_container_prefix()),
        # Disable real gateway calls during tests.
        IMBUE_GATEWAY_BASE_URL="",
        # Disable Docker by default for unit tests to avoid background thread errors
        # when Docker is not available. Integration tests can override this.
        DOCKER_PROVIDER_ENABLED=False,
        LOCAL_PROVIDER_ENABLED=True,
        MODAL_PROVIDER_ENABLED=False,
        # TODO(andrew.laack): Fix once snapshotting is enabled for imbue_cli
        IS_IMBUE_VERIFY_CHECK_ENABLED=False,
        IS_IMBUE_SCOUT_CHECK_ENABLED=False,
        CONFIG_HOME=tmp_path,
    )
    return settings


@pytest.fixture(autouse=True)
def always_explode_on_error(explode_on_error: Any) -> Generator[None, None, None]:
    """
    Ensures that we do not log errors or exceptions during testing.

    If your test is checking error handling behavior (and you expect to see a log_exception call),
    use the `expect_exact_logged_errors` decorator to suppress the logging of those errors.
    """
    yield


@pytest.fixture(autouse=True, scope="session")
def configure_logging() -> None:
    setup_default_test_logging()


@pytest.fixture()
def test_root_concurrency_group() -> Generator[ConcurrencyGroup, None, None]:
    with ConcurrencyGroup(name="test_root") as concurrency_group:
        yield concurrency_group


@pytest.fixture(autouse=True)
def reset_shutdown_event() -> Generator[None, None, None]:
    # Without this, the shutdown event remains set after the first test that uses it.
    yield
    shutdown_event().clear()
    GLOBAL_SHUTDOWN_EVENT.clear()


@pytest.fixture(autouse=True)
def disable_docker_image_prefetching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLE_DOCKER_IMAGE_DOWNLOADS_ENV_VAR, "1")
