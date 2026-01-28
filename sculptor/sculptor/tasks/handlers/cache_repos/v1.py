import datetime
import os
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from loguru import logger

from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.gitlab_management import GITLAB_TOKEN_NAME
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.primitives.ids import RequestID
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_devcontainer_json_path_from_repo_or_default,
)
from sculptor.services.task_service.data_types import ServiceCollectionForTask
from sculptor.services.task_service.errors import UserPausedTaskError

IMBUE_TESTING_GITLAB_MIRROR_REPO_URL = (
    "https://gitlab.com/generally-intelligent/gitlab-management-test-repos/integration_testing.git"
)


def _cache_repos_task_v1(
    services: ServiceCollectionForTask, shutdown_event: ReadOnlyEvent, on_started: Callable[[], None] | None = None
) -> None:
    if on_started is not None:
        on_started()
    settings = services.settings
    if settings.GITLAB_DEFAULT_TOKEN != "":
        os.environ[GITLAB_TOKEN_NAME] = settings.GITLAB_DEFAULT_TOKEN
        os.environ["GITLAB_PROJECT_URL"] = IMBUE_TESTING_GITLAB_MIRROR_REPO_URL
        os.environ["GITLAB_URL"] = "https://gitlab.com"

    with services.data_model_service.open_transaction(RequestID()) as transaction:
        all_projects = transaction.get_projects()
        for project in all_projects:
            if shutdown_event.is_set():
                raise CancelledByEventError()
            logger.info("Caching repo for project {}", project.name)
            if not project.user_git_repo_url:
                continue

            active_repo_path = Path(urlparse(project.user_git_repo_url).path)
            cached_repo_path = project.get_cached_repo_path()

            devcontainer_json_path = get_devcontainer_json_path_from_repo_or_default(active_repo_path)
            image_config = LocalDevcontainerImageConfig(
                devcontainer_json_path=str(devcontainer_json_path),
            )
            logger.info("Creating image for image_config={}", image_config)

            services.environment_service.ensure_image(
                image_config,
                secrets={},
                active_repo_path=active_repo_path,
                cached_repo_path=cached_repo_path,
                project_id=project.object_id,
                force_tarball_refresh=True,
                image_metadata=ImageMetadataV1.from_daily_cache(day=datetime.date.today()),
                shutdown_event=shutdown_event,
            )
            logger.info("Finished creating image for image_config={}", image_config)


def cache_repos_task_v1(
    services: ServiceCollectionForTask, shutdown_event: ReadOnlyEvent, on_started: Callable[[], None] | None = None
) -> None:
    try:
        _cache_repos_task_v1(services, shutdown_event, on_started)
    except CancelledByEventError as e:
        logger.info("Cache repos task was cancelled by shutdown event.")
        raise UserPausedTaskError() from e
