import tempfile
from pathlib import Path
from typing import Generator
from typing import cast
from unittest.mock import patch

import pytest

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.secrets_utils import Secret
from sculptor.config.settings import SculptorSettings
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.config_service.api import ConfigService
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.local_implementation import LocalConfigService
from sculptor.services.config_service.user_config import get_default_user_config_instance
from sculptor.services.config_service.user_config import set_user_config_instance
from sculptor.services.config_service.utils import populate_credentials_file
from sculptor.services.data_model_service.api import DataModelService
from sculptor.services.data_model_service.api import TaskDataModelService
from sculptor.services.data_model_service.sql_implementation import SQLDataModelService
from sculptor.services.environment_service.api import EnvironmentService
from sculptor.services.environment_service.default_implementation import DefaultEnvironmentService
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.services.git_repo_service.default_implementation import DefaultGitRepoService
from sculptor.services.local_sync_service.default_implementation import DefaultLocalSyncService
from sculptor.services.project_service.api import ProjectService
from sculptor.services.project_service.default_implementation import DefaultProjectService
from sculptor.services.task_service.api import TaskService
from sculptor.services.task_service.threaded_implementation import LocalThreadTaskService


@pytest.fixture
def silly_global_config() -> Generator[UserConfig, None, None]:
    config = get_default_user_config_instance()
    config = config.model_copy(update={"are_suggestions_enabled": True})
    set_user_config_instance(config)
    yield config
    set_user_config_instance(None)


@pytest.fixture
def patch_mutagen_data_directory() -> Generator[None, None, None]:
    with tempfile.TemporaryDirectory(prefix="mutagen_pytest_") as tmp_dir:
        # Each test that uses this fixture gets its own mutagen data directory - that way each test has its own mutagen daemon.
        with patch(
            "sculptor.services.local_sync_service.mutagen_utils._mutagen_data_directory_env",
            return_value=("MUTAGEN_DATA_DIRECTORY", tmp_dir),
        ):
            yield


# NOTE: We use the leading underscore notation to highlight the fact that services should not be used on their own outside of this module.
# (They need to be started and stopped in a controlled manner so always require the whole collection instead of individual services.)


@pytest.fixture
def _test_config_service(
    tmp_path: Path,
    silly_global_config: UserConfig,
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings: SculptorSettings,
) -> ConfigService:
    credentials_file_path = tmp_path / ".sculptor" / "credentials.json"
    populate_credentials_file(
        path=credentials_file_path,
        credentials=Credentials(
            anthropic=AnthropicApiKey(
                anthropic_api_key=Secret(secret_value="sk-ant-fake-api-key"), generated_from_oauth=False
            )
        ),
    )
    secret_file_path = tmp_path / ".sculptor/.env"
    service = LocalConfigService(
        secret_file_path=secret_file_path,
        credentials_file_path=credentials_file_path,
        concurrency_group=test_root_concurrency_group.make_concurrency_group("config_service"),
        config_home_local=test_settings.CONFIG_HOME,
    )
    return service


@pytest.fixture
def _test_data_model_service(
    test_settings: SculptorSettings, test_root_concurrency_group: ConcurrencyGroup
) -> DataModelService:
    return SQLDataModelService.build_from_settings(
        test_settings, test_root_concurrency_group.make_concurrency_group("data_model_service")
    )


@pytest.fixture
def _test_git_repo_service(test_root_concurrency_group: ConcurrencyGroup) -> GitRepoService:
    return DefaultGitRepoService(
        concurrency_group=test_root_concurrency_group.make_concurrency_group("git_repo_service")
    )


@pytest.fixture
def _test_project_service(
    test_settings: SculptorSettings,
    _test_config_service: ConfigService,
    _test_data_model_service: DataModelService,
    _test_git_repo_service: GitRepoService,
    test_root_concurrency_group: ConcurrencyGroup,
) -> ProjectService:
    return DefaultProjectService(
        settings=test_settings,
        data_model_service=_test_data_model_service,
        config_service=_test_config_service,
        git_repo_service=_test_git_repo_service,
        concurrency_group=test_root_concurrency_group.make_concurrency_group("project_service"),
    )


@pytest.fixture
def _test_environment_service(
    test_settings: SculptorSettings,
    _test_data_model_service: DataModelService,
    _test_git_repo_service: GitRepoService,
    _test_project_service: ProjectService,
    test_root_concurrency_group: ConcurrencyGroup,
) -> EnvironmentService:
    return DefaultEnvironmentService(
        settings=test_settings,
        data_model_service=_test_data_model_service,
        git_repo_service=_test_git_repo_service,
        concurrency_group=test_root_concurrency_group.make_concurrency_group("environment_service"),
        should_start_image_downloads_in_background=False,
    )


@pytest.fixture
def _test_task_service(
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings: SculptorSettings,
    _test_config_service: ConfigService,
    _test_data_model_service: DataModelService,
    _test_git_repo_service: GitRepoService,
    _test_environment_service: EnvironmentService,
    _test_project_service: ProjectService,
) -> TaskService:
    return LocalThreadTaskService(
        settings=test_settings,
        config_service=_test_config_service,
        data_model_service=cast(TaskDataModelService, _test_data_model_service),
        git_repo_service=_test_git_repo_service,
        environment_service=_test_environment_service,
        project_service=_test_project_service,
        concurrency_group=test_root_concurrency_group.make_concurrency_group("task_service"),
        task_sync_dir=test_settings.task_sync_path,
    )


@pytest.fixture
def _test_local_sync_service(
    _test_git_repo_service: GitRepoService,
    _test_data_model_service: DataModelService,
    _test_task_service: TaskService,
    test_root_concurrency_group: ConcurrencyGroup,
    patch_mutagen_data_directory: None,
) -> Generator[DefaultLocalSyncService, None, None]:
    yield DefaultLocalSyncService(
        git_repo_service=_test_git_repo_service,
        data_model_service=_test_data_model_service,
        task_service=_test_task_service,
        concurrency_group=test_root_concurrency_group.make_concurrency_group("local_sync_service"),
    )


@pytest.fixture
def test_service_collection(
    test_settings: SculptorSettings,
    _test_config_service: ConfigService,
    _test_data_model_service: DataModelService,
    _test_git_repo_service: GitRepoService,
    _test_environment_service: EnvironmentService,
    _test_task_service: TaskService,
    _test_local_sync_service: DefaultLocalSyncService,
    _test_project_service: ProjectService,
) -> Generator[CompleteServiceCollection, None, None]:
    services = CompleteServiceCollection(
        settings=test_settings,
        data_model_service=_test_data_model_service,
        task_service=_test_task_service,
        environment_service=_test_environment_service,
        config_service=_test_config_service,
        git_repo_service=_test_git_repo_service,
        local_sync_service=_test_local_sync_service,
        project_service=_test_project_service,
    )
    with services.run_all():
        yield services
