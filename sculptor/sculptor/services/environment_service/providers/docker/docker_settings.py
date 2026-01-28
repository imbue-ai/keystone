"""Module for retrieving Docker settings and configuration.

This module provides functionality to retrieve Docker settings including:
- Docker client version
- Memory limit
- Disk limit
- Swap enabled
- Resource saver enabled
- Virtual machine manager (macOS only)
- Use containerd for pulling and storing images

Some settings are retrieved via `docker info`, while others may require
reading Docker Desktop's settings file (typically only available on macOS).
"""

import json
import platform
from pathlib import Path
from typing import Any

import humanfriendly
from loguru import logger
from packaging import version

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.subprocess_utils import ProcessError
from sculptor.web.data_types import SystemDependencyInfo
from sculptor.web.data_types import SystemRequirement
from sculptor.web.data_types import SystemRequirementStatus

# Some breadcrumbs to aid in understanding continued development.
# These are dumped by running:
# $ cat ~/Library/Group\ Containers/group.com.docker/settings-store.json| jq . | pbcopy
#
# There's a _very_ high chance that the logic is incorrect or incomplete but we have to start somewhere.
# Generally the code is pretty defensive but we _do_ need to make certain assumptions about defaults.
# Eventually we may want to have the logic leverage version information.

# The "default" settings file after _just_ installing Docker Desktop:
"""
{
  "AutoStart": false,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "UseContainerdSnapshotter": true
}
"""

# After disabling containerd
# Notice "UseContainerdSnapshotter": false
"""
{
  "AutoStart": false,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "UseContainerdSnapshotter": false
}
"""

# After switching to Docker VMM
# Notice "UseLibkrun": true, "UseVirtualizationFramework": false, "UseVirtualizationFrameworkRosetta": false
"""
{
  "AutoStart": false,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "UseContainerdSnapshotter": false,
  "UseLibkrun": true,
  "UseVirtualizationFramework": false,
  "UseVirtualizationFrameworkRosetta": false
}
"""

# After increasing memory limit to 12 GB
# Notice "MemoryMiB": 12288
"""
{
  "AutoStart": false,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "MemoryMiB": 12288,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "UseContainerdSnapshotter": false,
  "UseLibkrun": true,
  "UseVirtualizationFramework": false,
  "UseVirtualizationFrameworkRosetta": false
}
"""

# After disabling swap
# Notice "SwapMiB": 0
"""
{
  "AutoStart": false,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "MemoryMiB": 12288,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "SwapMiB": 0,
  "UseContainerdSnapshotter": false,
  "UseLibkrun": true,
  "UseVirtualizationFramework": false,
  "UseVirtualizationFrameworkRosetta": false
}
"""

# After decreasing disk size limit to 256 GB
# Notice "DiskSizeMiB": 262144
"""
{
  "AutoStart": false,
  "DiskSizeMiB": 262144,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "MemoryMiB": 12288,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "SwapMiB": 0,
  "UseContainerdSnapshotter": false,
  "UseLibkrun": true,
  "UseVirtualizationFramework": false,
  "UseVirtualizationFrameworkRosetta": false
}
"""

# After disabling resource saver
# Notice "UseResourceSaver": false
"""
{
  "AutoStart": false,
  "DiskSizeMiB": 262144,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "MemoryMiB": 12288,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "SwapMiB": 0,
  "UseContainerdSnapshotter": false,
  "UseLibkrun": true,
  "UseResourceSaver": false,
  "UseVirtualizationFramework": false,
  "UseVirtualizationFrameworkRosetta": false
}
"""

# After switching back to Apple Virtualization Framework
"""
{
  "AutoStart": false,
  "DiskSizeMiB": 262144,
  "DisplayedOnboarding": true,
  "DockerAppLaunchPath": "/Applications/Docker.app",
  "EnableDockerAI": true,
  "LastContainerdSnapshotterEnable": 1764017291,
  "LicenseTermsVersion": 2,
  "MemoryMiB": 12288,
  "SettingsVersion": 43,
  "ShowInstallScreen": false,
  "SwapMiB": 0,
  "UseContainerdSnapshotter": false,
  "UseLibkrun": false,
  "UseResourceSaver": false,
  "UseVirtualizationFramework": true,
  "UseVirtualizationFrameworkRosetta": false
}
"""

# Helper functions for creating common requirement types


def check_version_requirement(
    requirement_id: str,
    actual_version: str | None,
    min_version_str: str,
) -> SystemRequirement:
    """Check if a version meets a minimum version requirement.

    Args:
        requirement_id: Identifier for this requirement
        actual_version: The detected version string, or None if unknown
        min_version_str: Minimum required version (e.g., "27.0.0")

    Returns:
        SystemRequirement with status PASS/FAIL/UNKNOWN
    """
    requirement_description = f">={min_version_str}"

    if actual_version is None:
        return SystemRequirement(
            requirement_id=requirement_id,
            requirement_description=requirement_description,
            status=SystemRequirementStatus.UNKNOWN,
            actual_value=None,
        )

    try:
        actual_ver = version.parse(actual_version)
        min_ver = version.parse(min_version_str)
        status = SystemRequirementStatus.PASS if actual_ver >= min_ver else SystemRequirementStatus.FAIL
        return SystemRequirement(
            requirement_id=requirement_id,
            requirement_description=requirement_description,
            status=status,
            actual_value=actual_version,
        )
    except Exception as e:
        logger.debug("Error parsing version {}: {}", actual_version, e)
        return SystemRequirement(
            requirement_id=requirement_id,
            requirement_description=requirement_description,
            status=SystemRequirementStatus.UNKNOWN,
            actual_value=actual_version,
        )


def check_boolean_requirement(
    requirement_id: str,
    actual_value: bool | None,
    expected_value: bool,
    enabled_description: str = "Enabled",
    disabled_description: str = "Disabled",
) -> SystemRequirement:
    """Check if a boolean setting matches the expected value.

    Args:
        requirement_id: Identifier for this requirement
        actual_value: The detected boolean value, or None if unknown
        expected_value: The expected boolean value
        enabled_description: Human-readable string for True state
        disabled_description: Human-readable string for False state

    Returns:
        SystemRequirement with status PASS/FAIL/UNKNOWN
    """
    requirement_description = enabled_description if expected_value else disabled_description

    if actual_value is None:
        return SystemRequirement(
            requirement_id=requirement_id,
            requirement_description=requirement_description,
            status=SystemRequirementStatus.UNKNOWN,
            actual_value=None,
        )

    status = SystemRequirementStatus.PASS if actual_value == expected_value else SystemRequirementStatus.FAIL
    actual_value_str = enabled_description if actual_value else disabled_description

    return SystemRequirement(
        requirement_id=requirement_id,
        requirement_description=requirement_description,
        status=status,
        actual_value=actual_value_str,
    )


def check_minimum_bytes_requirement(
    requirement_id: str,
    actual_bytes: int | None,
    min_bytes: int,
) -> SystemRequirement:
    """Check if a byte value meets a minimum threshold.

    Args:
        requirement_id: Identifier for this requirement
        actual_bytes: The detected value in bytes, or None if unknown
        min_bytes: Minimum required value in bytes

    Returns:
        SystemRequirement with status PASS/FAIL/UNKNOWN
    """
    # Calculate human-readable minimum
    min_human_readable = humanfriendly.format_size(min_bytes, binary=True)
    requirement_description = f">={min_human_readable}"

    if actual_bytes is None:
        return SystemRequirement(
            requirement_id=requirement_id,
            requirement_description=requirement_description,
            status=SystemRequirementStatus.UNKNOWN,
            actual_value=None,
        )

    status = SystemRequirementStatus.PASS if actual_bytes >= min_bytes else SystemRequirementStatus.FAIL
    actual_value_str = humanfriendly.format_size(actual_bytes, binary=True)

    return SystemRequirement(
        requirement_id=requirement_id,
        requirement_description=requirement_description,
        status=status,
        actual_value=actual_value_str,
    )


def check_exact_value_requirement(
    requirement_id: str,
    actual_value: str | None,
    expected_value: str,
) -> SystemRequirement:
    """Check if a value exactly matches the expected value.

    Args:
        requirement_id: Identifier for this requirement
        actual_value: The detected value, or None if unknown
        expected_value: The expected value

    Returns:
        SystemRequirement with status PASS/FAIL/UNKNOWN
    """
    requirement_description = expected_value

    if actual_value is None:
        return SystemRequirement(
            requirement_id=requirement_id,
            requirement_description=requirement_description,
            status=SystemRequirementStatus.UNKNOWN,
            actual_value=None,
        )

    status = SystemRequirementStatus.PASS if actual_value == expected_value else SystemRequirementStatus.FAIL

    return SystemRequirement(
        requirement_id=requirement_id,
        requirement_description=requirement_description,
        status=status,
        actual_value=actual_value,
    )


class DockerSettings(SerializableModel):
    """Docker configuration settings (internal use).

    This contains the raw detected settings. For API responses,
    use to_system_dependency_info() to convert to SystemDependencyInfo.
    """

    platform: str

    # Version information
    client_version: str | None = None
    server_version: str | None = None

    # Resource limits from docker info
    memory_limit_bytes: int | None = None
    cpu_count: int | None = None

    # Settings that may require Docker Desktop settings file
    disk_limit_bytes: int | None = None
    swap_enabled: bool | None = None
    resource_saver_enabled: bool | None = None
    vm_manager: str | None = None  # e.g., "Apple Virtualization Framework", "Docker VMM"; macOS only
    use_containerd_for_images: bool | None = None

    # Error information
    error: str | None = None

    def to_system_dependency_info(self) -> SystemDependencyInfo:
        """Convert to generic SystemDependencyInfo for frontend consumption."""
        requirements = validate_docker_settings(self)

        # Determine overall status
        overall_status = SystemRequirementStatus.PASS
        for req in requirements:
            if req.status == SystemRequirementStatus.FAIL:
                overall_status = SystemRequirementStatus.FAIL
                break
            elif req.status == SystemRequirementStatus.UNKNOWN:
                overall_status = SystemRequirementStatus.UNKNOWN

        return SystemDependencyInfo(
            name="Docker",
            overall_status=overall_status,
            requirements=requirements,
            error=self.error,
        )


def get_docker_version(concurrency_group: ConcurrencyGroup) -> tuple[str | None, str | None]:
    """Get Docker client and server version.

    Returns:
        Tuple of (client_version, server_version). Either or both may be None if not available.
    """
    try:
        result = concurrency_group.run_process_to_completion(
            command=["docker", "version", "--format", "json"],
            timeout=10.0,
        )

        version_data = json.loads(result.stdout)
        client_version = version_data.get("Client", {}).get("Version")
        server_version = version_data.get("Server", {}).get("Version")
        if not isinstance(client_version, str):
            logger.debug("Docker client version is not a string: {}", client_version)
            client_version = None
        if not isinstance(server_version, str):
            logger.debug("Docker server version is not a string: {}", server_version)
            server_version = None
        return client_version, server_version
    except (ProcessError, json.JSONDecodeError, KeyError) as e:
        logger.debug("Error getting docker version: {}", e)
        return None, None


def get_docker_info_settings(concurrency_group: ConcurrencyGroup) -> dict[str, Any]:
    """Get Docker settings from `docker info`.

    Returns:
        Dictionary containing available docker info settings.
    """
    settings: dict[str, Any] = {}

    try:
        result = concurrency_group.run_process_to_completion(
            command=["docker", "info", "--format", "json"],
            timeout=10.0,
        )

        info_data = json.loads(result.stdout)

        if not isinstance(info_data, dict):
            logger.debug("Docker info data is not a dictionary: {}", info_data)
            return settings

        # Memory limit
        mem_total = info_data.get("MemTotal")
        if isinstance(mem_total, int):
            settings["memory_limit_bytes"] = mem_total

        # CPU count
        ncpu = info_data.get("NCPU")
        if isinstance(ncpu, int):
            settings["cpu_count"] = ncpu

        # Check if containerd is used for pulling and storing images (check "Driver" field)
        # In practice, this means that the "overlayfs" storage driver is used (as opposed to "overlay2")
        # This almost certainly is not a perfect check, but it is what we have observed in practice.
        driver = info_data.get("Driver")
        if isinstance(driver, str):
            if driver.lower() == "overlayfs":
                settings["use_containerd_for_images"] = True
            elif driver.lower() == "overlay2":
                settings["use_containerd_for_images"] = False

        return settings
    except (ProcessError, json.JSONDecodeError) as e:
        logger.debug("Error getting docker info settings: {}", e)
        return settings


def get_docker_desktop_settings_path() -> Path | None:
    """Get the path to Docker Desktop settings file.

    The settings file location depends on the platform:
    - macOS: ~/Library/Group Containers/group.com.docker/settings-store.json
    - Linux: Docker Desktop is not typically used, so this returns None

    Returns:
        Path to settings file if it exists (on appropriate platform), else None.
    """
    # We only support Docker Desktop settings on macOS for now.
    if platform.system() != "Darwin":
        return None

    settings_path = Path.home() / "Library" / "Group Containers" / "group.com.docker" / "settings-store.json"
    return settings_path if settings_path.exists() else None


def get_docker_desktop_settings(settings_path: Path) -> dict[str, Any]:
    """Read Docker Desktop settings from settings.json file.

    Args:
        settings_path: Path to Docker Desktop settings.json

    Returns:
        Dictionary containing available Docker Desktop settings.
    """
    # Our best guess at default settings.
    # This is incredibly brittle; these are not based on official documentation.
    settings: dict[str, Any] = {
        "resource_saver_enabled": True,
        "swap_enabled": True,
        "vm_manager": "Apple Virtualization Framework",
        "use_containerd_for_images": True,
    }

    try:
        with open(settings_path, "r") as f:
            desktop_settings = json.load(f)

        if not isinstance(desktop_settings, dict):
            return settings

        # Disk limit (size in MiB in settings, convert to bytes)
        disk_size_mib = desktop_settings.get("DiskSizeMiB")
        if isinstance(disk_size_mib, int):
            settings["disk_limit_bytes"] = disk_size_mib * 1024 * 1024

        # Memory limit (size in MiB in settings, convert to bytes)
        memory_mib = desktop_settings.get("MemoryMiB")
        if isinstance(memory_mib, int):
            settings["memory_limit_bytes"] = memory_mib * 1024 * 1024

        # Swap enabled
        swap_mib = desktop_settings.get("SwapMiB")
        if isinstance(swap_mib, int):
            settings["swap_enabled"] = swap_mib > 0

        # Resource saver
        use_resource_saver = desktop_settings.get("UseResourceSaver")
        if isinstance(use_resource_saver, bool):
            settings["resource_saver_enabled"] = use_resource_saver

        # VM manager (macOS specific)
        use_virtualization_framework = desktop_settings.get("UseVirtualizationFramework", True)
        use_libkrun = desktop_settings.get("UseLibkrun", False)
        if isinstance(use_virtualization_framework, bool) and use_virtualization_framework:
            settings["vm_manager"] = "Apple Virtualization Framework"
        elif isinstance(use_libkrun, bool) and use_libkrun:
            settings["vm_manager"] = "Docker VMM"

        # TODO: Determine the conditions that would indicate using QEMU.

        # Containerd (may also be in Desktop settings)
        use_containerd_snapshotter = desktop_settings.get("UseContainerdSnapshotter", True)
        if isinstance(use_containerd_snapshotter, bool):
            settings["use_containerd_for_images"] = use_containerd_snapshotter

        return settings
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Error reading Docker Desktop settings from {}: {}", settings_path, e)
        return settings


def validate_docker_settings(settings: DockerSettings) -> list[SystemRequirement]:
    """Validate Docker settings against platform-specific requirements.

    Args:
        settings: DockerSettings object containing detected settings

    Returns:
        List of SystemRequirement objects with validation results
    """
    requirements: list[SystemRequirement] = []

    # Docker version >= 27 (applies to all platforms)
    requirements.append(
        check_version_requirement(
            requirement_id="docker_version",
            actual_version=settings.client_version,
            min_version_str="27.0.0",
        )
    )

    # macOS-specific requirements
    if settings.platform == "Darwin":
        # Virtual machine manager should be "Docker VMM"
        requirements.append(
            check_exact_value_requirement(
                requirement_id="vm_manager",
                actual_value=settings.vm_manager,
                expected_value="Docker VMM",
            )
        )

        # Memory limit >= 8 GB
        requirements.append(
            check_minimum_bytes_requirement(
                requirement_id="memory_limit",
                actual_bytes=settings.memory_limit_bytes,
                min_bytes=8 * 1024**3,
            )
        )

        # Swap should be disabled
        requirements.append(
            check_boolean_requirement(
                requirement_id="swap",
                actual_value=settings.swap_enabled,
                expected_value=False,
            )
        )

        # Disk limit >= 100 GB
        requirements.append(
            check_minimum_bytes_requirement(
                requirement_id="disk_limit",
                actual_bytes=settings.disk_limit_bytes,
                min_bytes=100 * 1024**3,
            )
        )

        # Resource saver should be disabled
        requirements.append(
            check_boolean_requirement(
                requirement_id="resource_saver",
                actual_value=settings.resource_saver_enabled,
                expected_value=False,
            )
        )

        # Containerd for image storage should be disabled
        requirements.append(
            check_boolean_requirement(
                requirement_id="containerd_for_images",
                actual_value=settings.use_containerd_for_images,
                expected_value=False,
            )
        )

    return requirements


def get_docker_settings(concurrency_group: ConcurrencyGroup) -> DockerSettings:
    """Get comprehensive Docker settings.

    This function retrieves Docker settings from multiple sources:
    1. Docker version information
    2. Docker info command
    3. Docker Desktop settings file (if available)

    Args:
        concurrency_group: ConcurrencyGroup for running commands

    Returns:
        DockerSettings object containing all available settings
    """
    settings_dict = {}

    try:
        # Get version information
        client_version, server_version = get_docker_version(concurrency_group)
        settings_dict["client_version"] = client_version
        settings_dict["server_version"] = server_version

        info_settings = get_docker_info_settings(concurrency_group)
        settings_dict.update(info_settings)

        desktop_settings_path = get_docker_desktop_settings_path()
        if desktop_settings_path is not None:
            desktop_settings = get_docker_desktop_settings(desktop_settings_path)
            settings_dict.update(desktop_settings)

    except Exception as e:
        logger.info("Unexpected error getting Docker settings: {}", e)
        settings_dict["error"] = str(e)

    return DockerSettings(platform=platform.system(), **settings_dict)


def main() -> None:
    """Main function to test docker settings retrieval."""
    # Configure loguru.
    # logger.
    print("Docker Settings")
    print("=" * 70)

    with ConcurrencyGroup(name="docker_settings_test") as concurrency_group:
        settings = get_docker_settings(concurrency_group)

        print(f"Platform: {settings.platform}")
        print()

        # Version information
        print(f"Client Version: {settings.client_version or 'Not available'}")
        print(f"Server Version: {settings.server_version or 'Not available'}")
        print()

        # Resource limits
        print(
            f"Memory Limit: {humanfriendly.format_size(settings.memory_limit_bytes) if settings.memory_limit_bytes is not None else 'Not available'}"
        )
        print(f"CPU Count: {settings.cpu_count if settings.cpu_count else 'Not available'}")
        print(
            f"Disk Limit: {humanfriendly.format_size(settings.disk_limit_bytes) if settings.disk_limit_bytes is not None else 'Not available'}"
        )
        print()

        # Configuration settings
        swap_status = "Not available" if settings.swap_enabled is None else str(settings.swap_enabled)
        print(f"Swap Enabled: {swap_status}")

        rs_status = (
            "Not available" if settings.resource_saver_enabled is None else str(settings.resource_saver_enabled)
        )
        print(f"Resource Saver: {rs_status}")

        print(f"VM Manager: {settings.vm_manager or 'Not available'}")

        containerd_status = (
            "Not available" if settings.use_containerd_for_images is None else str(settings.use_containerd_for_images)
        )
        print(f"Containerd: {containerd_status}")

        if settings.error:
            print()
            print(f"Error: {settings.error}")

        # Convert to system dependency info and display validation results
        dep_info = settings.to_system_dependency_info()
        if dep_info.requirements:
            print()
            print("Validation Results:")
            print("-" * 70)
            for requirement in dep_info.requirements:
                status_symbol = (
                    "✓"
                    if requirement.status == SystemRequirementStatus.PASS
                    else "✗"
                    if requirement.status == SystemRequirementStatus.FAIL
                    else "?"
                )
                actual = f" (actual: {requirement.actual_value})" if requirement.actual_value else ""
                print(f"{status_symbol} {requirement.requirement_id}: {requirement.requirement_description}{actual}")

        print()
        print("=" * 70)


if __name__ == "__main__":
    main()
