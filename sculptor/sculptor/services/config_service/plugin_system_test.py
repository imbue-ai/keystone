from pathlib import Path
from typing import Generator
from unittest.mock import Mock

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.database.models import Project
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.interfaces.environments.base import LocalImage
from sculptor.primitives.ids import OrganizationReference
from sculptor.services.config_service.plugin_system import ConfigServicePlugin
from sculptor.services.config_service.plugin_system import ConfigurationContext
from sculptor.services.config_service.plugin_system import ConfigurationRule
from sculptor.services.config_service.plugin_system import LOCAL_HOME_PLACEHOLDER
from sculptor.services.config_service.plugin_system import LOCAL_PROJECT_ROOT_PLACEHOLDER
from sculptor.services.config_service.plugin_system import PROJECT_ID_PLACEHOLDER
from sculptor.services.config_service.plugin_system import SANDBOX_HOME_PLACEHOLDER
from sculptor.services.config_service.plugin_system import SANDBOX_PROJECT_ROOT_PLACEHOLDER
from sculptor.services.config_service.plugin_system import apply_configuration_rule
from sculptor.services.config_service.plugin_system import resolve_placeholders
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.providers.local.environment_utils import build_local_environment
from sculptor.services.environment_service.providers.local.image_utils import build_local_image


@pytest.fixture
def test_project(tmp_path: Path) -> Project:
    """Create a test project for testing."""
    project_path = tmp_path / "test_project"
    project_path.mkdir(parents=True, exist_ok=True)
    return Project(
        object_id=ProjectID(),
        name="Test Project",
        organization_reference=OrganizationReference("org_test_123"),
        user_git_repo_url=f"file://{project_path}",
    )


@pytest.fixture
def local_image(tmp_path: Path, test_project: Project) -> LocalImage:
    code_directory = tmp_path / "code"
    code_directory.mkdir(parents=True, exist_ok=True)
    return build_local_image(code_directory, test_project.object_id)


@pytest.fixture
def local_environment(
    test_root_concurrency_group: ConcurrencyGroup, local_image: LocalImage
) -> Generator[LocalEnvironment, None, None]:
    """Create a test LocalEnvironment for testing."""
    environment = None
    try:
        environment = build_local_environment(local_image, LocalEnvironmentConfig(), test_root_concurrency_group)
        yield environment
    finally:
        if environment is not None:
            environment.close()


def test_resolve_local_home_placeholder(tmp_path: Path) -> None:
    home_path = tmp_path / "home"
    home_path.mkdir(parents=True, exist_ok=True)
    path_with_placeholder = LOCAL_HOME_PLACEHOLDER / ".config" / "myapp"
    resolved = resolve_placeholders(path_with_placeholder, home_local=home_path)
    assert resolved == home_path / ".config" / "myapp"


def test_resolve_local_project_root_placeholder(test_project: Project) -> None:
    project_path = test_project.get_local_user_path()
    path_with_placeholder = LOCAL_PROJECT_ROOT_PLACEHOLDER / "src" / "main.py"
    resolved = resolve_placeholders(path_with_placeholder, project=test_project)
    assert resolved == project_path / "src" / "main.py"


def test_resolve_project_id_placeholder(test_project: Project) -> None:
    path_with_placeholder = Path("/tmp") / PROJECT_ID_PLACEHOLDER / "cache"
    resolved = resolve_placeholders(path_with_placeholder, project=test_project)
    assert resolved == Path("/tmp") / str(test_project.object_id) / "cache"


def test_resolve_sandbox_home_placeholder(local_environment: LocalEnvironment) -> None:
    path_with_placeholder = SANDBOX_HOME_PLACEHOLDER / ".bashrc"
    resolved = resolve_placeholders(path_with_placeholder, environment=local_environment)
    expected_home = local_environment.get_container_user_home_directory()
    assert resolved == expected_home / ".bashrc"


def test_resolve_sandbox_project_root_placeholder(local_environment: LocalEnvironment) -> None:
    path_with_placeholder = SANDBOX_PROJECT_ROOT_PLACEHOLDER / "code" / "test.py"
    resolved = resolve_placeholders(path_with_placeholder, environment=local_environment)
    expected_workspace = local_environment.get_workspace_path()
    assert resolved == expected_workspace / "code" / "test.py"


def test_resolve_multiple_placeholders(
    test_project: Project, local_environment: LocalEnvironment, tmp_path: Path
) -> None:
    home_path = tmp_path / "home"
    home_path.mkdir(parents=True, exist_ok=True)
    path_with_placeholder = LOCAL_HOME_PLACEHOLDER / ".cache" / "sculptor" / PROJECT_ID_PLACEHOLDER / "temp"
    resolved = resolve_placeholders(path_with_placeholder, project=test_project, home_local=home_path)
    expected = home_path / ".cache" / "sculptor" / str(test_project.object_id) / "temp"
    assert resolved == expected


def test_resolve_no_placeholders() -> None:
    regular_path = Path("/home/user/project/file.txt")
    resolved = resolve_placeholders(regular_path)
    assert resolved == regular_path


def test_placeholder_without_value_raises_assertion() -> None:
    path_with_placeholder = Path("/tmp") / PROJECT_ID_PLACEHOLDER / "cache"
    with pytest.raises(AssertionError, match="PROJECT_ID_PLACEHOLDER found but project is None"):
        resolve_placeholders(path_with_placeholder, project=None)


def test_resolve_placeholder_in_middle_of_path(test_project: Project) -> None:
    path_with_placeholder = Path("/config") / PROJECT_ID_PLACEHOLDER / "data" / "file.txt"
    resolved = resolve_placeholders(path_with_placeholder, project=test_project)
    assert resolved == Path("/config") / str(test_project.object_id) / "data" / "file.txt"


def test_apply_configuration_rule_simple_file(test_project: Project, tmp_path: Path) -> None:
    source_file = tmp_path / "config.txt"
    source_file.write_text("test configuration content")
    rule = ConfigurationRule(
        name="Test Config Rule",
        synchronize_from=source_file,
        synchronize_to=Path("/tmp/test_config.txt"),
    )
    plugin = ConfigServicePlugin(configuration_rules=(rule,))
    mock_environment = Mock()
    apply_configuration_rule(
        plugin=plugin,
        configuration_rule=rule,
        project=test_project,
        environment=mock_environment,
        home_local=tmp_path,
    )
    mock_environment.write_atomically.assert_called_once()
    call_args = mock_environment.write_atomically.call_args
    assert call_args[0][0] == "/tmp/test_config.txt"
    assert call_args[0][1] == "test configuration content"


def _augment(context: ConfigurationContext) -> str:
    return f"augmented: {context.configuration_contents}"


def test_apply_configuration_rule_with_augment(test_project: Project, tmp_path: Path) -> None:
    source_file = tmp_path / "config.txt"
    source_file.write_text("original content")
    rule = ConfigurationRule(
        name="Test Transform Rule",
        synchronize_from=source_file,
        synchronize_to=Path("/tmp/augmented_config.txt"),
        augment_function=_augment,
    )
    plugin = ConfigServicePlugin(configuration_rules=(rule,))
    mock_environment = Mock()
    mock_environment.get_container_user_home_directory.return_value = Path("/home/user")
    mock_environment.to_host_path.return_value = Path("/host/home/user")
    apply_configuration_rule(
        plugin=plugin,
        configuration_rule=rule,
        project=test_project,
        environment=mock_environment,
        home_local=tmp_path,
    )
    mock_environment.write_atomically.assert_called_once()
    call_args = mock_environment.write_atomically.call_args
    assert call_args[0][0] == "/tmp/augmented_config.txt"
    assert call_args[0][1] == "augmented: original content"


def _transform_to_none(context: ConfigurationContext) -> None:
    return None


def test_apply_configuration_rule_augment_returns_none_deletes_file(test_project: Project, tmp_path: Path) -> None:
    source_file = tmp_path / "config.txt"
    source_file.write_text("content to ignore")
    rule = ConfigurationRule(
        name="Test Delete Rule",
        synchronize_from=source_file,
        synchronize_to=Path("/tmp/deleted_config.txt"),
        augment_function=_transform_to_none,
    )
    plugin = ConfigServicePlugin(configuration_rules=(rule,))
    mock_environment = Mock()
    mock_environment.get_container_user_home_directory.return_value = Path("/home/user")
    mock_environment.to_host_path.return_value = Path("/host/home/user")
    apply_configuration_rule(
        plugin=plugin,
        configuration_rule=rule,
        project=test_project,
        environment=mock_environment,
        home_local=tmp_path,
    )
    mock_environment.delete_file_or_directory.assert_called_once_with("/tmp/deleted_config.txt")
    mock_environment.write_atomically.assert_not_called()
