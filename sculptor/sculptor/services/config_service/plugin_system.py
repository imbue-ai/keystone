"""
This module defines a plugin system for managing configuration synchronization.

- A "configuration rule" specifies how to synchronize a local configuration item (a file or a directory) into a sandbox.
  (Alternatively, the configuration rule can also create a synthetic configuration item to be synchronized on the fly.)
- A "plugin" is a collection of configuration rules, possibly with a state.

The interfaces and declarative plugin / rule definitions should mostly be compatible with different architectures going forward.
(E.g. configuration synchronization done by daemons running both locally and remotely.)

At the same time, the current implementation of the application of configuration rules is tied to the existing abstractions like Project or Environment.
The plugin system is currently driven by the ConfigService which orchestrates the individual parts, manages state (including watchers), and applies
the configuration rules as needed.

"""

from pathlib import Path
from typing import Callable
from typing import Generator
from typing import Generic
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ConfigDict

from imbue_core.pydantic_serialization import FrozenModel
from sculptor.database.models import Project
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.errors import EnvironmentFailure

# Placeholders that can be used in ConfigurationRule paths.
SANDBOX_HOME_PLACEHOLDER = Path("__SANDBOX_HOME__")
SANDBOX_PROJECT_ROOT_PLACEHOLDER = Path("__SANDBOX_PROJECT_ROOT__")
LOCAL_HOME_PLACEHOLDER = Path("__LOCAL_HOME__")
LOCAL_PROJECT_ROOT_PLACEHOLDER = Path("__LOCAL_PROJECT_ROOT__")
PROJECT_ID_PLACEHOLDER = Path("__PROJECT_ID__")


T = TypeVar("T", bound="ConfigServicePlugin")


class ConfigurationContext(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    plugin: T
    synchronized_from_file: Path | None
    synchronized_to_file: Path
    configuration_contents: str | None
    workspace_path_local: Path
    workspace_path_sandbox: Path


class ConfigurationRule(BaseModel, Generic[T]):
    # A human-readable name for the configuration rule - will be displayed in logs and UIs.
    name: str
    # The path on the user's computer that is the source of the configuration.
    # Can be a file or a directory. If None, the configuration item is synthesized on the fly.
    # Can contain placeholders defined above.
    synchronize_from: Path | None
    # The path inside a Sculptor sandbox / environment where the configuration should be synchronized to.
    synchronize_to: Path
    # An optional filter function to apply when synchronizing a directory.
    filter_function: Callable[[Path], bool] = lambda path: True
    # A generic function allowing the rule to augment the synchronized configuration item.
    augment_function: Callable[[ConfigurationContext[T]], str | None] | None = None
    is_notifying_on_updates: bool = True


class ConfigServicePlugin(FrozenModel):
    configuration_rules: tuple[ConfigurationRule, ...] = ()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


## A set of helper functions specific to our current architecture (with Environments, Projects, etc.).


def apply_configuration_rule(
    plugin: ConfigServicePlugin,
    configuration_rule: ConfigurationRule,
    project: Project,
    environment: Environment,
    home_local: Path,
    external_trigger: Path | None = None,
) -> None:
    """Apply a configuration rule by synchronizing the relevant configuration items into the given environment."""
    for local_path, sandbox_path, contents in _generate_configuration_items(
        configuration_rule,
        project,
        environment,
        home_local,
        external_trigger=external_trigger,
    ):
        augment = configuration_rule.augment_function
        if augment is not None:
            context = ConfigurationContext(
                plugin=plugin,
                synchronized_from_file=local_path,
                synchronized_to_file=sandbox_path,
                configuration_contents=contents,
                workspace_path_local=project.get_local_user_path(),
                workspace_path_sandbox=environment.to_host_path(environment.get_workspace_path()),
            )
            contents = augment(context)
        if contents is None:
            try:
                environment.delete_file_or_directory(str(sandbox_path))
            except EnvironmentFailure as e:
                if environment.exists(str(sandbox_path)):
                    raise e
        else:
            environment.write_atomically(str(sandbox_path), contents)


def resolve_placeholders(
    path: Path, project: Project | None = None, environment: Environment | None = None, home_local: Path = Path.home()
) -> Path:
    """
    Replace placeholders in the given path with actual values from the project and environment.

    If you're sure that some placeholders are not present, you can omit the corresponding arguments.
    The function will verify that the placeholders are indeed not present in that case.

    """
    path_str = str(path)
    path_str = path_str.replace(str(LOCAL_HOME_PLACEHOLDER), str(home_local))
    if project is not None:
        path_str = path_str.replace(str(LOCAL_PROJECT_ROOT_PLACEHOLDER), str(project.get_local_user_path()))
        path_str = path_str.replace(str(PROJECT_ID_PLACEHOLDER), str(project.object_id))
    else:
        assert PROJECT_ID_PLACEHOLDER.name not in path.parts, "PROJECT_ID_PLACEHOLDER found but project is None"
        assert LOCAL_PROJECT_ROOT_PLACEHOLDER.name not in path.parts, (
            "LOCAL_PROJECT_ROOT_PLACEHOLDER found but project is None"
        )
    if environment is not None:
        path_str = path_str.replace(
            str(SANDBOX_HOME_PLACEHOLDER),
            str(environment.get_container_user_home_directory()),
        )
        path_str = path_str.replace(str(SANDBOX_PROJECT_ROOT_PLACEHOLDER), str(environment.get_workspace_path()))
    else:
        assert SANDBOX_HOME_PLACEHOLDER.name not in path.parts, (
            "SANDBOX_HOME_PLACEHOLDER found but environment is None"
        )
        assert SANDBOX_PROJECT_ROOT_PLACEHOLDER.name not in path.parts, (
            "SANDBOX_PROJECT_ROOT_PLACEHOLDER found but environment is None"
        )
    return Path(path_str)


def _generate_configuration_items(
    configuration_rule: ConfigurationRule,
    project: Project,
    environment: Environment,
    home_local: Path,
    external_trigger: Path | None = None,
) -> Generator[tuple[Path | None, Path, str | None], None, None]:
    """
    Generate configuration items to be synchronized based on the given configuration rule.

    Each element is a tuple of (original file path, target file path, original contents).

    """
    synchronize_to = resolve_placeholders(
        configuration_rule.synchronize_to, project, environment, home_local=home_local
    )
    if configuration_rule.synchronize_from is None:
        yield (None, synchronize_to, None)
        return
    synchronize_from = resolve_placeholders(
        configuration_rule.synchronize_from, project, environment, home_local=home_local
    )
    if not synchronize_from.exists():
        yield (synchronize_from, synchronize_to, None)
        return
    if synchronize_from.is_dir():
        for file_path in synchronize_from.rglob("*"):
            if file_path.is_file() and configuration_rule.filter_function(file_path):  # pyre-ignore[19]
                relative_path = file_path.relative_to(synchronize_from)
                yield file_path, synchronize_to / relative_path, file_path.read_text()
        # Likely deletion.
        if external_trigger is not None and not external_trigger.exists():
            assert external_trigger.is_relative_to(synchronize_from)
            relative_path = external_trigger.relative_to(synchronize_from)
            yield external_trigger, synchronize_to / relative_path, None
    else:
        yield synchronize_from, synchronize_to, synchronize_from.read_text()
