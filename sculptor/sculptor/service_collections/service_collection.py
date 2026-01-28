from contextlib import contextmanager
from typing import Generator

from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.config.settings import SculptorSettings
from sculptor.services.local_sync_service.default_implementation import DefaultLocalSyncService
from sculptor.services.local_sync_service.service_collection import LocalSyncServiceCollection
from sculptor.services.task_service.data_types import TaskServiceCollection
from sculptor.services.task_service.service_collection import get_task_service_collection


def _resolve_local_sync_service(
    service_collection: TaskServiceCollection, concurrency_group: ConcurrencyGroup
) -> DefaultLocalSyncService:
    return DefaultLocalSyncService(
        git_repo_service=service_collection.git_repo_service,
        data_model_service=service_collection.data_model_service,
        task_service=service_collection.task_service,
        concurrency_group=concurrency_group.make_concurrency_group("local_sync_service"),
    )


class CompleteServiceCollection(LocalSyncServiceCollection):
    @contextmanager
    def run_all(self) -> Generator[None, None, None]:
        # The order is important here - it reflects the dependencies between services.
        with (
            self.config_service.run(log_runtimes=True),
            self.data_model_service.run(log_runtimes=True),
            self.project_service.run(log_runtimes=True),
            self.environment_service.run(log_runtimes=True),
            self.git_repo_service.run(log_runtimes=True),
            self.task_service.run(log_runtimes=True),
            self.local_sync_service.run(log_runtimes=True),
        ):
            yield


def get_services(
    concurrency_group: ConcurrencyGroup,
    settings: SculptorSettings,
    should_start_image_downloads_in_background: bool | None = None,
) -> CompleteServiceCollection:
    services = get_task_service_collection(concurrency_group, settings, should_start_image_downloads_in_background)
    return CompleteServiceCollection(
        settings=settings,
        data_model_service=services.data_model_service,
        task_service=services.task_service,
        environment_service=services.environment_service,
        config_service=services.config_service,
        git_repo_service=services.git_repo_service,
        project_service=services.project_service,
        local_sync_service=_resolve_local_sync_service(services, concurrency_group),
    )
