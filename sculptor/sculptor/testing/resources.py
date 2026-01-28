from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Callable
from typing import Generator
from typing import assert_never

import pytest
from loguru import logger
from playwright.sync_api import Playwright
from syrupy.assertion import SnapshotAssertion
from xdist import get_xdist_worker_id
from xdist import is_xdist_worker

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.concurrency_group import ConcurrencyGroupState
from imbue_core.secrets_utils import Secret
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.config_service.conftest import populate_config_file_for_test
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.data_types import OpenAIApiKey
from sculptor.services.config_service.local_implementation import CREDENTIALS_FILENAME
from sculptor.services.config_service.utils import populate_credentials_file
from sculptor.services.environment_service.providers.docker.image_fetch import BASE_IMAGE_CACHE_DIR
from sculptor.testing.caching_utils import get_cache_dir_from_snapshot
from sculptor.testing.dependency_stubs import DisabledDependencies
from sculptor.testing.frontend_utils import AppElectronFrontend
from sculptor.testing.frontend_utils import BrowserFrontend
from sculptor.testing.frontend_utils import DevElectronFrontend
from sculptor.testing.frontend_utils import Frontend
from sculptor.testing.frontend_utils import dev_electron_frontend
from sculptor.testing.git_snapshot import FullLocalGitRepo
from sculptor.testing.git_snapshot import GitCommitSnapshot
from sculptor.testing.launch_mode import LaunchMode
from sculptor.testing.launch_mode import get_launch_mode
from sculptor.testing.mock_repo import MockRepoState
from sculptor.testing.multi_tab_page_factory import MultiTabPageFactory
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.port_manager import PortManager
from sculptor.testing.repo_resources import generate_test_project_repo
from sculptor.testing.server_utils import SculptorFactory
from sculptor.testing.server_utils import get_sculptor_command_backend_only
from sculptor.testing.server_utils import get_sculptor_command_electron
from sculptor.testing.server_utils import get_testing_container_prefix
from sculptor.testing.server_utils import get_testing_environment
from sculptor.testing.server_utils import get_v1_frontend_path
from sculptor.testing.test_repo_factory import TestRepoFactory
from sculptor.utils.build import get_sculptor_folder


@pytest.fixture(scope="session")
def sculptor_launch_mode_(request: pytest.FixtureRequest) -> LaunchMode:
    return get_launch_mode(request.config)


class IsolationLevel(StrEnum):
    """The testing Isolation Level specifies whether the system under test is connecting to a live 3rd party system or
    whether it's served a mocked snapshot.

    Keep in mind that the same test scenario is often instrumented to run in multiple isolation levels.

    NOTE: This Enum could be expanded in future to encompass more fine-grained distinctions in testing isolation!
    """

    # Isolated tests do NOT use a real 3rd party service (such as an LLM provider) but have these calls mocked.
    ISOLATED = "isolated"
    # Non-isolated tests actually do call out to the 3rd party services--and are thus slower and more expensive.
    NON_ISOLATED = "non_isolated"


# TODO: Clean up this hack.
#  This switch allows us to do some munging of the tests that we generate based on whether the IMBUE_MODAL_TEST is
# in a particular mode. But this is going to be deprecated soon--we should GENERATE all tests and select them well.
if os.environ.get("IMBUE_MODAL_TEST") == "acceptance":
    _testing_mode_params = [
        pytest.param(IsolationLevel.ISOLATED, marks=[pytest.mark.non_isolated, pytest.mark.acceptance]),
    ]
elif os.environ.get("IMBUE_MODAL_TEST") == "integration":
    _testing_mode_params = [
        pytest.param(IsolationLevel.NON_ISOLATED, marks=[pytest.mark.isolated, pytest.mark.integration]),
    ]
else:
    _testing_mode_params = [
        pytest.param(IsolationLevel.ISOLATED, marks=[pytest.mark.non_isolated, pytest.mark.acceptance]),
        pytest.param(IsolationLevel.NON_ISOLATED, marks=[pytest.mark.isolated, pytest.mark.integration]),
    ]


@pytest.fixture(
    params=_testing_mode_params,
    scope="session",
)
def testing_mode_(request: pytest.FixtureRequest) -> Generator[IsolationLevel]:
    yield request.param


@pytest.fixture
def pure_local_repo_(
    request: pytest.FixtureRequest, test_root_concurrency_group: ConcurrencyGroup
) -> Generator[MockRepoState, None, None]:
    """Creates a local repository with a single commit on a branch and no remote.

    The repo is constructed from scratch, so it's actually very fast."""
    with generate_test_project_repo(request, test_root_concurrency_group) as repo:
        repo.create_reset_and_checkout_branch("testing")
        repo.write_file("src/app.py", "import flask\n\nflask.run()")
        repo.commit("app.py commit", commit_time="2025-01-01T00:00:01")
        # make a second commit to make sure we don't try to run stuff on a commit without the config files..
        repo.write_file("stuff.txt", "stuff")
        repo.commit("Stuff", commit_time="2025-01-01T00:00:02")
        yield repo
        logger.info("Cleaning up repo at {}", repo.base_path)


@pytest.fixture
def pure_local_repo_with_checks_(
    request: pytest.FixtureRequest, test_root_concurrency_group: ConcurrencyGroup
) -> Generator[MockRepoState, None, None]:
    """Creates a local repository with checks configuration included.

    Use this fixture for tests that need checks to be available."""
    with tempfile.TemporaryDirectory() as tempdir:
        checks_repo_contents = {
            ".gitignore": "node_modules\n",
            "README.md": "# Test Project\n\nThis is a test project\n",
            ".sculptor/checks.toml": """[successful_check]
command = "echo 'Hello World'"
is_enabled = true

[failing_check]
command = "echo 'Test failed' && exit 1"
is_enabled = true

[slow_check]
command = "sleep 10 && echo 'Slow check completed'"
is_enabled = true

[pytest_check]
command = "pytest tests/"
is_enabled = true

[lint_check]
command = "python -m flake8 src/"
is_enabled = true
""",
        }

        checks_file_contents = {
            "data/something.txt": "some data\n",
            "src/main.py": "print('hello world')\nprint('goodbye')\n",
        }

        initial_state = FullLocalGitRepo(
            git_user_email="product@imbue.com",
            git_user_name="imbue",
            git_diff=None,
            git_branch="main",
            main_history=(
                GitCommitSnapshot(
                    contents_by_path=checks_repo_contents,
                    commit_message="initial commit",
                    commit_time="2025-01-01T00:00:01",
                ),
                GitCommitSnapshot(
                    contents_by_path=checks_file_contents,
                    commit_message="add some cool data",
                    commit_time="2025-01-01T00:00:01",
                ),
            ),
        )

        test_project_name = (
            "test_project_checks"
            if not is_xdist_worker(request)
            else "test_project_checks_" + get_xdist_worker_id(request)
        )
        repo_dir = Path(tempdir) / test_project_name
        logger.info("Creating test project repo with checks in {}", str(repo_dir))
        repo = MockRepoState.build_locally(
            state=initial_state, local_dir=repo_dir, concurrency_group=test_root_concurrency_group
        )
        subprocess.run(["git", "remote", "add", "origin", str(repo_dir)])

        repo.create_reset_and_checkout_branch("testing")
        repo.write_file("stuff.txt", "stuff")
        repo.commit("Stuff", commit_time="2025-01-01T00:00:02")
        yield repo


custom_sculptor_config_path = pytest.mark.custom_sculptor_config_path


@pytest.fixture
def sculptor_config_path_(request: pytest.FixtureRequest) -> Generator[Path, None, None]:
    config_path = request.node.get_closest_marker(custom_sculptor_config_path.name)
    if config_path:
        yield Path(config_path.args[0])
        return

    with tempfile.NamedTemporaryFile(suffix=".toml", delete=True) as file:
        config_path = Path(file.name)
        populate_config_file_for_test(config_path)
        yield config_path


@pytest.fixture
def credentials_(snapshot: SnapshotAssertion, testing_mode_: IsolationLevel) -> Credentials:
    if snapshot.session.update_snapshots or testing_mode_ == IsolationLevel.ISOLATED:
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        openai_api_key = os.environ.get("OPENAI_API_KEY")
    else:
        anthropic_api_key = "sk-ant-fake-api-key"
        openai_api_key = "sk-fake-api-key"
    return Credentials(
        anthropic=AnthropicApiKey(anthropic_api_key=Secret(anthropic_api_key), generated_from_oauth=False),
        openai=OpenAIApiKey(openai_api_key=Secret(openai_api_key), generated_from_oauth=False),
    )


CustomFolderPopulator = Callable[[Path, Credentials], None]
custom_sculptor_folder_populator = pytest.mark.custom_sculptor_folder


def _default_sculptor_folder_populator(folder_path: Path, credentials: Credentials) -> None:
    populate_config_file_for_test(folder_path / "config.toml")
    populate_credentials_file(path=folder_path / CREDENTIALS_FILENAME, credentials=credentials)


def _extract_marker_arg(request: pytest.FixtureRequest, marker: pytest.MarkDecorator) -> Callable | None:
    marker = request.node.get_closest_marker(marker.name)
    return marker.args[0] if marker else None


@pytest.fixture
def sculptor_folder_(request: pytest.FixtureRequest, credentials_: Credentials) -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as dir:
        folder_path = Path(dir)
        folder_populator: CustomFolderPopulator = (
            _extract_marker_arg(request, custom_sculptor_folder_populator) or _default_sculptor_folder_populator
        )
        folder_populator(folder_path, credentials_)

        # Create a symlink to the image cache directory so that we don't redownload images all of the time.
        image_cache_dir = get_sculptor_folder() / BASE_IMAGE_CACHE_DIR
        if image_cache_dir.exists():
            (folder_path / BASE_IMAGE_CACHE_DIR).symlink_to(image_cache_dir)

        yield folder_path


@pytest.fixture(scope="function")
def container_prefix_() -> Generator[str, None, None]:
    yield get_testing_container_prefix()


no_auto_project = pytest.mark.no_auto_project


@pytest.fixture
def auto_select_project_(request: pytest.FixtureRequest) -> Generator[bool, None, None]:
    if request.node.get_closest_marker(no_auto_project.name):
        yield False
        return

    yield True


@pytest.fixture
def local_sync_debounce_seconds_() -> float | None:
    return None


@pytest.fixture
def sculptor_factory_(
    sculptor_launch_mode_: LaunchMode,
    testing_mode_: IsolationLevel,
    request: pytest.FixtureRequest,
    pure_local_repo_: MockRepoState,
    auto_select_project_: bool,
    database_url_: str,
    sculptor_folder_: Path,
    container_prefix_: str,
    snapshot_path_: Path,
    snapshot: SnapshotAssertion,
    output_path: str,
    sculptor_backend_port_: int,
    frontend_: Frontend,
    tmp_path: Path,
    local_sync_debounce_seconds_: float | None,
) -> Generator[SculptorFactory]:
    """This fixture provides a running sculptor server."""
    update_snapshots = snapshot.session.update_snapshots
    repo_path = pure_local_repo_.base_path if auto_select_project_ else None

    assert (testing_mode_, update_snapshots) != (IsolationLevel.ISOLATED, True), (
        "Updating snapshots is not implemented for acceptance tests"
    )

    if update_snapshots or testing_mode_ == IsolationLevel.ISOLATED:
        hide_keys = False
        existing_snapshot_path = None
    else:
        hide_keys = True
        existing_snapshot_path = snapshot_path_

    is_checks_enabled = "pure_local_repo_with_checks_" in request.fixturenames

    match sculptor_launch_mode_:
        case LaunchMode.BROWSER:
            sculptor_command = get_sculptor_command_backend_only(
                repo_path,
                port=sculptor_backend_port_,
            )
            sculptor_environment = get_testing_environment(
                database_url=database_url_,
                container_prefix=container_prefix_,
                sculptor_folder=sculptor_folder_,
                tmp_path=tmp_path,
                hide_keys=hide_keys,
                # Note: This is the only difference between v1 and (dist, electron)
                static_files_path=(get_v1_frontend_path() / "dist").absolute(),
                is_checks_enabled=is_checks_enabled,
            )
        case LaunchMode.APP_ELECTRON:
            assert isinstance(frontend_, AppElectronFrontend)
            sculptor_command = get_sculptor_command_electron(
                repo_path,
                port=sculptor_backend_port_,
                cdp_port=frontend_.cdp_port,
            )
            sculptor_environment = get_testing_environment(
                database_url=database_url_,
                container_prefix=container_prefix_,
                sculptor_folder=sculptor_folder_,
                tmp_path=tmp_path,
                hide_keys=hide_keys,
                is_checks_enabled=is_checks_enabled,
                port=sculptor_backend_port_,
                local_sync_debounce_seconds=local_sync_debounce_seconds_,
            )
        case LaunchMode.DEV_ELECTRON:
            assert isinstance(frontend_, DevElectronFrontend)
            sculptor_command = get_sculptor_command_backend_only(
                repo_path,
                port=sculptor_backend_port_,
            )
            sculptor_environment = get_testing_environment(
                database_url=database_url_,
                container_prefix=container_prefix_,
                sculptor_folder=sculptor_folder_,
                tmp_path=tmp_path,
                hide_keys=hide_keys,
                is_checks_enabled=is_checks_enabled,
                port=sculptor_backend_port_,
                frontend_port=frontend_.frontend_port,
                local_sync_debounce_seconds=local_sync_debounce_seconds_,
            )
        case _ as unreachable:
            assert_never(unreachable)

    # Check for @disable_dependency marks and apply them to the environment
    disabled_deps = DisabledDependencies.from_request(request)
    disabled_deps.apply_to_environment(sculptor_environment, tmp_path)

    sculptor_factory = SculptorFactory(
        command=sculptor_command,
        environment=sculptor_environment,
        snapshot_path=existing_snapshot_path,
        container_prefix=container_prefix_,
        port=sculptor_backend_port_,
        database_url=database_url_,
        update_snapshots=update_snapshots,
        frontend=frontend_,
        request=request,
    )
    yield sculptor_factory

    # Must update snapshots before the server is shut down.
    if snapshot.session.update_snapshots:
        logger.info("Copying in saved snapshots")
        sculptor_factory.copy_snapshots(new_snapshot_path=snapshot_path_)

    failed = not hasattr(request.node, "rep_call") or request.node.rep_call.failed
    if failed:
        logger.info(f"Copying out preserved files for a failed test run to: {output_path}")
        sculptor_factory.copy_artifacts(new_artifacts_path=Path(output_path))
        # might as well stick the logs and DB in there too:
        database_file = "/" + database_url_.replace("sqlite:///", "").lstrip("/")
        db_path = Path(database_file)
        if db_path.exists():
            shutil.copy(db_path, Path(output_path) / "sculptor.db")


@pytest.fixture(scope="function")
def database_url_() -> str:
    db_file = tempfile.NamedTemporaryFile(suffix="db").name
    return f"sqlite:///{db_file}"


@pytest.fixture(scope="function")
def sculptor_page_(sculptor_factory_: SculptorFactory) -> Generator[PlaywrightHomePage]:
    """Fixture to launch a Playwright page for test purposes with retry."""
    with sculptor_factory_.spawn_sculptor_instance() as (sculptor_server, sculptor_page):
        yield sculptor_page


@pytest.fixture
def snapshot_path_(snapshot: SnapshotAssertion) -> Generator[Path, None, None]:
    snapshot_path = get_cache_dir_from_snapshot(snapshot=snapshot)
    yield snapshot_path


@pytest.fixture
def multi_tab_page_factory_(
    sculptor_factory_: SculptorFactory,
) -> Generator[MultiTabPageFactory, None, None]:
    """
    Factory for creating multiple browser tabs in the same context for cross-tab testing.

    Returns a MultiTabPageFactory that can create pages on demand.
    All created pages share the same browser context (cookies, localStorage, etc.)
    but are separate tabs that can navigate independently.

    Usage:
        def test_cross_tab(multi_tab_page_factory):
            factory = multi_tab_page_factory

            # Primary page is already available
            factory.primary_page.do_something()

            # Create additional pages as needed
            secondary_page = factory.create_page()
            secondary_page.do_something_else()
    """
    with sculptor_factory_.spawn_sculptor_instance() as (server, primary_page):
        # Create the factory with the primary page and server URL
        factory = MultiTabPageFactory(primary_page, server.url)

        yield factory

        factory.cleanup()


@pytest.fixture
def test_repo_factory_(
    tmp_path: Path, test_root_concurrency_group: ConcurrencyGroup
) -> Generator[TestRepoFactory, None, None]:
    """
    Factory fixture for creating test repositories on demand.

    This fixture provides a function that tests can call multiple times
    to create separate test repositories with different configurations.
    Each repository is created in a temporary directory that's automatically
    cleaned up after the test.

    Usage:
        def test_something(test_repo_factory):
            repo1 = test_repo_factory("project1", "main")
            repo2 = test_repo_factory("project2", "develop")
    """
    factory = TestRepoFactory(base_path=tmp_path, concurrency_group=test_root_concurrency_group)
    yield factory


@pytest.fixture(scope="session")
def frontend_(
    sculptor_launch_mode_: LaunchMode,
    port_manager_: PortManager,
    sculptor_backend_port_: int,
    playwright: Playwright,
) -> Generator[Frontend, None, None]:
    match sculptor_launch_mode_:
        case LaunchMode.APP_ELECTRON:
            yield AppElectronFrontend(cdp_port=port_manager_.get_free_port(), playwright=playwright)
        case LaunchMode.DEV_ELECTRON:
            yield dev_electron_frontend(port_manager_, sculptor_backend_port_, playwright)
        case LaunchMode.BROWSER:
            yield BrowserFrontend(backend_port=sculptor_backend_port_)
        case _ as unreachable:
            assert_never(unreachable)


# We fix the backend port per-session because when launching the dev Electron frontend,
# we have to tell it in advance which backend port to connect to.
#
# It's not strictly necessary to launch all backends with the same port in other cases,
# but it's harmless too.
@pytest.fixture(scope="session")
def sculptor_backend_port_(port_manager_: PortManager) -> Generator[int, None, None]:
    yield port_manager_.get_free_port()


class AlreadyRunningServiceCollection(CompleteServiceCollection):
    """Wraps an existing already-started CompleteServiceCollection to prevent multiple run_all calls.

    This makes re-using the same service collection in the same process through the FastAPI mock client possible,
    as otherwise the app's lifespan middleware would restart it repeatedly & explode due to our db lock (among other things).

    For single-process integration tests. For more e2e tests use sculptor_factory_ instead.
    """

    @classmethod
    def build(cls, from_collection: CompleteServiceCollection) -> "AlreadyRunningServiceCollection":
        assert from_collection.config_service.concurrency_group._state == ConcurrencyGroupState.ACTIVE
        return cls(
            settings=from_collection.settings,
            data_model_service=from_collection.data_model_service,
            environment_service=from_collection.environment_service,
            config_service=from_collection.config_service,
            git_repo_service=from_collection.git_repo_service,
            task_service=from_collection.task_service,
            project_service=from_collection.project_service,
            local_sync_service=from_collection.local_sync_service,
        )

    @contextmanager
    def run_all(self) -> Generator[None, None, None]:
        yield
