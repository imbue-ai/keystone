from typing import cast

from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.config.settings import SculptorSettings
from sculptor.services.data_model_service.api import TaskDataModelService
from sculptor.services.git_repo_service.service_collection import get_git_repo_service_collection
from sculptor.services.task_service.data_types import TaskServiceCollection
from sculptor.services.task_service.threaded_implementation import LocalThreadTaskService


def get_task_service_collection(
    concurrency_group: ConcurrencyGroup,
    settings: SculptorSettings,
    should_start_image_downloads_in_background: bool | None = None,
) -> TaskServiceCollection:
    services = get_git_repo_service_collection(concurrency_group, settings, should_start_image_downloads_in_background)
    task_service = LocalThreadTaskService(
        concurrency_group=concurrency_group.make_concurrency_group("task_service"),
        settings=settings,
        data_model_service=cast(TaskDataModelService, services.data_model_service),
        environment_service=services.environment_service,
        config_service=services.config_service,
        git_repo_service=services.git_repo_service,
        task_sync_dir=settings.task_sync_path,
        project_service=services.project_service,
    )

    return TaskServiceCollection(
        settings=settings,
        data_model_service=services.data_model_service,
        task_service=task_service,
        environment_service=services.environment_service,
        config_service=services.config_service,
        git_repo_service=services.git_repo_service,
        project_service=services.project_service,
    )
