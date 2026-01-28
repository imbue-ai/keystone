from abc import ABC
from abc import abstractmethod
from typing import Callable
from typing import Mapping
from typing import Sequence

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.errors import ExpectedError
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.secrets_utils import Secret
from sculptor.database.models import Project
from sculptor.database.models import TaskID
from sculptor.interfaces.environments.base import Environment
from sculptor.primitives.service import Service
from sculptor.services.config_service.data_types import AnthropicCredentials
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.data_types import GlobalConfiguration
from sculptor.services.config_service.data_types import OpenAIApiKey


class MissingCredentialsError(ExpectedError):
    pass


class ConfigService(Service, ABC):
    @abstractmethod
    def start_synchronizing_environment(self, project: Project, task_id: TaskID, environment: Environment) -> None:
        """Start synchronizing user's configuration and secrets to the given environment."""

    @abstractmethod
    def stop_synchronizing_environment(self, project: Project, task_id: TaskID) -> None:
        """Stop synchronizing user's configuration and secrets to the given environment."""

    @abstractmethod
    def get_credentials(self) -> Credentials: ...

    @abstractmethod
    def set_anthropic_credentials(self, anthropic_credentials: AnthropicCredentials) -> None:
        """
        Set Anthropic credentials.

        If the credentials are ClaudeOauthCredentials,
        the service is also responsible for refreshing them.
        """

    @abstractmethod
    def remove_anthropic_credentials(self) -> None:
        """Remove the stored Anthropic credentials."""

    @abstractmethod
    def set_openai_credentials(self, openai_credentials: OpenAIApiKey) -> None:
        """
        Set OpenAI credentials.
        """

    @abstractmethod
    def remove_openai_credentials(self) -> None:
        """Remove the stored OpenAI credentials."""

    @abstractmethod
    def get_user_secrets(self, secret_names: Sequence[str] | None) -> dict[str, Secret]:
        """
        Retrieve secrets by their names.

        :param secret_names: List of secret names to retrieve.  If None, all secrets should be returned.
        :return: Dictionary mapping secret names to their values.
        """

    @abstractmethod
    def set_user_secrets(self, secrets: Mapping[str, str | Secret]) -> None:
        """
        Saves all secrets.
        """

    @abstractmethod
    def get_user_config(self) -> UserConfig:
        """
        Retrieve the current user configuration.
        """

    @abstractmethod
    def set_user_config(self, config: UserConfig) -> None:
        """
        Set the current user configuration.
        """

    @abstractmethod
    def get_global_configuration(self) -> GlobalConfiguration:
        """
        Retrieve the current global (user-level) configuration.

        """

    @abstractmethod
    def register_on_configuration_updated(self, callback: Callable[[str, ProjectID], None]) -> None: ...

    @abstractmethod
    def register_global_configuration_watcher(self, callback: Callable[[GlobalConfiguration], None]) -> None:
        """
        NOTE: Currently only watches changes to GlobalConfiguration.claude_config

        """
