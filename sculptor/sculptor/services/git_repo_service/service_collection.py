from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.config.settings import SculptorSettings
from sculptor.services.config_service.local_implementation import LocalConfigService
from sculptor.services.data_model_service.sql_implementation import SQLDataModelService
from sculptor.services.environment_service.default_implementation import DefaultEnvironmentService
from sculptor.services.environment_service.default_implementation import (
    SHOULD_START_IMAGE_DOWNLOADS_IN_BACKGROUND_DEFAULT,
)
from sculptor.services.git_repo_service.data_types import GitRepoServiceCollection
from sculptor.services.git_repo_service.default_implementation import DefaultGitRepoService
from sculptor.services.project_service.default_implementation import DefaultProjectService


def get_git_repo_service_collection(
    concurrency_group: ConcurrencyGroup,
    settings: SculptorSettings,
    should_start_image_downloads_in_background: bool | None = None,
) -> GitRepoServiceCollection:
    data_model_service = SQLDataModelService.build_from_settings(
        settings, concurrency_group.make_concurrency_group("data_model_service")
    )
    config_service = LocalConfigService(
        concurrency_group=concurrency_group.make_concurrency_group("config_service"),
        config_home_local=settings.CONFIG_HOME,
    )
    git_repo_service = DefaultGitRepoService(
        concurrency_group=concurrency_group.make_concurrency_group("git_repo_service")
    )
    project_service = DefaultProjectService(
        concurrency_group=concurrency_group.make_concurrency_group("project_service"),
        settings=settings,
        data_model_service=data_model_service,
        config_service=config_service,
        git_repo_service=git_repo_service,
    )
    environment_service = DefaultEnvironmentService(
        settings=settings,
        data_model_service=data_model_service,
        git_repo_service=git_repo_service,
        concurrency_group=concurrency_group.make_concurrency_group("environment_service"),
        should_start_image_downloads_in_background=should_start_image_downloads_in_background
        if should_start_image_downloads_in_background is not None
        else SHOULD_START_IMAGE_DOWNLOADS_IN_BACKGROUND_DEFAULT,
    )
    return GitRepoServiceCollection(
        settings=settings,
        environment_service=environment_service,
        data_model_service=data_model_service,
        config_service=config_service,
        git_repo_service=git_repo_service,
        project_service=project_service,
    )
