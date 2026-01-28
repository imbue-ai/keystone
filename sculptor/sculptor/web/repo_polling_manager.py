import datetime
from contextlib import contextmanager
from queue import Queue
from typing import Generator

from loguru import logger

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.itertools import generate_flattened
from sculptor.database.models import Project
from sculptor.primitives.ids import RequestID
from sculptor.primitives.threads import StopGapBackgroundPollingStreamSource
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.data_model_service.api import CompletedTransaction
from sculptor.services.git_repo_service.api import ReadOnlyGitRepo
from sculptor.services.git_repo_service.error_types import GitRepoError
from sculptor.services.project_service.default_implementation import ProjectNotFoundError
from sculptor.web.data_types import StreamingUpdateSourceTypes
from sculptor.web.derived import LocalRepoInfo


class _LocalRepoInfoPollingManager:
    def __init__(
        self,
        services: CompleteServiceCollection,
        queue: Queue[StreamingUpdateSourceTypes],
        concurrency_group: ConcurrencyGroup,
    ):
        self._services = services
        self._queue = queue
        self._concurrency_group = concurrency_group
        self._sources_by_project_id: dict[ProjectID, StopGapBackgroundPollingStreamSource[LocalRepoInfo]] = {}

    def initialize(self) -> None:
        active_projects = self._services.project_service.get_active_projects()

        for project in active_projects:
            if project.is_deleted:
                continue
            self._ensure_polling_for_project(project.object_id)

    def update_pollers_based_on_stream(self, models: list[StreamingUpdateSourceTypes]) -> None:
        updated_models = (m.updated_models for m in models if isinstance(m, CompletedTransaction))
        for updated_model in generate_flattened(updated_models):
            if isinstance(updated_model, Project):
                if updated_model.is_deleted:
                    self._stop_polling_for_project(updated_model.object_id)
                else:
                    self._ensure_polling_for_project(updated_model.object_id)

    def _ensure_polling_for_project(self, project_id: ProjectID) -> None:
        if project_id in self._sources_by_project_id:
            return
        polling_callback = _LocalRepoInfoExfiltrationCallback(self._services, project_id)
        # TODO: initializing as StopGapBackgroundPollingStreamSource[LocalRepoInfo] doesn't work for some reason
        source: StopGapBackgroundPollingStreamSource = StopGapBackgroundPollingStreamSource(
            polling_callback=polling_callback,
            # TODO: the contents of self._queue are not necessarily LocalRepoInfo
            output_queue=self._queue,
            check_interval_in_seconds=_GIT_STATUS_POLL_SECONDS,
            concurrency_group=self._concurrency_group,
        )
        source.start()
        self._sources_by_project_id[project_id] = source

    def _stop_polling_for_project(self, project_id: ProjectID) -> None:
        source = self._sources_by_project_id.pop(project_id, None)
        if source is not None:
            source.stop()

    def shutdown(self) -> None:
        for project_id in list(self._sources_by_project_id.keys()):
            self._stop_polling_for_project(project_id)


@contextmanager
def manage_local_repo_info_polling(
    services: CompleteServiceCollection,
    queue: Queue[StreamingUpdateSourceTypes],
    concurrency_group: ConcurrencyGroup,
) -> Generator[_LocalRepoInfoPollingManager, None, None]:
    manager = _LocalRepoInfoPollingManager(services=services, queue=queue, concurrency_group=concurrency_group)
    try:
        yield manager
    finally:
        manager.shutdown()


class _LocalRepoInfoExfiltrationCallback:
    """
    This is a stopgap until we implement a proper service-oriented watcher stream in the git repo service
    """

    def __init__(self, services: CompleteServiceCollection, project_id: ProjectID):
        self.services = services
        self.project_id = project_id
        self._first_failure_since_last_success: tuple[datetime.datetime, Exception] | None = None

    def __call__(self) -> LocalRepoInfo | None:
        try:
            status = read_local_repo_info(services=self.services, project_id=self.project_id)
            self._first_failure_since_last_success = None
            return status
        except Exception as e:
            if self._first_failure_since_last_success is None:
                self._first_failure_since_last_success = (datetime.datetime.now(), e)
                log_exception(
                    e, message="Failed to get user's git repository state", priority=ExceptionPriority.LOW_PRIORITY
                )
                return None
            original_time, original_exc = self._first_failure_since_last_success
            msg = "Still failing to get user's git repository state: {} (original was {} @ {})"
            logger.info(msg, e, type(original_exc), original_time.isoformat())
            return None


def read_local_repo_info(services: CompleteServiceCollection, project_id: ProjectID) -> LocalRepoInfo | None:
    with _open_repo_for_read(services, project_id) as repo:
        # TODO: add a top-level repo health check
        #       as otherwise this will error out without
        #       context to the user, if the repo becomes
        #       invalid
        current_branch = _get_branch_unless_repo_missing(repo)
        if current_branch is None:
            return None
        status = repo.get_current_status(is_read_only_and_lockless=True)
        return LocalRepoInfo(status=status, current_branch=current_branch, project_id=project_id)


def _get_branch_unless_repo_missing(repo: ReadOnlyGitRepo) -> str | None:
    try:
        return repo.get_current_git_branch()
    except FileNotFoundError as e:
        logger.debug("Failed to get current git branch because the repo doesn't exist: {}", e)
        return None
    except GitRepoError as e:
        if e.branch_name is not None:
            raise
        logger.debug("There is no current branch: {}", e)
        return None


@contextmanager
def _open_repo_for_read(
    services: CompleteServiceCollection, project_id: ProjectID
) -> Generator[ReadOnlyGitRepo, None, None]:
    with services.data_model_service.open_transaction(RequestID()) as transaction:
        project = transaction.get_project(project_id)
    if not project:
        raise ProjectNotFoundError(f"Project {project_id} not found")
    with services.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
        yield repo


_GIT_STATUS_POLL_SECONDS = 3.0
