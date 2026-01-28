import json
import pathlib
import shlex
from datetime import datetime
from datetime import timedelta
from functools import partial
from pathlib import Path
from threading import Event
from threading import Lock
from threading import Thread
from typing import Any
from typing import Callable
from typing import Final
from typing import Mapping
from typing import Sequence
from typing import assert_never

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.common import is_on_osx
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.sculptor.state.mcp_constants import IMBUE_CLI_INTERNAL_MCP_SERVER_NAME
from imbue_core.sculptor.state.mcp_constants import IMBUE_CLI_USER_MCP_SERVER_NAME
from imbue_core.subprocess_utils import ProcessError
from imbue_core.subprocess_utils import ProcessSetupError
from sculptor.agents.data_types import SlashCommand
from sculptor.agents.default.claude_code_sdk.constants import CLAUDE_DIRECTORY
from sculptor.agents.default.claude_code_sdk.constants import CLAUDE_GLOBAL_SETTINGS_FILENAME
from sculptor.agents.default.claude_code_sdk.constants import CLAUDE_JSON_FILENAME
from sculptor.agents.default.claude_code_sdk.constants import CLAUDE_LOCAL_SETTINGS_FILENAME
from sculptor.agents.default.claude_code_sdk.constants import COMMANDS_DIRECTORY
from sculptor.agents.default.claude_code_sdk.constants import CREDENTIALS_JSON_FILENAME
from sculptor.agents.default.claude_code_sdk.constants import SUBAGENTS_DIRECTORY
from sculptor.interfaces.environments.base import Environment
from sculptor.services.config_service.data_types import AWSBedrockApiKey
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import AnthropicCredentials
from sculptor.services.config_service.data_types import ClaudeOauthCredentials
from sculptor.services.config_service.plugin_system import ConfigServicePlugin
from sculptor.services.config_service.plugin_system import ConfigurationContext
from sculptor.services.config_service.plugin_system import ConfigurationRule
from sculptor.services.config_service.plugin_system import LOCAL_HOME_PLACEHOLDER
from sculptor.services.config_service.plugin_system import LOCAL_PROJECT_ROOT_PLACEHOLDER
from sculptor.services.config_service.plugin_system import SANDBOX_HOME_PLACEHOLDER
from sculptor.services.config_service.plugin_system import SANDBOX_PROJECT_ROOT_PLACEHOLDER
from sculptor.services.config_service.plugin_system import resolve_placeholders
from sculptor.services.environment_service.tool_readiness import READY_FILE

# Number of characters from the end of API key to store for approval tracking
API_KEY_SUFFIX_LENGTH = 20

PROJECT_SCOPE_COMMAND_UI_MARKER = "(project)"
USER_SCOPE_COMMAND_UI_MARKER = "(user)"

SUPPORTED_BUILTIN_SLASH_COMMANDS = ("compact",)

MCP_OAUTH_REFRESH_FREQUENCY_SECONDS = 15 * 60
MCP_OAUTH_REFRESH_BUFFER = 5 * 60

CREDENTIALS_LOCAL_PATH_UNRESOLVED = LOCAL_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / CREDENTIALS_JSON_FILENAME
# A special trigger file - when updated, we re-check the credentials and potentially refresh tokens.
# (We do not directly synchronize claude's credentials.json file because:
#   - It may not exist on os x where credentials are often stored in the keychain.
#   - Making changes to credentials.json while being triggered by changes to credentials.json is a recipe for infinite loops.)
# TODO: once sculptor credentials handling lives in the plugin system, stop writing to ~/.claude and write to our own location.
CREDENTIALS_TRIGGER_LOCAL_PATH_UNRESOLVED = CREDENTIALS_LOCAL_PATH_UNRESOLVED.with_suffix(".trigger")


def _augment_claude_local_settings_json(context: ConfigurationContext) -> str | None:
    contents = context.configuration_contents
    if contents is None:
        return None
    try:
        existing_settings = json.loads(contents)
    except json.JSONDecodeError:
        logger.info("Failed to parse existing Claude local settings JSON.")
        return None
    return json.dumps(_remove_unsupported_settings_fields(existing_settings))


def _augment_claude_global_settings_json(context: ConfigurationContext) -> str | None:
    contents = context.configuration_contents
    if contents is None:
        existing_settings = {}
    else:
        try:
            existing_settings = json.loads(contents)
        except json.JSONDecodeError:
            logger.info("Failed to parse existing Claude global settings JSON.")
            existing_settings = {}
    transformed_settings = _merge_tool_readiness_hook(
        _remove_unsupported_settings_fields(existing_settings), timeout_seconds=120
    )
    return json.dumps(transformed_settings)


def _populate_claude_json(
    context: ConfigurationContext["ClaudeCodeConfigurationPlugin"], is_user_settings_synchronized: bool
) -> str:
    """
    Populate .claude.json.

    Claude Code requires certain settings to run correctly.

    We default to using the user's settings (with some specific changes).
    However, if the user does NOT have claude code installed, we can provide
    them with our own settings.

    """
    anthropic_credentials = context.plugin.credentials_getter()
    assert anthropic_credentials is not None, "Anthropic credentials are required to populate .claude.json"
    workspace_path_local = context.workspace_path_local.expanduser().resolve()
    workspace_path_sandbox = context.workspace_path_sandbox.expanduser().resolve()
    workspace_key = str(workspace_path_sandbox)
    contents = context.configuration_contents
    if contents is not None and is_user_settings_synchronized:
        claude_config = json.loads(contents)
        local_project_configuration = claude_config.get("projects", {}).get(str(workspace_path_local))
        claude_config["projects"] = {}  # Clear out projects that only exist on the user's machine.
        claude_config["projects"][workspace_key] = _claude_project_config(
            workspace_path_sandbox, local_project_configuration
        )
    else:
        claude_config = _claude_config_template(workspace_path_sandbox)
    custom_api_key_responses, _ = _custom_api_key_responses_and_claude_ai_credentials(anthropic_credentials)
    claude_config["customApiKeyResponses"] = custom_api_key_responses
    claude_config["hasCompletedOnboarding"] = True
    return json.dumps(claude_config)


def _populate_credentials_json(
    context: ConfigurationContext["ClaudeCodeConfigurationPlugin"], is_user_settings_synchronized: bool
) -> str | None:
    anthropic_credentials = context.plugin.credentials_getter()
    assert anthropic_credentials is not None, "Anthropic credentials are required to populate .claude.json"
    credentials_data = {}
    _, claude_ai_credentials = _custom_api_key_responses_and_claude_ai_credentials(anthropic_credentials)
    if claude_ai_credentials is not None:
        credentials_data.update(claude_ai_credentials)
    if is_user_settings_synchronized:
        synchronized_from_file = context.synchronized_from_file
        assert synchronized_from_file is not None
        plugin = context.plugin
        assert isinstance(plugin, ClaudeCodeConfigurationPluginFull), (
            "Expected full Claude Code configuration plugin when synchronization is on."
        )
        plugin.maybe_refresh_claude_mcp_oauth_tokens(context.workspace_path_local)
        mcp_servers_oauth = _claude_mcp_servers_oauth(plugin.credentials_json_local, plugin.concurrency_group)
        if mcp_servers_oauth is not None:
            credentials_data.update(mcp_servers_oauth)

    if len(credentials_data) == 0:
        return None
    return json.dumps(credentials_data)


def _custom_api_key_responses_and_claude_ai_credentials(
    anthropic_credentials: AnthropicCredentials,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    match anthropic_credentials:
        case AnthropicApiKey(anthropic_api_key=anthropic_api_key):
            # this is required for claude to work with the anthropic api key without prompting the user (primarily required for compaction and terminal)
            return {
                "approved": [anthropic_api_key.unwrap()[-API_KEY_SUFFIX_LENGTH:]],
                "rejected": [],
            }, None
        case ClaudeOauthCredentials():
            return {
                "approved": [],
                "rejected": [],
            }, anthropic_credentials.convert_to_claude_code_credentials_json_section()
        case AWSBedrockApiKey(bedrock_api_key=bedrock_api_key):
            return {
                "approved": [bedrock_api_key.unwrap()[-API_KEY_SUFFIX_LENGTH:]],
                "rejected": [],
            }, None
        case _ as unreachable:
            # TODO: pyre doesn't understand the matching here
            assert_never(unreachable)  # pyre-fixme[6]


def _claude_mcp_servers_config(
    workspace_path: pathlib.Path, user_local_config: dict[str, Any] | None
) -> dict[str, Any]:
    mcp_servers = {
        name: {
            "command": "imbue-cli.sh",
            "args": [
                "--log-to-file=/tmp/imbue-cli.log",
                "mcp",
                *("--project-path", str(workspace_path)),
                *config_args,
                *("--transport", "stdio"),
            ],
            "env": {},
        }
        for name, config_args in (
            (IMBUE_CLI_INTERNAL_MCP_SERVER_NAME, ["--use-internal-config"]),
            (IMBUE_CLI_USER_MCP_SERVER_NAME, ["--config", str(workspace_path / "tools.toml")]),
        )
    }
    if user_local_config is not None:
        for server_name, server_config in user_local_config.get("mcpServers", {}).items():
            if server_name not in mcp_servers:
                mcp_servers[server_name] = server_config
    return mcp_servers


def _get_local_credentials_data(
    claude_credentials_path_local: Path, concurrency_group: ConcurrencyGroup
) -> dict[str, Any] | None:
    if is_on_osx():
        result = concurrency_group.run_process_to_completion(
            ("security", "find-generic-password", "-s", "Claude Code-credentials", "-w"),
            timeout=5.0,
            is_checked_after=False,
        )
        if result.returncode != 0:
            logger.info("Failed to get claude credentials data from OSX keychain: {}", result.stderr)
            return None
        return json.loads(result.stdout.strip())
    if not claude_credentials_path_local.exists():
        return None
    with open(claude_credentials_path_local, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            logger.info("Failed to get claude credentials data from {}", claude_credentials_path_local)
            return None


def _claude_mcp_servers_oauth(
    claude_credentials_path_local: Path, concurrency_group: ConcurrencyGroup
) -> dict[str, Any] | None:
    credentials_data: dict[str, Any] | None = _get_local_credentials_data(
        claude_credentials_path_local, concurrency_group
    )
    if credentials_data is None or "mcpOAuth" not in credentials_data:
        return None
    mcp_oauth_data = credentials_data["mcpOAuth"]
    for server_name, server_details in mcp_oauth_data.items():
        # Remove refresh tokens.
        # (We don't want them to be propagated to the sandboxes, otherwise claude instances in the sandboxes would invalidate them by using them.)
        server_details.pop("refreshToken", None)
    return {
        "mcpOAuth": mcp_oauth_data,
    }


def _claude_project_config(workspace_path: pathlib.Path, user_local_config: dict[str, Any] | None) -> dict[str, Any]:
    # TODO: do we need all of these settings? last session id seems to be randomly copy pasted from someone's .claude.json
    return {
        "allowedTools": [],
        "history": [],
        "dontCrawlDirectory": False,
        "mcpContextUris": user_local_config.get("mcpContextUris", []) if user_local_config is not None else [],
        "mcpServers": _claude_mcp_servers_config(workspace_path, user_local_config),
        "enabledMcpjsonServers": [],
        "disabledMcpjsonServers": [],
        "hasTrustDialogAccepted": True,
        "ignorePatterns": [],
        "projectOnboardingSeenCount": 1,
        "hasClaudeMdExternalIncludesApproved": False,
        "hasClaudeMdExternalIncludesWarningShown": False,
        "lastCost": 0,
        "lastAPIDuration": 0,
        "lastDuration": 3172,
        "lastLinesAdded": 0,
        "lastLinesRemoved": 0,
        "lastTotalInputTokens": 0,
        "lastTotalOutputTokens": 0,
        "lastTotalCacheCreationInputTokens": 0,
        "lastTotalCacheReadInputTokens": 0,
        "lastSessionId": "ef949ec0-4a45-4665-81a7-f9e1ec21a41c",
        "bypassPermissionsModeAccepted": True,
    }


def _claude_config_template(workspace_path_sandbox: Path) -> dict[str, Any]:
    return {
        "numStartups": 3,
        "theme": "light",
        "customApiKeyResponses": {
            "approved": [],
            "rejected": [],
        },
        "firstStartTime": "2025-06-10T21:50:05.520Z",
        "projects": {str(workspace_path_sandbox): _claude_project_config(workspace_path_sandbox, None)},
        "isQualifiedForDataSharing": False,
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "1.0.17",
        "recommendedSubscription": "",
        "subscriptionNoticeCount": 0,
        "hasAvailableSubscription": False,
    }


_HOOK_SCRIPT_PATH: Final[str] = "/imbue/bin/check_tool_readiness.sh"


def _matches_tool_readiness_hook(hook_config: object) -> bool:
    """Check if a given hook configuration matches our tool readiness hook."""

    # Imperative logic to check if the structure looks something like this:
    # {
    #   "matcher": "*",
    #   "hooks": [
    #     {
    #       "type": "command",
    #       "command": "/imbue/bin/check_tool_readiness.sh <ready_file_path>",
    #       ...,
    #     },
    #   ],
    # }
    #
    # Since this can contain arbitrary user-defined data, we have to be defensive.

    if not isinstance(hook_config, Mapping):
        return False
    matcher = hook_config.get("matcher")
    hooks = hook_config.get("hooks")
    if matcher != "*":
        return False
    if not isinstance(hooks, Sequence) or len(hooks) == 0:
        return False
    first_hook = hooks[0]
    if not isinstance(first_hook, Mapping):
        return False
    command = first_hook.get("command")
    if not isinstance(command, str):
        return False
    return _HOOK_SCRIPT_PATH in command


def _merge_tool_readiness_hook(existing_settings: Mapping[str, Any], timeout_seconds: int) -> dict[str, Any]:
    """Merge tool readiness hook configuration with existing settings.

    Preserves all existing settings and hooks, appending the tool readiness PreToolUse hook.
    Multiple hooks with the same matcher can coexist and will all run in parallel.

    Args:
        existing_settings: Existing settings
        timeout_seconds: Timeout for the tool readiness hook

    Returns:
        New settings dictionary with hook appended

    Raises:
        TypeError: If existing settings structure has unexpected types
    """
    our_hook_config = {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": f"{_HOOK_SCRIPT_PATH} {shlex.quote(READY_FILE.as_posix())}",
                "env": {
                    "SCULPTOR_TOOL_READINESS_TIMEOUT": str(timeout_seconds),
                },
            }
        ],
    }

    existing_hooks = existing_settings.get("hooks", {})
    if not isinstance(existing_hooks, dict):
        raise TypeError(f"Expected hooks to be a dict, got {type(existing_hooks).__name__}")

    existing_pre_tool_use = existing_hooks.get("PreToolUse", [])
    if not isinstance(existing_pre_tool_use, (list, tuple)):
        raise TypeError(f"Expected PreToolUse to be a list, got {type(existing_pre_tool_use).__name__}")

    logger.debug("Appending tool readiness hook to PreToolUse hooks")

    return {
        **existing_settings,
        "hooks": {
            **existing_hooks,
            "PreToolUse": [
                *(
                    hook_config
                    for hook_config in existing_pre_tool_use
                    if not _matches_tool_readiness_hook(hook_config)
                ),
                our_hook_config,
            ],
        },
    }


def _remove_unsupported_settings_fields(settings_json: dict[str, Any]) -> dict[str, Any]:
    """
    Preprocess the host Claude settings.json.

    Removes fields that should not be transferred to Sculptor sandboxes.

    """
    preprocessed = {**settings_json}
    # apiKeyHelper points to a script that typically won't exist in a container and thus can break oauth.
    preprocessed.pop("apiKeyHelper", None)
    return preprocessed


def get_all_supported_slash_commands(environment: Environment) -> tuple[SlashCommand, ...]:
    """
    Return all custom commands found in the project and user home directories as well as the supported built-in commands.

    The ordering, value as well as display name are all consistent with Claude Code's behavior.

    """
    command_groups: list[list[SlashCommand]] = []
    for commands_directory_path, suffix in (
        (environment.get_workspace_path() / CLAUDE_DIRECTORY / COMMANDS_DIRECTORY, PROJECT_SCOPE_COMMAND_UI_MARKER),
        (
            environment.get_container_user_home_directory() / CLAUDE_DIRECTORY / COMMANDS_DIRECTORY,
            USER_SCOPE_COMMAND_UI_MARKER,
        ),
    ):
        command_group: list[SlashCommand] = []
        find_command = [
            "find",
            str(commands_directory_path),
            "-name",
            "*.md",
            "-type",
            "f",
        ]
        process = environment.run_process_to_completion(find_command, secrets={}, is_checked_after=False)
        assert process.returncode in (0, 1)
        lines = process.stdout
        run_paths = []
        for line in lines.splitlines():
            line = line.strip()
            if line:
                command_path = Path(line).relative_to(commands_directory_path)
                command_string = command_path.stem
                for parent in command_path.parents:
                    if parent.stem:
                        command_string = f"{parent.stem}:{command_string}"

                command_group.append(
                    SlashCommand(
                        value=f"/{command_string}",
                        display_name=f"{command_string} {suffix}",
                    )
                )
        command_groups.append(command_group)

    # Claude Code makes the built-in commands part of the last group.
    command_groups[-1].extend(
        SlashCommand(value=f"/{command_string}", display_name=command_string)
        for command_string in SUPPORTED_BUILTIN_SLASH_COMMANDS
    )
    for command_group in command_groups:
        command_group.sort(key=lambda command: command.value)
    return tuple(command for group in command_groups for command in group)


class ClaudeCodeConfigurationPlugin(ConfigServicePlugin):
    credentials_getter: Callable[[], AnthropicCredentials | None]
    concurrency_group: ConcurrencyGroup


class ClaudeCodeConfigurationPluginMinimal(ClaudeCodeConfigurationPlugin):
    configuration_rules: tuple[ConfigurationRule, ...] = (
        ConfigurationRule(
            name=".claude.json",
            synchronize_from=LOCAL_HOME_PLACEHOLDER / CLAUDE_JSON_FILENAME,
            synchronize_to=SANDBOX_HOME_PLACEHOLDER / CLAUDE_JSON_FILENAME,
            augment_function=partial(_populate_claude_json, is_user_settings_synchronized=False),
            is_notifying_on_updates=False,
        ),
        ConfigurationRule(
            name="Claude credentials",
            synchronize_from=CREDENTIALS_TRIGGER_LOCAL_PATH_UNRESOLVED,
            synchronize_to=SANDBOX_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / CREDENTIALS_JSON_FILENAME,
            augment_function=partial(_populate_credentials_json, is_user_settings_synchronized=False),
            is_notifying_on_updates=False,
        ),
    )


class ClaudeCodeConfigurationPluginFull(ClaudeCodeConfigurationPlugin):
    credentials_json_local: Path
    credentials_trigger: Path
    _lock: Lock = PrivateAttr(default_factory=Lock)
    _shutdown_event: Event = PrivateAttr(default_factory=Event)
    _mcp_oauth_refresh_thread: Thread | None = PrivateAttr(default=None)

    configuration_rules: tuple[ConfigurationRule, ...] = (
        ConfigurationRule(
            name="Claude settings.json",
            synchronize_from=LOCAL_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / CLAUDE_GLOBAL_SETTINGS_FILENAME,
            synchronize_to=SANDBOX_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / CLAUDE_GLOBAL_SETTINGS_FILENAME,
            augment_function=_augment_claude_global_settings_json,
        ),
        ConfigurationRule(
            name="Claude settings.local.json",
            synchronize_from=LOCAL_PROJECT_ROOT_PLACEHOLDER / CLAUDE_DIRECTORY / CLAUDE_LOCAL_SETTINGS_FILENAME,
            synchronize_to=SANDBOX_PROJECT_ROOT_PLACEHOLDER / CLAUDE_DIRECTORY / CLAUDE_LOCAL_SETTINGS_FILENAME,
            augment_function=_augment_claude_local_settings_json,
        ),
        ConfigurationRule(
            name="Claude subagents",
            synchronize_from=LOCAL_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / SUBAGENTS_DIRECTORY,
            synchronize_to=SANDBOX_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / SUBAGENTS_DIRECTORY,
            filter_function=lambda path: path.suffix == ".md",
        ),
        ConfigurationRule(
            name="Claude commands",
            synchronize_from=LOCAL_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / COMMANDS_DIRECTORY,
            synchronize_to=SANDBOX_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / COMMANDS_DIRECTORY,
            filter_function=lambda path: path.suffix == ".md",
        ),
        ConfigurationRule(
            name=".claude.json",
            synchronize_from=LOCAL_HOME_PLACEHOLDER / CLAUDE_JSON_FILENAME,
            synchronize_to=SANDBOX_HOME_PLACEHOLDER / CLAUDE_JSON_FILENAME,
            augment_function=partial(_populate_claude_json, is_user_settings_synchronized=True),
        ),
        ConfigurationRule(
            name="Claude credentials",
            synchronize_from=CREDENTIALS_TRIGGER_LOCAL_PATH_UNRESOLVED,
            synchronize_to=SANDBOX_HOME_PLACEHOLDER / CLAUDE_DIRECTORY / CREDENTIALS_JSON_FILENAME,
            augment_function=partial(_populate_credentials_json, is_user_settings_synchronized=True),
            is_notifying_on_updates=False,
        ),
    )

    def start(self) -> None:
        super().start()
        self._shutdown_event.clear()
        self._mcp_oauth_refresh_thread = self.concurrency_group.start_new_thread(
            target=self._periodically_touch_credentials_trigger,
            name="Claude MCP OAuth Token Refresher",
        )

    def stop(self) -> None:
        super().stop()
        self._shutdown_event.set()
        if self._mcp_oauth_refresh_thread is not None:
            self._mcp_oauth_refresh_thread.join(timeout=10)

    def _periodically_touch_credentials_trigger(self) -> None:
        while True:
            # By periodically touching the credentials trigger file, we trigger the _populate_credentials_json function
            # which can then refresh the MCP OAuth tokens as needed.
            # TODO: optimize this to only propagate credentials to environments when actually needed.
            if self._shutdown_event.wait(timeout=MCP_OAUTH_REFRESH_FREQUENCY_SECONDS):
                break
            if self.credentials_trigger.parent.exists():
                self.credentials_trigger.touch()

    def maybe_refresh_claude_mcp_oauth_tokens(self, workspace_path_local: Path) -> None:
        workspace_path = workspace_path_local.expanduser().resolve()
        # TODO: surface various failures to the user somehow.
        if self._shutdown_event.is_set():
            return
        with self._lock:
            credentials_data = _get_local_credentials_data(self.credentials_json_local, self.concurrency_group)
            if credentials_data is None:
                return
            now = datetime.now()
            # TODO: Ideally, we should only consider servers relevant to the given project / workspace.
            for server_name, server_details in credentials_data.get("mcpOAuth", {}).items():
                expires_at_miliseconds = server_details.get("expiresAt")
                if not expires_at_miliseconds:
                    continue
                expires_at = datetime.fromtimestamp(expires_at_miliseconds / 1000)
                if expires_at > now + timedelta(
                    seconds=MCP_OAUTH_REFRESH_FREQUENCY_SECONDS + MCP_OAUTH_REFRESH_BUFFER
                ):
                    continue
                break
            else:
                # No refresh needed.
                return
            try:
                # This call updates .credentials.json in place - the tokens are renewed if necessary.
                logger.info("Refreshing Claude MCP OAuth tokens.")
                process = self.concurrency_group.run_process_to_completion(
                    ["claude", "mcp", "list"],
                    shutdown_event=self._shutdown_event,
                    # Needs to run in the workspace so that claude can identify the right set of MCP servers.
                    cwd=workspace_path,
                )
                # TODO: check for the "Needs authentication" line in the output and tell the user.
            except ProcessSetupError:
                logger.info(
                    "Could not refresh Claude MCP OAuth tokens because claude code does not seem to be installed."
                )
                return
            except ProcessError:
                if self._shutdown_event.is_set():
                    return
                raise


def get_plugin(
    concurrency_group: ConcurrencyGroup,
    credentials_getter: Callable[[], AnthropicCredentials | None],
    is_claude_configuration_synchronized: bool,
    home_local: Path,
) -> ConfigServicePlugin:
    credentials_json_local = resolve_placeholders(CREDENTIALS_LOCAL_PATH_UNRESOLVED, home_local=home_local)
    credentials_trigger = resolve_placeholders(CREDENTIALS_TRIGGER_LOCAL_PATH_UNRESOLVED, home_local=home_local)
    if is_claude_configuration_synchronized:
        return ClaudeCodeConfigurationPluginFull(
            credentials_getter=credentials_getter,
            concurrency_group=concurrency_group,
            credentials_json_local=credentials_json_local,
            credentials_trigger=credentials_trigger,
        )
    return ClaudeCodeConfigurationPluginMinimal(
        credentials_getter=credentials_getter,
        concurrency_group=concurrency_group,
    )
