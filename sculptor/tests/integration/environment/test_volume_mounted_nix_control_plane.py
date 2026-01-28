import subprocess
from typing import Final
from typing import Generator

import pytest

from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    ControlPlaneImageNameProvider,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import ControlPlaneRunMode
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    _fetch_control_plane_volume,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import _is_volume_ready
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    get_control_plane_volume_docker_args,
)

_ALPINE_IMAGE_URL: Final[str] = "alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1"
_TEST_VOLUME_NAME: Final[str] = "imbue_control_plane_test_volume"
_TEST_CONTROL_PLANE_IMAGE: Final[str] = "imbue_test_control_plane:latest"


@pytest.fixture
def isolated_test_volume(monkeypatch) -> Generator[str, None, None]:
    """Fixture that sets SCULPTOR_CONTROL_PLANE_VOLUME to a test-specific volume name.

    This isolates tests from any running Sculptor instance that might be using the
    real control plane volume. The test volume is cleaned up after the test.
    """
    monkeypatch.setenv("SCULPTOR_CONTROL_PLANE_VOLUME", _TEST_VOLUME_NAME)

    # Reset BackgroundSetup state so it doesn't think the volume is already ready
    # pylint: disable=protected-access
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP._is_finished = False

    try:
        yield _TEST_VOLUME_NAME
    finally:
        # Clean up the test volume
        subprocess.run(
            ["docker", "volume", "rm", "-f", _TEST_VOLUME_NAME],
            capture_output=True,
            check=False,
        )
        # Reset BackgroundSetup state again
        CONTROL_PLANE_FETCH_BACKGROUND_SETUP._is_finished = False


@pytest.fixture
def isolated_test_environment(monkeypatch) -> Generator[tuple[str, str], None, None]:
    """Fixture that sets up an isolated test environment with test volume and test image.

    This creates a minimal test control plane image with /imbue directory and rsync,
    and sets both SCULPTOR_CONTROL_PLANE_VOLUME and SCULPTOR_CONTROL_PLANE_IMAGE env vars.
    This allows tests to run _fetch_control_plane_volume without needing CDN access.
    """
    # Build the test control plane image
    # Note: We install rsync with --no-scripts to avoid busybox trigger issues in some environments
    dockerfile_content = """FROM alpine:latest
RUN mkdir -p /imbue/bin /imbue/nix
RUN echo "test control plane" > /imbue/README.txt
RUN apk add --no-cache --no-scripts rsync || apk add --no-cache rsync
"""
    build_result = subprocess.run(
        ["docker", "build", "-t", _TEST_CONTROL_PLANE_IMAGE, "-"],
        input=dockerfile_content,
        capture_output=True,
        text=True,
        check=False,
    )
    if build_result.returncode != 0:
        pytest.skip(f"Could not build test control plane image: {build_result.stderr}")

    # Set env vars
    monkeypatch.setenv("SCULPTOR_CONTROL_PLANE_VOLUME", _TEST_VOLUME_NAME)
    monkeypatch.setenv("SCULPTOR_CONTROL_PLANE_IMAGE", _TEST_CONTROL_PLANE_IMAGE)

    # Reset BackgroundSetup state
    # pylint: disable=protected-access
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP._is_finished = False

    try:
        yield (_TEST_VOLUME_NAME, _TEST_CONTROL_PLANE_IMAGE)
    finally:
        # Clean up
        subprocess.run(["docker", "volume", "rm", "-f", _TEST_VOLUME_NAME], capture_output=True, check=False)
        subprocess.run(["docker", "image", "rm", "-f", _TEST_CONTROL_PLANE_IMAGE], capture_output=True, check=False)

        CONTROL_PLANE_FETCH_BACKGROUND_SETUP._is_finished = False


@pytest.mark.integration
def test_nix_control_plane_on_bare_alpine_runs_claude(test_root_concurrency_group: ConcurrencyGroup):
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP.ensure_finished(test_root_concurrency_group)
    docker_cmd = [
        *("docker", "run", "-t", "--rm"),
        *get_control_plane_volume_docker_args(),
        _ALPINE_IMAGE_URL,
        # We need to manually create the /nix symlink, because it's not available in vanilla alpine.
        # We typically run claude from /imbue_addons/bin, but that isn't available in vanilla alpine.
        *("sh", "-c", "ln -s /imbue/nix /nix && /imbue/nix_bin/claude --version"),
    ]
    result = subprocess.run(docker_cmd, capture_output=True, text=True, check=True)
    assert "Claude Code" in result.stdout
    assert result.returncode == 0


@pytest.mark.integration
def test_nix_control_plane_on_bare_alpine_runs_imbue_cli(test_root_concurrency_group: ConcurrencyGroup):
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP.ensure_finished(test_root_concurrency_group)
    docker_cmd = [
        *("docker", "run", "-t", "--rm"),
        *get_control_plane_volume_docker_args(),
        _ALPINE_IMAGE_URL,
        # We need to manually create the /nix symlink, because it's not available in vanilla alpine.
        *("sh", "-c", "ln -s /imbue/nix /nix && /imbue/bin/imbue-cli.sh --help"),
    ]
    result = subprocess.run(docker_cmd, capture_output=True, text=True, check=True)
    assert "imbue-cli" in result.stdout
    assert result.returncode == 0


@pytest.mark.integration
def test_control_plane_image_deleted_after_volume_creation(test_root_concurrency_group: ConcurrencyGroup):
    """Test that the control plane Docker image is deleted after the volume is populated."""
    provider = ControlPlaneImageNameProvider()
    if provider.determine_current_run_mode() != ControlPlaneRunMode.TAGGED_RELEASE:
        pytest.skip("This test only makes sense if the control plane was downloaded from the CDN.")

    # Set the run mode to TAGGED_RELEASE
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP.ensure_finished(test_root_concurrency_group)

    # Get the image name that should have been deleted
    image_name = provider.determine_control_plane_image_name()

    # Check if the image exists in Docker
    inspect_cmd = ["docker", "inspect", image_name]
    result = subprocess.run(inspect_cmd, capture_output=True, text=True, check=False)

    # The image should not exist (returncode != 0) after volume creation
    assert result.returncode != 0, f"Expected image {image_name} to be deleted, but it still exists in Docker"


@pytest.mark.integration
def test_control_plane_volume_recreated_after_deletion(
    test_root_concurrency_group: ConcurrencyGroup, isolated_test_volume: str
):
    """Test that the control plane volume can be recreated after manual deletion."""
    # Ensure the volume is created initially
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP.ensure_finished(test_root_concurrency_group)

    # Get the volume name
    volume_name = ControlPlaneImageNameProvider().get_control_plane_volume_name()

    # Verify the volume exists
    inspect_cmd = ["docker", "volume", "inspect", volume_name]
    result = subprocess.run(inspect_cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Expected volume {volume_name} to exist"

    # Delete the volume
    rm_cmd = ["docker", "volume", "rm", volume_name]
    result = subprocess.run(rm_cmd, capture_output=True, text=True, check=True)

    # Verify the volume was deleted
    inspect_cmd = ["docker", "volume", "inspect", volume_name]
    result = subprocess.run(inspect_cmd, capture_output=True, text=True)
    assert result.returncode != 0, f"Expected volume {volume_name} to be deleted"

    # Call the fetch function again to recreate the volume
    _fetch_control_plane_volume(test_root_concurrency_group)

    # Verify the volume was recreated
    inspect_cmd = ["docker", "volume", "inspect", volume_name]
    result = subprocess.run(inspect_cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Expected volume {volume_name} to be recreated"


def _docker_image_exists(image_name: str) -> bool:
    """Check if a Docker image exists locally."""
    result = subprocess.run(
        ["docker", "image", "inspect", image_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _docker_volume_exists(volume_name: str) -> bool:
    """Check if a Docker volume exists."""
    result = subprocess.run(
        ["docker", "volume", "inspect", volume_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _delete_docker_image(image_name: str) -> None:
    """Delete a Docker image if it exists."""
    subprocess.run(["docker", "image", "rm", "-f", image_name], capture_output=True, check=False)


def _delete_docker_volume(volume_name: str) -> None:
    """Delete a Docker volume if it exists."""
    subprocess.run(["docker", "volume", "rm", "-f", volume_name], capture_output=True, check=False)


@pytest.mark.integration
def test_no_volume_no_image_creates_both(
    test_root_concurrency_group: ConcurrencyGroup, isolated_test_environment: tuple[str, str]
):
    """Test case 1: No volume or image exists - both must be created."""
    volume_name, image_name = isolated_test_environment

    # Delete the image to simulate starting with neither volume nor image
    _delete_docker_image(image_name)

    # Verify neither exists
    assert not _docker_volume_exists(volume_name), "Volume should not exist before test"
    assert not _docker_image_exists(image_name), "Image should not exist before test"
    assert not _is_volume_ready(volume_name, test_root_concurrency_group), "_is_volume_ready should return False"

    # Rebuild the test image (simulating it being downloaded/built)
    # Note: We install rsync with --no-scripts to avoid busybox trigger issues in some environments
    dockerfile_content = """FROM alpine:latest
RUN mkdir -p /imbue/bin /imbue/nix
RUN echo "test control plane" > /imbue/README.txt
RUN apk add --no-cache --no-scripts rsync || apk add --no-cache rsync
"""
    subprocess.run(
        ["docker", "build", "-t", image_name, "-"],
        input=dockerfile_content,
        capture_output=True,
        text=True,
        check=True,
    )

    # Run the fetch function
    _fetch_control_plane_volume(test_root_concurrency_group)

    # Verify volume was created and is ready
    assert _docker_volume_exists(volume_name), "Volume should exist after fetch"
    assert _is_volume_ready(volume_name, test_root_concurrency_group), (
        "_is_volume_ready should return True after fetch"
    )


@pytest.mark.integration
def test_image_exists_no_volume_creates_volume(
    test_root_concurrency_group: ConcurrencyGroup, isolated_test_environment: tuple[str, str]
):
    """Test case 2: Image exists but no volume - creates volume using existing image."""
    volume_name, image_name = isolated_test_environment

    # The fixture already created the image, just make sure volume doesn't exist
    _delete_docker_volume(volume_name)

    # Verify state: image exists, volume doesn't
    assert _docker_image_exists(image_name), "Image should exist before test"
    assert not _docker_volume_exists(volume_name), "Volume should not exist before test"
    assert not _is_volume_ready(volume_name, test_root_concurrency_group), "_is_volume_ready should return False"

    # Run the fetch function - it should use the existing image
    _fetch_control_plane_volume(test_root_concurrency_group)

    # Verify volume was created
    assert _docker_volume_exists(volume_name), "Volume should exist after fetch"
    assert _is_volume_ready(volume_name, test_root_concurrency_group), (
        "_is_volume_ready should return True after fetch"
    )


@pytest.mark.integration
def test_volume_exists_no_image_skips_download(
    test_root_concurrency_group: ConcurrencyGroup, isolated_test_volume: str
):
    """Test case 3: Volume exists but no image - should skip download entirely.

    This test creates a volume with the sentinel file directly (without using the control plane image),
    then verifies that _fetch_control_plane_volume detects the volume is ready and returns early.
    """
    volume_name = isolated_test_volume

    # Create the volume with a sentinel file using alpine (simulating a ready volume)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume_name}:/imbue_volume",
            "alpine:latest",
            "touch",
            "/imbue_volume/VOLUME_READY.TXT",
        ],
        check=True,
    )

    # Verify state: volume exists and is ready
    assert _docker_volume_exists(volume_name), "Volume should exist before test"
    assert _is_volume_ready(volume_name, test_root_concurrency_group), "_is_volume_ready should return True"

    # Run the fetch function - it should detect the volume is ready and skip everything
    # This should return immediately without downloading anything
    _fetch_control_plane_volume(test_root_concurrency_group)

    # Verify volume still exists and is ready
    assert _docker_volume_exists(volume_name), "Volume should still exist"
    assert _is_volume_ready(volume_name, test_root_concurrency_group), "_is_volume_ready should still return True"


@pytest.mark.integration
def test_both_volume_and_image_exist_can_delete_image(
    test_root_concurrency_group: ConcurrencyGroup, isolated_test_environment: tuple[str, str]
):
    """Test case 4: Both volume and image exist - we can safely delete the image."""
    volume_name, image_name = isolated_test_environment

    # First create the volume using _fetch_control_plane_volume
    _fetch_control_plane_volume(test_root_concurrency_group)

    # Verify state: both volume and image exist
    assert _docker_volume_exists(volume_name), "Volume should exist"
    assert _is_volume_ready(volume_name, test_root_concurrency_group), "_is_volume_ready should return True"
    assert _docker_image_exists(image_name), "Image should exist"

    # Run the fetch function again - it should detect the volume is ready and skip
    _fetch_control_plane_volume(test_root_concurrency_group)

    # Volume should still exist and be ready
    assert _docker_volume_exists(volume_name), "Volume should still exist"
    assert _is_volume_ready(volume_name, test_root_concurrency_group), "_is_volume_ready should still return True"

    # We can safely delete the image since the volume has everything we need
    _delete_docker_image(image_name)
    assert not _docker_image_exists(image_name), "Image should be deleted"

    # Volume should still be usable
    assert _is_volume_ready(volume_name, test_root_concurrency_group), (
        "_is_volume_ready should still return True after image deletion"
    )
