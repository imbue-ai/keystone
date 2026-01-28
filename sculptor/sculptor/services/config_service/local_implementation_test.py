import time
from pathlib import Path
from typing import Any
from typing import Generator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.secrets_utils import Secret
from sculptor.database.models import Project
from sculptor.database.models import TaskID
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.interfaces.environments.base import LocalImage
from sculptor.primitives.ids import OrganizationReference
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.local_implementation import LocalConfigService
from sculptor.services.config_service.plugin_system import ConfigServicePlugin
from sculptor.services.config_service.plugin_system import ConfigurationRule
from sculptor.services.config_service.plugin_system import LOCAL_HOME_PLACEHOLDER
from sculptor.services.config_service.utils import populate_credentials_file
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.providers.local.environment_utils import build_local_environment
from sculptor.services.environment_service.providers.local.image_utils import build_local_image

SANDBOX_DESTINATION = Path("/tmp/synced_config")
POLL_INTERVAL_SECONDS = 0.1
MAX_WAIT_SECONDS = 2.0


class _LocalConfigService(LocalConfigService):
    plugin_override: ConfigServicePlugin

    def model_post_init(self, context: Any) -> None:
        self._claude_code_plugin_full = ConfigServicePlugin()
        self._claude_code_plugin_minimal = ConfigServicePlugin()
        self._project_service_plugin = self.plugin_override


@pytest.fixture
def test_project(tmp_path: Path) -> Project:
    project_path = tmp_path / "test_project"
    project_path.mkdir(parents=True, exist_ok=True)
    return Project(
        object_id=ProjectID(),
        name="Test Project",
        organization_reference=OrganizationReference("org_test_123"),
        user_git_repo_url=f"file://{project_path}",
    )


@pytest.fixture
def local_image(tmp_path: Path, test_project: Project) -> LocalImage:
    code_directory = tmp_path / "code"
    code_directory.mkdir(parents=True, exist_ok=True)
    return build_local_image(code_directory, test_project.object_id)


@pytest.fixture
def local_environment(
    test_root_concurrency_group: ConcurrencyGroup, local_image: LocalImage
) -> Generator[LocalEnvironment, None, None]:
    environment = None
    try:
        environment = build_local_environment(local_image, LocalEnvironmentConfig(), test_root_concurrency_group)
        yield environment
    finally:
        if environment is not None:
            environment.close()


@pytest.fixture
def test_config_service(
    tmp_path: Path,
    test_root_concurrency_group: ConcurrencyGroup,
) -> Generator[LocalConfigService, None, None]:
    config_home = tmp_path / "config_home"
    config_home.mkdir(parents=True, exist_ok=True)
    credentials_file_path = tmp_path / ".sculptor" / "credentials.json"
    credentials_file_path.parent.mkdir(parents=True, exist_ok=True)
    populate_credentials_file(
        path=credentials_file_path,
        credentials=Credentials(
            anthropic=AnthropicApiKey(
                anthropic_api_key=Secret(secret_value="sk-ant-fake-api-key"), generated_from_oauth=False
            )
        ),
    )
    secret_file_path = tmp_path / ".sculptor/.env"
    rule = ConfigurationRule(
        name="Test Sync Rule",
        synchronize_from=LOCAL_HOME_PLACEHOLDER / "test_config",
        synchronize_to=SANDBOX_DESTINATION,
        is_notifying_on_updates=False,
    )

    plugin = ConfigServicePlugin(configuration_rules=(rule,))
    service = _LocalConfigService(
        secret_file_path=secret_file_path,
        credentials_file_path=credentials_file_path,
        concurrency_group=test_root_concurrency_group.make_concurrency_group("config_service"),
        config_home_local=config_home,
        plugin_override=plugin,
    )
    with service.run():
        yield service


def _test_synchronization(
    config_service: LocalConfigService,
    local_environment: LocalEnvironment,
    test_project: Project,
    source_file: Path,
    destination_file: Path,
) -> None:
    """
    Verify that:

    1. After start_synchronizing_environment(), the configuration gets applied.
    2. When the source file changes on disk, the configuration is updated in the environment.
    3. When the source file is deleted on disk, the configuration is removed from the environment.
    3. After stop_synchronizing_environment(), the configuration is no longer updated in the environment.

    """
    task_id = TaskID()

    config_service.start_synchronizing_environment(test_project, task_id, local_environment)
    initial_content = local_environment.read_file(str(destination_file))
    assert initial_content == "initial config content", "Initial configuration should be synced to the environment"

    source_file.unlink()
    waited = 0.0
    updated = False
    while waited < MAX_WAIT_SECONDS:
        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
        if local_environment.exists(str(destination_file)) is False:
            break
    else:
        assert False, "Configuration was not deleted in the environment within the expected time"

    source_file.write_text("updated config content")
    waited = 0.0
    updated = False
    while waited < MAX_WAIT_SECONDS:
        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
        if not local_environment.exists(str(destination_file)):
            continue
        updated_content = local_environment.read_file(str(destination_file))
        if updated_content == "updated config content":
            break
    else:
        assert False, "Configuration was not updated in the environment within the expected time"

    config_service.stop_synchronizing_environment(test_project, task_id)
    source_file.write_text("post-stop config content")
    time.sleep(0.5)
    final_content = local_environment.read_file(str(destination_file))
    assert final_content == "updated config content", (
        "Configuration should not be updated after stopping synchronization"
    )


def test_plugin_synchronization_single_file(
    tmp_path: Path,
    test_project: Project,
    local_environment: LocalEnvironment,
    test_config_service: LocalConfigService,
) -> None:
    config_home = test_config_service.config_home_local
    source_file = config_home / "test_config"
    source_file.write_text("initial config content")
    destination_file = SANDBOX_DESTINATION
    _test_synchronization(test_config_service, local_environment, test_project, source_file, destination_file)


def test_plugin_synchronization_directory(
    tmp_path: Path,
    test_project: Project,
    local_environment: LocalEnvironment,
    test_config_service: LocalConfigService,
) -> None:
    config_home = test_config_service.config_home_local
    subdirectory = config_home / "test_config"
    subdirectory.mkdir(parents=True, exist_ok=True)
    source_file = subdirectory / "file.txt"
    source_file.write_text("initial config content")
    destination = SANDBOX_DESTINATION / "file.txt"
    _test_synchronization(test_config_service, local_environment, test_project, source_file, destination)
