import tempfile
from pathlib import Path
from typing import Iterator

from sculptor.services.environment_service.providers.docker.control_plane_volume_garbage_collector import (
    ControlPlaneVolumeInformation,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    ControlPlaneImageNameProvider,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import ControlPlaneRunMode
from sculptor.testing.container_utils import contextmanager


@contextmanager
def _setup_tag_files(publish_tag: str | None, local_tag: str | None) -> Iterator[tuple[Path, Path]]:
    with tempfile.NamedTemporaryFile(mode="w+t", delete=True, suffix=".txt", prefix="tag_path_") as tag_path_file:
        with tempfile.NamedTemporaryFile(
            mode="w+t", delete=True, suffix=".txt", prefix="local_tag_path_"
        ) as local_tag_path_file:
            if publish_tag is not None:
                tag_path_file.write(publish_tag)
                tag_path_file.flush()
            if local_tag is not None:
                local_tag_path_file.write(local_tag)
                local_tag_path_file.flush()

            nonexistent_file = "nonexistent_file.txt"
            tag_path = tag_path_file.name if publish_tag is not None else nonexistent_file
            local_tag_path = local_tag_path_file.name if local_tag is not None else nonexistent_file
            yield (Path(tag_path), Path(local_tag_path))


def test_chooses_local_tag_if_both_exist() -> None:
    with _setup_tag_files(publish_tag="tag-for-published", local_tag="tag-for-local") as (
        publish_tag_path,
        local_tag_path,
    ):
        image_name_provider = ControlPlaneImageNameProvider(
            control_plane_tag_path=publish_tag_path, control_plane_local_tag_path=local_tag_path
        )
        assert image_name_provider.determine_current_run_mode() == ControlPlaneRunMode.LOCALLY_BUILT


def test_chooses_published_tag_if_only_published_exists() -> None:
    with _setup_tag_files(publish_tag="tag-for-published", local_tag=None) as (publish_tag_path, local_tag_path):
        image_name_provider = ControlPlaneImageNameProvider(
            control_plane_tag_path=publish_tag_path, control_plane_local_tag_path=local_tag_path
        )
        assert image_name_provider.determine_current_run_mode() == ControlPlaneRunMode.TAGGED_RELEASE


def test_control_plane_image_name_for_local_tag() -> None:
    local_tag_to_use = "tag-for-local"
    with _setup_tag_files(publish_tag="tag-for-publish", local_tag=local_tag_to_use) as (
        publish_tag_path,
        local_tag_path,
    ):
        image_name_provider = ControlPlaneImageNameProvider(
            control_plane_tag_path=publish_tag_path, control_plane_local_tag_path=local_tag_path
        )

        image_url = image_name_provider.determine_control_plane_image_name()
        assert f"sculptorbase_nix:local_build_{local_tag_to_use}" in image_url


def test_control_plane_image_name_for_published_tag() -> None:
    publish_tag_to_use = "tag-for-published"
    with _setup_tag_files(publish_tag=publish_tag_to_use, local_tag=None) as (publish_tag_path, local_tag_path):
        image_name_provider = ControlPlaneImageNameProvider(
            control_plane_tag_path=publish_tag_path, control_plane_local_tag_path=local_tag_path
        )

        image_url = image_name_provider.determine_control_plane_image_name()

        assert "ghcr.io/imbue-ai/sculptorbase_nix" in image_url
        assert publish_tag_to_use in image_url
        assert "sha256:" in image_url


def test_control_plane_volume_information_round_trip() -> None:
    info = ControlPlaneVolumeInformation(is_dev_build=True, commit_hash="abc123", sha256="def456")
    volume_name = info.as_volume_name()
    parsed_info = ControlPlaneVolumeInformation.from_volume_name(volume_name)
    assert parsed_info == info


def test_control_plane_volume_information_invalid_name() -> None:
    invalid_names = [
        "not_a_control_plane_volume",
        "some_other_prefix",
    ]
    for name in invalid_names:
        assert ControlPlaneVolumeInformation.from_volume_name(name) is None
