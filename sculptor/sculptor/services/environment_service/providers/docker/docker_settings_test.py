"""Unit tests for docker_settings module."""

import json
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.subprocess_utils import ProcessError
from sculptor.services.environment_service.providers.docker.docker_settings import DockerSettings
from sculptor.services.environment_service.providers.docker.docker_settings import get_docker_desktop_settings
from sculptor.services.environment_service.providers.docker.docker_settings import get_docker_desktop_settings_path
from sculptor.services.environment_service.providers.docker.docker_settings import get_docker_info_settings
from sculptor.services.environment_service.providers.docker.docker_settings import get_docker_settings
from sculptor.services.environment_service.providers.docker.docker_settings import get_docker_version
from sculptor.services.environment_service.providers.docker.docker_settings import validate_docker_settings
from sculptor.web.data_types import SystemRequirementStatus


def test_get_docker_version_success() -> None:
    """Test successful docker version retrieval."""
    mock_concurrency_group = Mock(spec=ConcurrencyGroup)
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(
        {
            "Client": {"Version": "24.0.0"},
            "Server": {"Version": "24.0.0"},
        }
    )
    mock_concurrency_group.run_process_to_completion.return_value = mock_result

    client_version, server_version = get_docker_version(mock_concurrency_group)

    assert client_version == "24.0.0"
    assert server_version == "24.0.0"
    mock_concurrency_group.run_process_to_completion.assert_called_once()


def test_get_docker_version_failure() -> None:
    """Test docker version retrieval when command fails."""
    mock_concurrency_group = Mock(spec=ConcurrencyGroup)
    mock_concurrency_group.run_process_to_completion.side_effect = ProcessError(
        command=(), returncode=1, stdout="", stderr="Error"
    )

    client_version, server_version = get_docker_version(mock_concurrency_group)

    assert client_version is None
    assert server_version is None


def test_get_docker_info_settings_success() -> None:
    """Test successful docker info retrieval."""
    mock_concurrency_group = Mock(spec=ConcurrencyGroup)
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(
        {
            "MemTotal": 8589934592,  # 8GB in bytes
            "NCPU": 8,
            "Driver": "overlayfs",
        }
    )
    mock_concurrency_group.run_process_to_completion.return_value = mock_result

    settings = get_docker_info_settings(mock_concurrency_group)

    assert settings["memory_limit_bytes"] == 8589934592
    assert settings["cpu_count"] == 8
    assert settings["use_containerd_for_images"] is True


def test_get_docker_info_settings_minimal() -> None:
    """Test docker info with minimal fields."""
    mock_concurrency_group = Mock(spec=ConcurrencyGroup)
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({"NCPU": 4})
    mock_concurrency_group.run_process_to_completion.return_value = mock_result

    settings = get_docker_info_settings(mock_concurrency_group)

    assert settings["cpu_count"] == 4
    assert "memory_limit_bytes" not in settings


def test_get_docker_desktop_settings_path_macos() -> None:
    """Test getting Docker Desktop settings path on macOS."""
    with patch("platform.system", return_value="Darwin"):
        with patch("pathlib.Path.exists", return_value=True):
            path = get_docker_desktop_settings_path()
            assert path is not None
            assert "group.com.docker" in str(path)


def test_get_docker_desktop_settings_path_linux() -> None:
    """Test getting Docker Desktop settings path on Linux (should return None)."""
    with patch("platform.system", return_value="Linux"):
        path = get_docker_desktop_settings_path()
        assert path is None


def test_get_docker_desktop_settings() -> None:
    """Test reading Docker Desktop settings file."""
    settings_data = {
        "DiskSizeMiB": 61440,  # 60GB
        "SwapMiB": 1024,
        "UseResourceSaver": True,
        "UseVirtualizationFramework": True,
        "UseContainerdSnapshotter": True,
    }

    mock_path = Mock(spec=Path)
    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(settings_data)
        mock_file = MagicMock()
        mock_file.__enter__.return_value = MagicMock()
        mock_open.return_value = mock_file

        with patch("json.load", return_value=settings_data):
            settings = get_docker_desktop_settings(mock_path)

        assert settings["disk_limit_bytes"] == 61440 * 1024 * 1024
        assert settings["swap_enabled"] is True
        assert settings["resource_saver_enabled"] is True
        assert settings["vm_manager"] == "Apple Virtualization Framework"
        assert settings["use_containerd_for_images"] is True


def test_get_docker_settings_comprehensive() -> None:
    """Test comprehensive docker settings retrieval."""
    mock_concurrency_group = Mock(spec=ConcurrencyGroup)

    # Mock version response
    version_result = Mock()
    version_result.returncode = 0
    version_result.stdout = json.dumps(
        {
            "Client": {"Version": "24.0.0"},
            "Server": {"Version": "24.0.0"},
        }
    )

    # Mock info response
    info_result = Mock()
    info_result.returncode = 0
    info_result.stdout = json.dumps(
        {
            "MemTotal": 8589934592,
            "NCPU": 8,
        }
    )

    mock_concurrency_group.run_process_to_completion.side_effect = [version_result, info_result]

    with patch(
        "sculptor.services.environment_service.providers.docker.docker_settings.get_docker_desktop_settings_path",
        return_value=None,
    ):
        settings = get_docker_settings(mock_concurrency_group)

    assert settings.client_version == "24.0.0"
    assert settings.server_version == "24.0.0"
    assert settings.memory_limit_bytes == 8589934592
    assert settings.cpu_count == 8
    assert settings.error is None


def test_get_docker_settings_with_exception() -> None:
    """Test docker settings retrieval handles exceptions gracefully."""
    mock_concurrency_group = Mock(spec=ConcurrencyGroup)
    mock_concurrency_group.run_process_to_completion.side_effect = Exception("Test error")

    settings = get_docker_settings(mock_concurrency_group)

    assert settings.error == "Test error"


def test_validate_docker_settings_linux_pass() -> None:
    """Test validation on Linux with Docker >= 27."""
    settings = DockerSettings(
        platform="Linux",
        client_version="27.0.0",
    )

    requirements = validate_docker_settings(settings)

    assert len(requirements) == 1
    assert requirements[0].requirement_id == "docker_version"
    assert requirements[0].requirement_description == ">=27.0.0"
    assert requirements[0].status == SystemRequirementStatus.PASS
    assert requirements[0].actual_value == "27.0.0"


def test_validate_docker_settings_linux_fail() -> None:
    """Test validation on Linux with Docker < 27."""
    settings = DockerSettings(
        platform="Linux",
        client_version="26.1.0",
    )

    requirements = validate_docker_settings(settings)

    assert len(requirements) == 1
    assert requirements[0].requirement_id == "docker_version"
    assert requirements[0].status == SystemRequirementStatus.FAIL
    assert requirements[0].actual_value == "26.1.0"


def test_validate_docker_settings_linux_unknown() -> None:
    """Test validation on Linux with unknown Docker version."""
    settings = DockerSettings(
        platform="Linux",
        client_version=None,
    )

    requirements = validate_docker_settings(settings)

    assert len(requirements) == 1
    assert requirements[0].requirement_id == "docker_version"
    assert requirements[0].status == SystemRequirementStatus.UNKNOWN
    assert requirements[0].actual_value is None


def test_validate_docker_settings_macos_all_pass() -> None:
    """Test validation on macOS with all settings passing."""
    settings = DockerSettings(
        platform="Darwin",
        client_version="27.1.0",
        vm_manager="Docker VMM",
        memory_limit_bytes=10 * 1024**3,  # 10 GB
        swap_enabled=False,
        disk_limit_bytes=150 * 1024**3,  # 150 GB
        resource_saver_enabled=False,
        use_containerd_for_images=False,
    )

    requirements = validate_docker_settings(settings)

    # Should have 7 requirements: version, vm_manager, memory, swap, disk, resource_saver, containerd
    assert len(requirements) == 7

    # All should pass
    for suggestion in requirements:
        assert suggestion.status == SystemRequirementStatus.PASS


def test_validate_docker_settings_macos_all_fail() -> None:
    """Test validation on macOS with all settings failing."""
    settings = DockerSettings(
        platform="Darwin",
        client_version="26.0.0",
        vm_manager="Apple Virtualization Framework",
        memory_limit_bytes=4 * 1024**3,  # 4 GB
        swap_enabled=True,
        disk_limit_bytes=50 * 1024**3,  # 50 GB
        resource_saver_enabled=True,
        use_containerd_for_images=True,
    )

    requirements = validate_docker_settings(settings)

    # Should have 7 requirements
    assert len(requirements) == 7

    # All should fail
    for suggestion in requirements:
        assert suggestion.status == SystemRequirementStatus.FAIL


def test_validate_docker_settings_macos_mixed() -> None:
    """Test validation on macOS with mixed results."""
    settings = DockerSettings(
        platform="Darwin",
        client_version="27.0.0",  # Pass
        vm_manager="Docker VMM",  # Pass
        memory_limit_bytes=6 * 1024**3,  # Fail (< 8 GB)
        swap_enabled=False,  # Pass
        disk_limit_bytes=None,  # Unknown
        resource_saver_enabled=True,  # Fail
        use_containerd_for_images=None,  # Unknown
    )

    requirements = validate_docker_settings(settings)

    # Should have 7 requirements
    assert len(requirements) == 7

    # Check specific statuses
    docker_version = next(s for s in requirements if s.requirement_id == "docker_version")
    assert docker_version.status == SystemRequirementStatus.PASS

    vm_manager = next(s for s in requirements if s.requirement_id == "vm_manager")
    assert vm_manager.status == SystemRequirementStatus.PASS
    assert vm_manager.actual_value == "Docker VMM"

    memory = next(s for s in requirements if s.requirement_id == "memory_limit")
    assert memory.status == SystemRequirementStatus.FAIL
    actual_value = memory.actual_value
    assert isinstance(actual_value, str)
    assert actual_value.startswith("6")

    swap = next(s for s in requirements if s.requirement_id == "swap")
    assert swap.status == SystemRequirementStatus.PASS
    assert swap.actual_value == "Disabled"

    disk = next(s for s in requirements if s.requirement_id == "disk_limit")
    assert disk.status == SystemRequirementStatus.UNKNOWN
    assert disk.actual_value is None

    resource_saver = next(s for s in requirements if s.requirement_id == "resource_saver")
    assert resource_saver.status == SystemRequirementStatus.FAIL
    assert resource_saver.actual_value == "Enabled"

    containerd = next(s for s in requirements if s.requirement_id == "containerd_for_images")
    assert containerd.status == SystemRequirementStatus.UNKNOWN
    assert containerd.actual_value is None


def test_validate_docker_settings_version_parsing_error() -> None:
    """Test validation handles version parsing errors gracefully."""
    settings = DockerSettings(
        platform="Linux",
        client_version="invalid-version",
    )

    requirements = validate_docker_settings(settings)

    assert len(requirements) == 1
    assert requirements[0].requirement_id == "docker_version"
    assert requirements[0].status == SystemRequirementStatus.UNKNOWN
    assert requirements[0].actual_value == "invalid-version"


def test_get_docker_settings_to_system_dependency_info() -> None:
    """Test that get_docker_settings can be converted to SystemDependencyInfo."""
    mock_concurrency_group = Mock(spec=ConcurrencyGroup)

    # Mock version response
    version_result = Mock()
    version_result.returncode = 0
    version_result.stdout = json.dumps(
        {
            "Client": {"Version": "27.0.0"},
            "Server": {"Version": "27.0.0"},
        }
    )

    # Mock info response
    info_result = Mock()
    info_result.returncode = 0
    info_result.stdout = json.dumps(
        {
            "MemTotal": 8589934592,
            "NCPU": 8,
        }
    )

    mock_concurrency_group.run_process_to_completion.side_effect = [version_result, info_result]

    with patch(
        "sculptor.services.environment_service.providers.docker.docker_settings.get_docker_desktop_settings_path",
        return_value=None,
    ):
        with patch("platform.system", return_value="Linux"):
            settings = get_docker_settings(mock_concurrency_group)

    # Convert to SystemDependencyInfo
    dep_info = settings.to_system_dependency_info()
    assert dep_info.name == "Docker"
    assert len(dep_info.requirements) > 0
    assert dep_info.requirements[0].requirement_id == "docker_version"


def test_docker_settings_to_system_dependency_info() -> None:
    """Test that DockerSettings.to_system_dependency_info() provides a clean frontend-focused payload."""
    # Create a full DockerSettings object
    full_settings = DockerSettings(
        platform="Darwin",
        client_version="27.0.0",
        server_version="27.0.0",
        memory_limit_bytes=10 * 1024**3,
        cpu_count=8,
        disk_limit_bytes=150 * 1024**3,
        swap_enabled=False,
        resource_saver_enabled=False,
        vm_manager="Docker VMM",
        use_containerd_for_images=False,
    )

    # Convert to SystemDependencyInfo
    dep_info = full_settings.to_system_dependency_info()

    # Verify the response has what frontend needs
    assert dep_info.name == "Docker"
    assert len(dep_info.requirements) == 7
    assert dep_info.error is None
    assert dep_info.overall_status == SystemRequirementStatus.PASS

    # Verify requirements contain human-readable information
    for requirement in dep_info.requirements:
        assert requirement.requirement_id
        assert requirement.requirement_description
        assert requirement.status in [
            SystemRequirementStatus.PASS,
            SystemRequirementStatus.FAIL,
            SystemRequirementStatus.UNKNOWN,
        ]
