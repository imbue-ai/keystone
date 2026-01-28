import datetime

import pytest

from imbue_core.agents.data_types.ids import TaskID
from sculptor.services.environment_service.environments.image_tags import DockerImageMetadata
from sculptor.services.environment_service.environments.image_tags import ImageCreatedFor
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV0
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.image_tags import get_image_metadata

# ImageMetadataV0 Tests


def test_v0_encode_regular_image() -> None:
    """Test encoding a regular (non-user) image."""
    metadata = ImageMetadataV0(identifier="test_image_123", is_user_image=False)
    docker_metadata = metadata.to_docker_metadata()

    assert docker_metadata.tag == "test_image_123"
    assert docker_metadata.labels == {}


def test_v0_encode_user_image() -> None:
    """Test encoding a user image."""
    metadata = ImageMetadataV0(identifier="my_custom_image", is_user_image=True)
    docker_metadata = metadata.to_docker_metadata()

    assert docker_metadata.tag == "my_custom_image_user_image_to_wrap"
    assert docker_metadata.labels == {}


def test_v0_roundtrip_regular_image() -> None:
    """Test encoding and decoding a regular image."""
    original = ImageMetadataV0(identifier="ubuntu_2204", is_user_image=False)
    docker_metadata = original.to_docker_metadata()
    decoded = ImageMetadataV0.from_docker_metadata(docker_metadata)

    assert decoded.identifier == original.identifier
    assert decoded.is_user_image == original.is_user_image


def test_v0_roundtrip_user_image() -> None:
    """Test encoding and decoding a user image."""
    original = ImageMetadataV0(identifier="custom_env", is_user_image=True)
    docker_metadata = original.to_docker_metadata()
    decoded = ImageMetadataV0.from_docker_metadata(docker_metadata)

    assert decoded.identifier == original.identifier
    assert decoded.is_user_image == original.is_user_image


def test_v0_parse_image_metadata() -> None:
    """Test parsing V0 metadata using the generic parser."""
    docker_metadata = DockerImageMetadata(tag="simple_tag", labels={})
    parsed = get_image_metadata(docker_metadata)

    assert isinstance(parsed, ImageMetadataV0)
    assert parsed.identifier == "simple_tag"
    assert parsed.is_user_image is False


def test_v0_parse_image_metadata_user_image() -> None:
    """Test parsing V0 user image metadata using the generic parser."""
    docker_metadata = DockerImageMetadata(tag="my_image_user_image_to_wrap", labels={})
    parsed = get_image_metadata(docker_metadata)

    assert isinstance(parsed, ImageMetadataV0)
    assert parsed.identifier == "my_image"
    assert parsed.is_user_image is True


# ImageMetadataV1 Tests


def test_v1_encode_task_image() -> None:
    """Test encoding a task-based image."""
    task_id = TaskID()
    metadata = ImageMetadataV1.from_task(task_id, sequence_number=5)
    docker_metadata = metadata.to_docker_metadata()

    assert docker_metadata.tag == f"v1-task-{task_id}-5"
    assert "instance_id" in docker_metadata.labels


def test_v1_encode_task_user_image() -> None:
    """Test encoding a task-based user image."""
    task_id = TaskID()
    metadata = ImageMetadataV1.from_task(task_id, sequence_number=2, is_user_image=True)
    docker_metadata = metadata.to_docker_metadata()

    assert docker_metadata.tag == f"v1-task-{task_id}-2_user_image_to_wrap"
    assert "instance_id" in docker_metadata.labels


def test_v1_encode_daily_cache_image() -> None:
    """Test encoding a daily cache image."""
    day = datetime.date(2025, 11, 17)
    metadata = ImageMetadataV1.from_daily_cache(day)
    docker_metadata = metadata.to_docker_metadata()

    assert docker_metadata.tag == "v1-daily_cache-2025-11-17-0"
    assert "instance_id" in docker_metadata.labels


def test_v1_encode_testing_image() -> None:
    """Test encoding a testing image."""
    metadata = ImageMetadataV1.from_testing()
    docker_metadata = metadata.to_docker_metadata()

    assert docker_metadata.tag == "v1-testing--0"
    assert "instance_id" in docker_metadata.labels


def test_v1_roundtrip_task_image() -> None:
    """Test encoding and decoding a task image."""
    task_id = TaskID()
    original = ImageMetadataV1.from_task(task_id, sequence_number=3)
    docker_metadata = original.to_docker_metadata()
    decoded = ImageMetadataV1.from_docker_metadata(docker_metadata)

    assert decoded.created_for == ImageCreatedFor.TASK
    assert decoded.identifier == str(task_id)
    assert decoded.sequence_number == 3
    assert decoded.is_user_image is False
    assert decoded.sculptor_instance_id == original.sculptor_instance_id


def test_v1_roundtrip_task_user_image() -> None:
    """Test encoding and decoding a task user image."""
    task_id = TaskID()
    original = ImageMetadataV1.from_task(task_id, sequence_number=7, is_user_image=True)
    docker_metadata = original.to_docker_metadata()
    decoded = ImageMetadataV1.from_docker_metadata(docker_metadata)

    assert decoded.created_for == ImageCreatedFor.TASK
    assert decoded.identifier == str(task_id)
    assert decoded.sequence_number == 7
    assert decoded.is_user_image is True
    assert decoded.sculptor_instance_id == original.sculptor_instance_id


def test_v1_roundtrip_daily_cache_image() -> None:
    """Test encoding and decoding a daily cache image."""
    day = datetime.date(2025, 10, 31)
    original = ImageMetadataV1.from_daily_cache(day)
    docker_metadata = original.to_docker_metadata()
    decoded = ImageMetadataV1.from_docker_metadata(docker_metadata)

    assert decoded.created_for == ImageCreatedFor.DAILY_CACHE
    assert decoded.identifier == str(day)
    assert decoded.sequence_number == 0
    assert decoded.is_user_image is False
    assert decoded.sculptor_instance_id == original.sculptor_instance_id


def test_v1_roundtrip_testing_image() -> None:
    """Test encoding and decoding a testing image."""
    original = ImageMetadataV1.from_testing()
    docker_metadata = original.to_docker_metadata()
    decoded = ImageMetadataV1.from_docker_metadata(docker_metadata)

    assert decoded.created_for == ImageCreatedFor.TESTING
    assert decoded.identifier == ""
    assert decoded.sequence_number == 0
    assert decoded.is_user_image is False
    assert decoded.sculptor_instance_id == original.sculptor_instance_id


def test_v1_parse_image_metadata() -> None:
    """Test parsing V1 metadata using the generic parser."""
    docker_metadata = DockerImageMetadata(tag="v1-task-some-task-id-10", labels={"instance_id": "test-instance-123"})
    parsed = get_image_metadata(docker_metadata)

    assert isinstance(parsed, ImageMetadataV1)
    assert parsed.created_for == ImageCreatedFor.TASK
    assert parsed.identifier == "some-task-id"
    assert parsed.sequence_number == 10
    assert parsed.is_user_image is False


def test_v1_parse_image_metadata_user_image() -> None:
    """Test parsing V1 user image metadata using the generic parser."""
    docker_metadata = DockerImageMetadata(
        tag="v1-daily_cache-2025-12-25-0_user_image_to_wrap",
        labels={"instance_id": "test-instance-456"},
    )
    parsed = get_image_metadata(docker_metadata)

    assert isinstance(parsed, ImageMetadataV1)
    assert parsed.created_for == ImageCreatedFor.DAILY_CACHE
    assert parsed.identifier == "2025-12-25"
    assert parsed.is_user_image is True


# Generic parse_image_metadata tests


def test_parse_v0_without_version_prefix() -> None:
    """Test that tags without version prefix are parsed as V0."""
    docker_metadata = DockerImageMetadata(tag="no_version_here", labels={})
    parsed = get_image_metadata(docker_metadata)

    assert isinstance(parsed, ImageMetadataV0)


def test_parse_v1_with_version_prefix() -> None:
    """Test that tags with v1 prefix are parsed as V1."""
    docker_metadata = DockerImageMetadata(tag="v1-testing--0", labels={"instance_id": "test"})
    parsed = get_image_metadata(docker_metadata)

    assert isinstance(parsed, ImageMetadataV1)


def test_invalid_version_raises_error() -> None:
    """Test that unsupported version numbers raise an error."""
    docker_metadata = DockerImageMetadata(tag="v99-future-version", labels={})

    with pytest.raises(ValueError, match="Unsupported version number"):
        get_image_metadata(docker_metadata)
