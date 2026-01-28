"""
The local implementation of the ConfigService.

Offers core functionality + uses the plugin system to manage configuration synchronization from users local machine to remote environments.

"""

import os
import time
from pathlib import Path
from threading import Event
from threading import Lock
from threading import Timer
from typing import Any
from typing import Callable
from typing import Final
from typing import Mapping
from typing import Sequence

import httpx
from dotenv import dotenv_values
from dotenv import set_key
from loguru import logger
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import PrivateAttr
from pydantic import ValidationError
from watchdog.events import FileSystemEvent
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.gitlab_management import GITLAB_TOKEN_NAME
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.secrets_utils import Secret
from sculptor.agents.default.claude_code_sdk.config_service_plugin import get_plugin as get_claude_code_plugin
from sculptor.database.models import Project
from sculptor.database.models import TaskID
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.errors import EnvironmentFailure
from sculptor.primitives.threads import ObservableThread
from sculptor.services.config_service.api import ConfigService
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import AnthropicCredentials
from sculptor.services.config_service.data_types import CLAUDE_CODE_CLIENT_ID
from sculptor.services.config_service.data_types import ClaudeOauthCredentials
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.data_types import GlobalConfiguration
from sculptor.services.config_service.data_types import OpenAIApiKey
from sculptor.services.config_service.data_types import REFRESH_TOKEN_EXPIRY_BUFFER_SECONDS
from sculptor.services.config_service.data_types import TokenResponse
from sculptor.services.config_service.plugin_system import ConfigServicePlugin
from sculptor.services.config_service.plugin_system import ConfigurationRule
from sculptor.services.config_service.plugin_system import apply_configuration_rule
from sculptor.services.config_service.plugin_system import resolve_placeholders
from sculptor.services.config_service.user_config import get_config_path
from sculptor.services.config_service.user_config import get_user_config_instance
from sculptor.services.config_service.user_config import save_config
from sculptor.services.config_service.user_config import set_user_config_instance
from sculptor.services.config_service.utils import populate_credentials_file
from sculptor.services.project_service.config_service_plugin import get_plugin as get_project_service_plugin
from sculptor.utils.build import get_sculptor_folder

SHARED_DOTENV_FILENAME: Final = ".env"
CREDENTIALS_FILENAME: Final = "credentials.json"


class _ConfigurationChangeHandler(FileSystemEventHandler):
    """
    Handle file system events for a specific configuration rule and project.

    Uses debounce logic to avoid handling rapid successive events.
    (E.g. a simple file edit in vim may trigger multiple events.)

    """

    def __init__(
        self,
        config_service: "LocalConfigService",
        plugin: ConfigServicePlugin,
        configuration_rule: ConfigurationRule,
        project: Project,
        same_file_only: Path | None = None,
    ):
        self._config_service = config_service
        self._configuration_rule = configuration_rule
        self._plugin = plugin
        self._project = project
        self._same_file_only = same_file_only
        self._timers: dict[Path, Timer] = {}
        self._lock: Lock = Lock()

    def _on_changed(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        assert isinstance(event.src_path, str), "Expected src_path to be a string"
        event_path = Path(event.src_path)
        with self._lock:
            if event_path in self._timers:
                self._timers[event_path].cancel()
            timer = Timer(0.1, self._fire, args=(event_path,))
            self._timers[event_path] = timer
            timer.start()

    def _is_path_relevant(self, event_path: Path) -> bool:
        same_file_only = self._same_file_only
        if same_file_only is not None and event_path.expanduser().resolve() != same_file_only.expanduser().resolve():
            return False
        return self._configuration_rule.filter_function(event_path)  # pyre-ignore[19]

    def _fire(self, event_path: Path) -> None:
        with self._lock:
            if event_path in self._timers:
                self._timers.pop(event_path)
        if self._is_path_relevant(event_path):
            self._config_service.on_disk_update(self._configuration_rule, self._plugin, self._project, event_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._on_changed(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._on_changed(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._on_changed(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._on_changed(event)


class _ProjectSynchronization(BaseModel):
    """
    A collection of project's active environments we're currently synchronizing.

    Even after all environments have been removed, we keep the synchronization around.
    That way we know the watchers have already been set up in case a new environment is added later.

    (We don't bother removing watchers when there are no environments left.
     Watchdog's interface makes it somewhat complicated and it's not worth the effort at the moment.)

    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    project: Project
    environments: dict[TaskID, Environment]
    # Paths we were unable to watch when setting up the synchronization.
    # (We remember that to try again later.)
    skipped_paths: list[Path] = Field(default_factory=list)


class LocalConfigService(ConfigService):
    secret_file_path: Path = Field(default_factory=lambda: get_sculptor_folder() / SHARED_DOTENV_FILENAME)
    credentials_file_path: Path = Field(default_factory=lambda: get_sculptor_folder() / CREDENTIALS_FILENAME)
    # Home directory from the config perspective. (E.g. where do we expect .claude and .claude.json to be found on the user's local machine?)
    # This is useful for tests to not pollute the real user's home directory.
    config_home_local: Path
    # FIXME: these should all be private vars?
    credentials: Credentials = Field(default_factory=Credentials)
    token_refresh_stop_event: Event = Field(default_factory=Event)
    token_refresh_thread: ObservableThread | None = None

    _lock: Lock = PrivateAttr(default_factory=Lock)
    _anthropic_global_configuration_watchers: list[Callable[[GlobalConfiguration], None]] = PrivateAttr(
        default_factory=list
    )

    # The rest of the fields are related to the plugin system.
    _observer: Observer = PrivateAttr(default_factory=Observer)  # pyre-fixme[11]
    _on_configuration_updated_callbacks: list[Callable[[str, ProjectID], None]] = PrivateAttr(default_factory=list)
    _project_synchronizations: list[_ProjectSynchronization] = PrivateAttr(default_factory=list)

    # Available plugins.
    _claude_code_plugin_full: ConfigServicePlugin = PrivateAttr()
    _claude_code_plugin_minimal: ConfigServicePlugin = PrivateAttr()
    _project_service_plugin: ConfigServicePlugin = PrivateAttr()

    # Actually activated plugins (this dynamically changes at runtime based on user config).
    _active_plugins: tuple[ConfigServicePlugin, ...] = PrivateAttr(default_factory=tuple)

    def model_post_init(self, context: Any) -> None:
        self._claude_code_plugin_full = get_claude_code_plugin(
            self.concurrency_group,
            lambda: self.get_credentials().anthropic,
            is_claude_configuration_synchronized=True,
            home_local=self.config_home_local,
        )
        self._claude_code_plugin_minimal = get_claude_code_plugin(
            self.concurrency_group,
            lambda: self.get_credentials().anthropic,
            is_claude_configuration_synchronized=False,
            home_local=self.config_home_local,
        )
        self._project_service_plugin = get_project_service_plugin()

    def _activate_plugin(self, plugin: ConfigServicePlugin) -> None:
        self._active_plugins += (plugin,)
        plugin.start()

    def _deactivate_plugin(self, plugin: ConfigServicePlugin) -> None:
        self._active_plugins = tuple(p for p in self._active_plugins if p != plugin)
        plugin.stop()

    def start(self) -> None:
        self.secret_file_path.parent.mkdir(parents=True, exist_ok=True)
        user_config = get_user_config_instance()
        try:
            credentials = Credentials.model_validate_json(self.credentials_file_path.read_text())
            if credentials.anthropic:
                self.set_anthropic_credentials(anthropic_credentials=credentials.anthropic)
            if credentials.openai:
                self.set_openai_credentials(openai_credentials=credentials.openai)
        except (FileNotFoundError, ValidationError):
            if user_config:
                if user_config.anthropic_api_key:
                    self.set_anthropic_credentials(
                        AnthropicApiKey(
                            anthropic_api_key=Secret(user_config.anthropic_api_key), generated_from_oauth=False
                        )
                    )
                # TODO(Andy): Investigate if this is a no-op
                if user_config.openai_api_key:
                    self.set_openai_credentials(
                        OpenAIApiKey(openai_api_key=Secret(user_config.openai_api_key), generated_from_oauth=False)
                    )
        self._observer.start()
        self._activate_plugin(self._project_service_plugin)
        if user_config.is_claude_configuration_synchronized:
            self._activate_plugin(self._claude_code_plugin_full)
        else:
            self._activate_plugin(self._claude_code_plugin_minimal)

    def stop(self) -> None:
        if self.token_refresh_thread:
            self._stop_token_refresh_thread()
        self._observer.stop()
        self._observer.join()
        self.concurrency_group.shutdown()
        for plugin in self._active_plugins:
            plugin.stop()

    def on_disk_update(
        self, configuration_rule: ConfigurationRule, plugin: ConfigServicePlugin, project: Project, modified_path: Path
    ) -> None:
        self._refresh_configuration_across_environments(project, plugin, configuration_rule, modified_path)
        if not configuration_rule.is_notifying_on_updates:
            return
        update_description = f"Updated: {configuration_rule.name}"
        for callback in self._on_configuration_updated_callbacks:
            try:
                callback(update_description, project.object_id)
            except Exception as e:
                logger.error(f"Error in on configuration updated callback: {e}")

    def _set_up_project_watchers(self, synchronization: _ProjectSynchronization, skipped_only: bool = False) -> None:
        for plugin in self._active_plugins:
            for configuration_rule in plugin.configuration_rules:
                synchronize_from = configuration_rule.synchronize_from
                assert synchronize_from is not None, "Expected synchronize_from to be not None"
                synchronize_from = resolve_placeholders(
                    synchronize_from, project=synchronization.project, home_local=self.config_home_local
                )
                if not synchronize_from.exists():
                    # Cannot easily set up a watch on a non-existing path. Skip it for now and mark to try again later.
                    with self._lock:
                        if synchronize_from not in synchronization.skipped_paths:
                            synchronization.skipped_paths.append(synchronize_from)
                        continue
                if skipped_only:
                    with self._lock:
                        if synchronize_from not in synchronization.skipped_paths:
                            continue
                        synchronization.skipped_paths.remove(synchronize_from)
                if synchronize_from.is_file():
                    to_watch = synchronize_from.parent
                    same_file_only = synchronize_from
                else:
                    to_watch = synchronize_from
                    same_file_only = None
                handler = _ConfigurationChangeHandler(
                    self, plugin, configuration_rule, synchronization.project, same_file_only
                )
                self._observer.schedule(handler, str(to_watch))

    def _apply_single_configuration_rule(
        self,
        plugin: ConfigServicePlugin,
        configuration_rule: ConfigurationRule,
        project: Project,
        environment: Environment,
        external_trigger: Path | None = None,
    ) -> None:
        try:
            apply_configuration_rule(
                plugin, configuration_rule, project, environment, self.config_home_local, external_trigger
            )
        except EnvironmentFailure as e:
            # It's fine to ignore failures if the environment is dead.
            # (Task service will soon notice and will call `stop_configuration_synchronization`.)
            if environment.is_alive():
                raise

    def _apply_configuration(self, project: Project, environment: Environment) -> None:
        for plugin in self._active_plugins:
            for configuration_rule in plugin.configuration_rules:
                self._apply_single_configuration_rule(plugin, configuration_rule, project, environment)

    def _refresh_configuration_across_environments(
        self,
        project: Project,
        plugin: ConfigServicePlugin,
        configuration_rule: ConfigurationRule,
        external_trigger: Path,
    ) -> None:
        synchronization = self._get_synchronization(project)
        if synchronization is not None:
            with self._lock:
                environments = list(synchronization.environments.values())
            for environment in environments:
                self._apply_single_configuration_rule(
                    plugin=plugin,
                    configuration_rule=configuration_rule,
                    project=project,
                    environment=environment,
                    external_trigger=external_trigger,
                )

    def _rebuild_watchers_and_refresh_all_configurations(self) -> None:
        with self._lock:
            synchronizations = self._project_synchronizations
            self._project_synchronizations = []
            self._observer.unschedule_all()
        for synchronization in synchronizations:
            with self._lock:
                environment_by_task_id = synchronization.environments.copy()
            for task_id, environment in environment_by_task_id.items():
                self.start_synchronizing_environment(synchronization.project, task_id, environment)

    def _get_synchronization(self, project: Project) -> _ProjectSynchronization | None:
        with self._lock:
            for synchronization in self._project_synchronizations:
                if synchronization.project.object_id == project.object_id:
                    return synchronization
        return None

    def start_synchronizing_environment(self, project: Project, task_id: TaskID, environment: Environment) -> None:
        logger.info("Starting configuration synchronization for task {}", task_id)
        synchronization = self._get_synchronization(project)
        with self._lock:
            if synchronization is not None:
                is_new_project = False
                synchronization.environments[task_id] = environment
            else:
                is_new_project = True
                synchronization = _ProjectSynchronization(project=project, environments={task_id: environment})
                self._project_synchronizations.append(synchronization)
        self._set_up_project_watchers(synchronization, skipped_only=not is_new_project)
        self._apply_configuration(project, environment)

    def stop_synchronizing_environment(self, project: Project, task_id: TaskID) -> None:
        logger.info("Stopping configuration synchronization for task {}", task_id)
        synchronization = self._get_synchronization(project)
        if synchronization is None:
            return
        with self._lock:
            synchronization.environments.pop(task_id, None)

    def register_on_configuration_updated(self, callback: Callable[[str, ProjectID], None]) -> None:
        self._on_configuration_updated_callbacks.append(callback)

    # The rest of the methods are mostly ad-hoc, unrelated to the plugin system.

    def get_credentials(self) -> Credentials:
        return self.credentials

    def set_anthropic_credentials(self, anthropic_credentials: AnthropicCredentials) -> None:
        old_credentials_is_claude_oauth = isinstance(self.credentials.anthropic, ClaudeOauthCredentials)
        new_credentials_is_claude_oauth = isinstance(anthropic_credentials, ClaudeOauthCredentials)
        if old_credentials_is_claude_oauth and not new_credentials_is_claude_oauth:
            self._stop_token_refresh_thread()
        self.credentials = Credentials(anthropic=anthropic_credentials, openai=self.credentials.openai)
        if isinstance(anthropic_credentials, ClaudeOauthCredentials):
            self._on_new_user_config(GlobalConfiguration(credentials=self.credentials))
        populate_credentials_file(path=self.credentials_file_path, credentials=self.credentials)
        if not old_credentials_is_claude_oauth and new_credentials_is_claude_oauth:
            self._start_token_refresh_thread()

    def set_openai_credentials(self, openai_credentials: OpenAIApiKey) -> None:
        self.credentials = Credentials(openai=openai_credentials, anthropic=self.credentials.anthropic)
        populate_credentials_file(path=self.credentials_file_path, credentials=self.credentials)

    def remove_anthropic_credentials(self) -> None:
        self.credentials = Credentials(anthropic=None, openai=self.credentials.openai)
        self._write_credentials()

    def remove_openai_credentials(self) -> None:
        self.credentials = Credentials(openai=None, anthropic=self.credentials.anthropic)
        self._write_credentials()

    def _write_credentials(self) -> None:
        if self.credentials.is_set:
            populate_credentials_file(path=self.credentials_file_path, credentials=self.credentials)
        else:
            try:
                self.credentials_file_path.unlink()
            except FileNotFoundError:
                pass

    def _start_token_refresh_thread(self) -> None:
        self.token_refresh_thread = self.concurrency_group.start_new_thread(target=self._token_refresh_thread_target)

    def _stop_token_refresh_thread(self) -> None:
        self.token_refresh_stop_event.set()
        self.token_refresh_thread.join()  # pyre-fixme[16]: token_refresh_thread can be None
        self.token_refresh_thread = None
        self.token_refresh_stop_event = Event()

    def _token_refresh_thread_target(self) -> None:
        first_iteration = True
        while not self.concurrency_group.is_shutting_down():
            if first_iteration:
                first_iteration = False
            else:
                # Wait for a short time between all iterations,
                # but not before the first iteration -
                # the OAuth token might already have expired when Sculptor starts.
                #
                # The timeout may seem unnecessarily short short,
                # as the token is usually valid for at least a couple of hours.
                # However, the user's computer could go to sleep and we can overshoot the expiry.
                # Minimize that possiblity by checking more frequently.
                should_stop = self.token_refresh_stop_event.wait(timeout=30)
                if should_stop:
                    break
            logger.debug("Claude OAuth token refresh thread has woken up")
            anthropic_credentials = self.credentials.anthropic

            # NOTE(bowei): very important not to throw exception here, cuz this flow can race with the re-login oauth modal flow.
            # Instead the token refresh thread should wait around until the relogin has finished. If we error here it will kill the api request too!
            if not isinstance(anthropic_credentials, ClaudeOauthCredentials):
                continue
            if time.time() < anthropic_credentials.expires_at_unix_ms / 1000 - REFRESH_TOKEN_EXPIRY_BUFFER_SECONDS:
                continue
            logger.info("Refreshing Claude OAuth tokens")
            refresh_token = anthropic_credentials.refresh_token.unwrap()
            with httpx.Client() as client:
                raw_response: httpx.Response | None = None
                try:
                    raw_response = client.post(
                        "https://console.anthropic.com/v1/oauth/token",
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token,
                            "client_id": CLAUDE_CODE_CLIENT_ID,
                        },
                        headers={"Accept": "application/json"},
                    )
                    token_response = TokenResponse.model_validate_json(raw_response.content)
                except Exception as e:
                    log_exception(e, "Error refreshing Claude OAuth credentials")
                    # If we have failed, the response wouldn't contain any secret credentials,
                    # so it's safe to log.
                    if raw_response is not None:
                        logger.info("Raw response: {}", raw_response.content)
                    logger.info("Ignoring the error; we'll try again later")
                    continue
            self.credentials = Credentials(
                anthropic=ClaudeOauthCredentials(
                    access_token=Secret(token_response.access_token),
                    refresh_token=Secret(token_response.refresh_token),
                    expires_at_unix_ms=int((time.time() + token_response.expires_in) * 1000),
                    scopes=token_response.scope.split(" "),
                    subscription_type=anthropic_credentials.subscription_type,
                ),
                openai=self.credentials.openai,
            )
            populate_credentials_file(path=self.credentials_file_path, credentials=self.credentials)
            self._on_new_user_config(GlobalConfiguration(credentials=self.credentials))

    def _on_new_user_config(self, user_config: GlobalConfiguration) -> None:
        self.notify_global_anthropic_configuration_watchers()

    def get_user_secrets(self, secret_names: Sequence[str] | None = None) -> dict[str, Secret]:
        file_secrets = {}
        if self.secret_file_path.exists():
            file_secrets = dotenv_values(self.secret_file_path)

        secrets = file_secrets
        if os.getenv(GITLAB_TOKEN_NAME) is not None:
            # NOTE(bowei): this is NOT supposed to be a user-owned key. TODO(PROD-3226): rename the name to be clearer
            # Sculptor mirrors user repos to GitLab; this token must be forwarded so the backend keeps push access.
            secrets[GITLAB_TOKEN_NAME] = os.environ[GITLAB_TOKEN_NAME]

        if secret_names is not None:
            secrets = {name: secrets[name] for name in secret_names if name in secrets}

        secrets = {key: Secret(value) for key, value in secrets.items()}

        return secrets

    def set_user_secrets(self, secrets: Mapping[str, str | Secret]) -> None:
        logger.debug("Saving {} secrets to {}", len(secrets), self.secret_file_path)

        self.secret_file_path.parent.mkdir(parents=True, exist_ok=True)

        for key, value in secrets.items():
            set_key(
                dotenv_path=str(self.secret_file_path),
                key_to_set=key,
                value_to_set=value.unwrap() if isinstance(value, Secret) else value,
                quote_mode="auto",
            )

    def get_user_config(self) -> UserConfig:
        return get_user_config_instance()

    def set_user_config(self, config: UserConfig) -> None:
        original_config = get_user_config_instance()
        config_path = get_config_path()
        save_config(config, config_path)
        set_user_config_instance(config)
        if config.is_claude_configuration_synchronized != original_config.is_claude_configuration_synchronized:
            if config.is_claude_configuration_synchronized:
                self._deactivate_plugin(self._claude_code_plugin_minimal)
                self._activate_plugin(self._claude_code_plugin_full)
            else:
                self._deactivate_plugin(self._claude_code_plugin_full)
                self._activate_plugin(self._claude_code_plugin_minimal)
            self._rebuild_watchers_and_refresh_all_configurations()

    def get_global_configuration(self) -> GlobalConfiguration:
        with self._lock:
            return GlobalConfiguration(
                credentials=self.credentials,
            )

    def register_global_configuration_watcher(self, callback: Callable[[GlobalConfiguration], None]) -> None:
        self._anthropic_global_configuration_watchers.append(callback)

    def notify_global_anthropic_configuration_watchers(self) -> None:
        anthropic_configuration = self.get_global_configuration()

        for callback in self._anthropic_global_configuration_watchers:
            try:
                callback(anthropic_configuration)
            except Exception as e:
                logger.error(f"Error in user config watcher callback: {e}")
