import os
import shutil
import threading
from collections import defaultdict
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import MutableMapping
from urllib.parse import urljoin

import requests
from loguru import logger
from pydantic import PrivateAttr
from typeid.errors import InvalidTypeIDStringException

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TypeIDPrefixMismatchError
from imbue_core.async_monkey_patches import log_exception
from imbue_core.errors import ExpectedError
from imbue_core.pydantic_serialization import FrozenModel
from imbue_core.thread_utils import ObservableThread
from sculptor.agents.default.constants import GITLAB_PROJECT_URL_STATE_FILE
from sculptor.agents.default.constants import GITLAB_TOKEN_STATE_FILE
from sculptor.config.settings import SculptorSettings
from sculptor.constants import GatewayRemoteAPIEndpoints
from sculptor.database.models import Project
from sculptor.primitives.ids import OrganizationReference
from sculptor.primitives.ids import RequestID
from sculptor.primitives.ids import get_deterministic_typeid_suffix
from sculptor.services.config_service.api import ConfigService
from sculptor.services.data_model_service.api import DataModelService
from sculptor.services.data_model_service.data_types import DataModelTransaction
from sculptor.services.environment_service.environments.image_tags import get_non_testing_environment_prefix
from sculptor.services.environment_service.providers.docker.environment_utils import destroy_outdated_docker_images
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.services.project_service.api import ProjectService
from sculptor.services.project_service.constants import GITLAB_TOKEN_EXPIRATION_FILE
from sculptor.services.project_service.constants import PROJECT_CONFIGURATIONS_PATH
from sculptor.utils.build import get_sculptor_folder

_PROJECT_CONFIG_FILENAME = "project_config.json"


class ProjectNotFoundError(ExpectedError):
    pass


class ProjectConfiguration(FrozenModel):
    """Project-level configuration settings."""

    gitlab_token: str | None = None
    gitlab_url: str | None = None
    token_expires_at_iso: str | None = None


class DefaultProjectService(ProjectService):
    settings: SculptorSettings
    data_model_service: DataModelService
    config_service: ConfigService
    git_repo_service: GitRepoService

    _cached_projects: dict[tuple[OrganizationReference, Path], Project] = PrivateAttr(default_factory=dict)
    _project_initialization_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _initialized_project: Project | None = PrivateAttr(default=None)
    _current_project_path: Path | None = PrivateAttr(default=None)
    # Set of currently active projects, where the first one is the most recently activated
    _active_projects: tuple[Project, ...] = PrivateAttr(default_factory=tuple)
    _project_activation_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    # Path monitoring thread fields
    _monitoring_thread: ObservableThread | None = PrivateAttr(default=None)
    _gitlab_token_refresh_thread: ObservableThread | None = PrivateAttr(default=None)
    _stop_event: threading.Event | None = PrivateAttr(default=None)

    # Maps from every project we know to the lock that guards token refresh for it
    _token_refresh_locks: MutableMapping[ProjectID, threading.Lock] = PrivateAttr(
        default_factory=lambda: defaultdict(threading.Lock)
    )

    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def start(self) -> None:
        self._stop_event = threading.Event()
        self._start_path_monitoring_thread()

        if self.needs_gitlab_token():
            self._start_gitlab_token_refresh_thread()

    def stop(self) -> None:
        logger.info("Stopping project path monitoring thread")
        if self._stop_event is not None:
            self._stop_event.set()
        if self._monitoring_thread is not None:
            self._monitoring_thread.join(timeout=5)
        if self._gitlab_token_refresh_thread is not None:
            self._gitlab_token_refresh_thread.join(timeout=5)
        logger.info("Project path monitoring thread joined")

    def needs_gitlab_token(self) -> bool:
        """True iff this Sculptor is configured to utilize features that require the Gitlab token."""
        user_config = self.config_service.get_user_config()
        return bool(user_config and user_config.is_repo_backup_enabled)

    def get_active_projects(self) -> tuple[Project, ...]:
        with self._project_activation_lock:
            return tuple(p for p in self._active_projects if not p.is_deleted)

    def activate_project(self, project: Project) -> None:
        with self._project_activation_lock:
            update_most_recently_used_project(project_id=project.object_id)
            # move the project to the front of the list
            self._active_projects = (project,) + tuple(p for p in self._active_projects if p != project)

    def initialize_project(
        self, project_path: Path, organization_reference: OrganizationReference, transaction: DataModelTransaction
    ) -> Project:
        project = self._ensure_project_is_initialized(project_path, organization_reference, transaction)
        self._provision_gitlab_token_for_project(project)
        return project

    def _ensure_project_is_initialized(
        self, project_path: Path, organization_reference: OrganizationReference, transaction: DataModelTransaction
    ) -> Project:
        project_name = project_path.name
        project_id = self._get_project_id(transaction, project_path, organization_reference)

        user_git_repo_url = f"file://{project_path}"

        our_git_repo_url: str | None = os.environ.get("GITLAB_PROJECT_URL")
        logger.info("Mirror url {} loaded", our_git_repo_url)

        current_project = Project(
            object_id=project_id,
            organization_reference=organization_reference,
            name=project_name,
            user_git_repo_url=user_git_repo_url,
            our_git_repo_url=our_git_repo_url,
        )
        transaction.upsert_project(current_project)
        return current_project

    def _get_project_id(
        self, transaction: DataModelTransaction, project_path: Path, organization_reference: OrganizationReference
    ) -> ProjectID:
        existing_projects = transaction.get_projects(organization_reference)
        for existing_project in existing_projects:
            # Legacy projects can have IDs different from the current deterministic creation scheme.
            if existing_project.user_git_repo_url is None:
                continue
            if Path(existing_project.get_local_user_path()).absolute() == Path(project_path).absolute():
                return existing_project.object_id
        return ProjectID(get_deterministic_typeid_suffix(str(organization_reference) + str(project_path)))

    # FIXME(andrew): please delete this if the one below is sufficient
    # def _old_setup_gitlab_mirroring(self, project: Project) -> None:
    #     """Set up GitLab mirroring for the project if enabled."""
    #     user_config = get_user_config_instance()
    #     if not user_config or not user_config.is_repo_backup_enabled:
    #         return
    #
    #     if not self.settings.is_imbue_gateway_configured:
    #         return
    #
    #     try:
    #         project_path = Path(project.user_git_repo_url.replace("file://", ""))
    #         logger.info("Setting up GitLab mirroring for project: {}", project_path)
    #
    #         result = run_blocking(command=["git", "rev-parse", "HEAD"], cwd=project_path, is_output_traced=False)
    #         base_commit_hash = result.stdout.strip()
    #         logger.info("Base commit hash: {}", base_commit_hash)
    #
    #         gateway_url = urljoin(
    #             self.settings.IMBUE_GATEWAY_BASE_URL, GatewayRemoteAPIEndpoints.GITLAB_ANONYMOUS_PAT_ENDPOINT
    #         )
    #
    #         logger.info("Gateway url for PAT is {}", gateway_url)
    #
    #         access_token = None
    #         gitlab_project_url = None
    #
    #         # Disabling mirroring for now.
    #         if access_token and gitlab_project_url:
    #             logger.success("Successfully retrieved GitLab access token from imbue-gateway")
    #             os.environ[GITLAB_TOKEN_NAME] = access_token
    #             os.environ["GITLAB_PROJECT_URL"] = gitlab_project_url
    #             logger.info("Gitlab project url: {}", gitlab_project_url)
    #
    #             if gitlab_project_url.startswith("https://"):
    #                 base_gitlab_url = gitlab_project_url.split("/", 3)[0] + "//" + gitlab_project_url.split("/", 3)[2]
    #                 os.environ["GITLAB_URL"] = base_gitlab_url
    #         else:
    #             logger.info("imbue-gateway response missing required fields")
    #             gitlab_url = os.getenv("GITLAB_URL", "https://gitlab.com")
    #             os.environ["GITLAB_URL"] = gitlab_url
    #     except Exception as e:
    #         logger.info("Failed to retrieve GitLab access token from imbue-gateway: {}", e)
    #         gitlab_url = os.getenv("GITLAB_URL", "https://gitlab.com")
    #         os.environ["GITLAB_URL"] = gitlab_url

    def _provision_gitlab_token_for_project(self, project: Project) -> None:
        with self._token_refresh_locks[project.object_id]:
            if not self.needs_gitlab_token():
                logger.info("GitLab mirroring disabled or user config not available")
                return

            try:
                if not project.user_git_repo_url or not project.user_git_repo_url.startswith("file://"):
                    logger.error("Project does not have a valid local git repository URL")
                    return

                project_path = Path(project.get_local_user_path())
                logger.info("Provisioning GitLab token for project: {}", project_path)

                # Use git_repo_service to get the current commit hash
                with self.git_repo_service.open_local_user_git_repo_for_read(project) as repo:
                    base_commit_hash = repo.get_current_commit_hash()
                logger.debug("Base commit hash: {}", base_commit_hash)

                user_config = self.config_service.get_user_config()

                settings = self.settings
                gateway_url = urljoin(
                    settings.IMBUE_GATEWAY_BASE_URL, GatewayRemoteAPIEndpoints.GITLAB_ANONYMOUS_PAT_ENDPOINT
                )
                params = {"base_commit_hash": base_commit_hash, "user_id": user_config.anonymous_access_token}

                logger.debug("Gateway url for PAT is {}", gateway_url)

                access_token = None
                gitlab_project_url = None

                logger.debug("Gitlab mirroring is enabled: {}", settings.is_imbue_gateway_configured)
                if settings.is_imbue_gateway_configured:
                    # integration test
                    if settings.GITLAB_DEFAULT_TOKEN != "":
                        access_token = settings.GITLAB_DEFAULT_TOKEN
                        gitlab_project_url = IMBUE_TESTING_GITLAB_MIRROR_REPO_URL
                    else:
                        try:
                            response = requests.post(gateway_url, params=params, timeout=5)
                            response.raise_for_status()

                            response_data = response.json()
                            access_token = response_data.get("accessToken")
                            gitlab_project_url = response_data.get("url")
                        except requests.exceptions.Timeout:
                            logger.error("Call to imbue_gateway reached local timeout, continuing without mirroring.")
                else:
                    logger.info("GitLab mirroring disabled, PAT not generated")

                if access_token and gitlab_project_url:
                    logger.info("Retrieved GitLab access token from imbue-gateway")
                    logger.debug("Gitlab project url: {}", gitlab_project_url)

                    expiration_time = (datetime.now() + timedelta(days=30)).isoformat()

                    configuration = ProjectConfiguration(
                        gitlab_token=access_token, gitlab_url=gitlab_project_url, token_expires_at_iso=expiration_time
                    )
                    project_config_path = PROJECT_CONFIGURATIONS_PATH / str(project.object_id)
                    project_config_path.mkdir(parents=True, exist_ok=True)
                    (project_config_path / GITLAB_TOKEN_STATE_FILE).write_text(access_token)
                    (project_config_path / GITLAB_PROJECT_URL_STATE_FILE).write_text(gitlab_project_url)
                    (project_config_path / GITLAB_TOKEN_EXPIRATION_FILE).write_text(expiration_time)

                    logger.debug("Successfully sent GitLab configuration to project: {}", project.object_id)
                else:
                    logger.info("Failed to retrieve GitLab access token from imbue-gateway")

            except Exception as e:
                logger.error("Failed to provision GitLab token for project {}: {}", project.object_id, e)

    def _start_path_monitoring_thread(self) -> None:
        """Start the background thread that monitors project paths."""
        if self._monitoring_thread is not None and self._monitoring_thread.is_alive():
            logger.info("Project path monitoring thread is already running")
            return

        self._monitoring_thread = self.concurrency_group.start_new_thread(
            target=self._monitor_project_paths,
            name="ProjectPathMonitor",
            daemon=True,
            args=(self._stop_event,),
        )
        logger.info("Started project path monitoring thread")

    def _start_gitlab_token_refresh_thread(self) -> None:
        if self._gitlab_token_refresh_thread is not None and self._gitlab_token_refresh_thread.is_alive():
            logger.info("Project gitlab token refresh thread is already running")
            return

        self._gitlab_token_refresh_thread = self.concurrency_group.start_new_thread(
            target=self._refresh_gitlab_token,
            name="ProjectGitlabTokenRefresh",
            daemon=True,
            args=(self._stop_event,),
        )
        logger.info("Started project path monitoring thread")

    def _monitor_project_paths(self, stop_event: threading.Event, interval_in_seconds: float = 10.0) -> None:
        """Background thread that continuously monitors project path accessibility."""
        logger.info("Project path monitoring thread started")

        while not stop_event.is_set():
            try:
                active_projects = self.get_active_projects()

                for project in active_projects:
                    self._check_and_update_project_accessibility(project)

                # Wait for the monitoring interval or until stop event is set
                # wait() returns True if the event is set, False if timeout occurred
                if stop_event.wait(timeout=interval_in_seconds):
                    break  # Stop event was set, exit the loop

            except Exception as e:
                log_exception(e, "Error in project path monitoring")
                # Continue monitoring even if there's an error, but check for stop event
                if stop_event.wait(timeout=interval_in_seconds):
                    break  # Stop event was set, exit the loop

        logger.info("Project path monitoring thread stopped")

    def _refresh_gitlab_token(self, stop_event: threading.Event, interval_in_seconds: float = 60.0 * 60.0) -> None:
        """Background thread that ensures the gitlab token is sufficiently up-to-date."""
        logger.info("Project gitlab token refresh thread started")

        while not stop_event.is_set():
            try:
                active_projects = self.get_active_projects()

                for project in active_projects:
                    # kick off a refresh if needed
                    config = self._get_current_project_configuration(project)
                    if self.settings.is_imbue_gateway_configured:
                        if not config.gitlab_token or not config.gitlab_url or _is_token_expired(config):
                            self._provision_gitlab_token_for_project(project)

                # Wait for the monitoring interval or until stop event is set
                # wait() returns True if the event is set, False if timeout occurred
                if stop_event.wait(timeout=interval_in_seconds):
                    break  # Stop event was set, exit the loop

            except Exception as e:
                log_exception(e, "Error in project gitlab token refresh")
                # Continue monitoring even if there's an error, but check for stop event
                if stop_event.wait(timeout=interval_in_seconds):
                    break  # Stop event was set, exit the loop

        logger.info("Project gitlab token refresh thread stopped")

    def _check_and_update_project_accessibility(self, project: Project) -> None:
        """Check if a project's path exists and update its accessibility status if changed."""
        if not project.user_git_repo_url or not project.user_git_repo_url.startswith("file://"):
            return

        project_path = Path(project.user_git_repo_url.replace("file://", ""))
        # Check if the path exists and is accessible
        try:
            current_accessible = project_path.exists() and project_path.is_dir()
        except OSError:
            current_accessible = False

        # If the status changed, update the project in the database
        if current_accessible == project.is_path_accessible:
            return
        logger.info(
            "Project path accessibility changed for {}: {} -> {}",
            project.name,
            project.is_path_accessible,
            current_accessible,
        )

        try:
            # Create a new project instance with updated accessibility using evolve pattern
            updated_project = project.evolve(project.ref().is_path_accessible, current_accessible)

            # Open a transaction to update the project
            # Use is_user_request=True to ensure updates are broadcast to frontend
            with self.data_model_service.open_transaction(request_id=RequestID(), is_user_request=True) as transaction:
                # Update the project in the database
                # FIXME: Read the project from the transaction to avoid upserting stale data
                transaction.upsert_project(updated_project)

                # Update our cached version
                with self._project_activation_lock:
                    # Find and update the project in active projects
                    updated_projects = []
                    for p in self._active_projects:
                        if p.object_id == project.object_id:
                            # Replace with the updated project instance
                            updated_projects.append(updated_project)
                        else:
                            updated_projects.append(p)
                    self._active_projects = tuple(updated_projects)

                logger.info("Successfully updated project {} accessibility to {}", project.name, current_accessible)
        except Exception as e:
            log_exception(e, "Failed to update project {project} accessibility", project=project.name)

    def delete_project(self, project: Project, transaction: DataModelTransaction) -> None:
        cached_repo_path = project.get_cached_repo_path()
        project_id = project.object_id
        updated_project = project.evolve(project.ref().is_deleted, True)
        # FIXME: Read the project from the transaction to avoid upserting stale data
        transaction.upsert_project(updated_project)
        with self._project_activation_lock:
            # Find and update the project in active projects
            self._active_projects = tuple(p for p in self._active_projects if p.object_id != project.object_id)
        logger.info("Cleaning up cached repo path: {}", cached_repo_path)
        if os.path.exists(cached_repo_path):
            shutil.rmtree(cached_repo_path)
        logger.info("Destroying outdated docker containers and images for project {}", project_id)
        # NOTE: most of the images will not actually get cleaned up at this point because task deletion is async
        # and until the containers for the tasks are deleted, the images cannot be deleted.
        destroy_outdated_docker_images(
            lambda img: img.startswith(get_non_testing_environment_prefix()) and str(project_id) in img,
            self.concurrency_group,
        )

    def _get_current_project_configuration(self, project: Project) -> ProjectConfiguration:
        gitlab_token: str | None = None
        gitlab_url: str | None = None
        token_expires_at_iso: str | None = None
        project_config_path = PROJECT_CONFIGURATIONS_PATH / str(project.object_id)
        if project_config_path.exists():
            gitlab_token_file = project_config_path / GITLAB_TOKEN_STATE_FILE
            if gitlab_token_file.exists():
                gitlab_token = gitlab_token_file.read_text()
            gitlab_url_file = project_config_path / GITLAB_PROJECT_URL_STATE_FILE
            if gitlab_url_file.exists():
                gitlab_url = gitlab_url_file.read_text()
            token_expires_at_file = project_config_path / GITLAB_TOKEN_EXPIRATION_FILE
            if token_expires_at_file.exists():
                token_expires_at_iso = token_expires_at_file.read_text()

        return ProjectConfiguration(
            gitlab_token=gitlab_token,
            gitlab_url=gitlab_url,
            token_expires_at_iso=token_expires_at_iso,
        )


def get_most_recently_used_project_id() -> ProjectID | None:
    sculptor_folder = get_sculptor_folder()
    mru_file = sculptor_folder / "most_recently_used_project.txt"
    if mru_file.exists():
        with open(mru_file, "r") as f:
            project_id_str = f.read().strip()
            try:
                return ProjectID(project_id_str)
            except (TypeIDPrefixMismatchError, InvalidTypeIDStringException):
                logger.info("Invalid project ID found in most_recently_used_project.txt: {}", project_id_str)
    return None


def update_most_recently_used_project(project_id: ProjectID) -> None:
    sculptor_folder = get_sculptor_folder()
    mru_file = sculptor_folder / "most_recently_used_project.txt"
    with open(mru_file, "w") as f:
        f.write(str(project_id))


# FIXME: rename this -- is about expired or expiring soon
def _is_token_expired(configuration: ProjectConfiguration) -> bool:
    token_expires_at_iso = configuration.token_expires_at_iso
    if not token_expires_at_iso:
        logger.debug("No token expiration time set, considering token expired")
        return True

    try:
        expires_at = datetime.fromisoformat(token_expires_at_iso)
        now = datetime.now()
        one_day_from_now = now + timedelta(days=1)
        is_expired_or_expiring_soon = now >= expires_at or expires_at <= one_day_from_now

        if is_expired_or_expiring_soon:
            if now >= expires_at:
                logger.debug("GitLab token expired at {}, current time is {}", expires_at, now)
            else:
                logger.debug("GitLab token expires at {} (within 24 hours)", expires_at, now)
        else:
            logger.debug("GitLab token is still valid, expires at {}", expires_at)

        return is_expired_or_expiring_soon
    except ValueError as e:
        logger.error("Invalid token expiration format: {}, considering token expired", e)
        return True


IMBUE_TESTING_GITLAB_MIRROR_REPO_URL: str = (
    "https://gitlab.com/generally-intelligent/gitlab-management-test-repos/integration_testing.git"
)
