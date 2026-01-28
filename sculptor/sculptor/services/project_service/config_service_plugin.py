from sculptor.agents.default.constants import GITLAB_PROJECT_URL_STATE_FILE
from sculptor.agents.default.constants import GITLAB_TOKEN_STATE_FILE
from sculptor.constants import ROOT_PATH
from sculptor.interfaces.environments.base import STATE_DIRECTORY
from sculptor.services.config_service.plugin_system import ConfigServicePlugin
from sculptor.services.config_service.plugin_system import ConfigurationRule
from sculptor.services.config_service.plugin_system import PROJECT_ID_PLACEHOLDER
from sculptor.services.project_service.constants import PROJECT_CONFIGURATIONS_PATH


def get_plugin() -> ConfigServicePlugin:
    # Synchronize gitlab configuration files created by the Project service.
    return ConfigServicePlugin(
        configuration_rules=(
            ConfigurationRule(
                name="Gitlab token",
                synchronize_from=PROJECT_CONFIGURATIONS_PATH / PROJECT_ID_PLACEHOLDER / GITLAB_TOKEN_STATE_FILE,
                synchronize_to=ROOT_PATH / STATE_DIRECTORY / GITLAB_TOKEN_STATE_FILE,
                is_notifying_on_updates=False,
            ),
            ConfigurationRule(
                name="Gitlab project URL",
                synchronize_from=PROJECT_CONFIGURATIONS_PATH / PROJECT_ID_PLACEHOLDER / GITLAB_PROJECT_URL_STATE_FILE,
                synchronize_to=ROOT_PATH / STATE_DIRECTORY / GITLAB_PROJECT_URL_STATE_FILE,
                is_notifying_on_updates=False,
            ),
        )
    )
