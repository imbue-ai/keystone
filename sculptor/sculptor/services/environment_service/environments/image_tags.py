import datetime
import json
from enum import StrEnum
from typing import Literal

from loguru import logger

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.itertools import only
from imbue_core.pydantic_serialization import FrozenModel
from imbue_core.subprocess_utils import ProcessError
from sculptor.cli.sculptor_instance_utils import get_or_create_sculptor_instance_id
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import ImageIDTypes
from sculptor.interfaces.environments.errors import ProviderError
from sculptor.interfaces.environments.provider_status import OkStatus
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.services.environment_service.environments.utils import get_docker_status
from sculptor.utils.build import is_dev_build

SNAPSHOT_SUFFIX = "-snapshot"
USER_IMAGE_SUFFIX = "_user_image_to_wrap"
INSTANCE_ID_LABEL_NAME = "instance_id"


# TODO(millan, sam): Figure out a sensible way to combine `ImageInfo` and `DockerImageMetadata`.
# Ideally we would have a _single_ mechanism for encoding and decoding structured metadata into/from docker image tags and labels.
class ImageInfo(FrozenModel):
    repository: str
    tag: str
    id: str
    created_at: str

    @property
    def category(self) -> Literal["USER", "WRAPPED", "SNAPSHOT"]:
        if self.tag.endswith(USER_IMAGE_SUFFIX):
            return "USER"
        if self.repository.endswith(SNAPSHOT_SUFFIX):
            return "SNAPSHOT"
        return "WRAPPED"


class NonTestingEnvironmentPrefix(StrEnum):
    DEV = "dev-sculptor-"
    PROD = "sculptor-"


class ImageCreatedFor(StrEnum):
    TASK = "task"
    DAILY_CACHE = "daily_cache"
    TESTING = "testing"


class DockerImageMetadata(FrozenModel):
    tag: str
    labels: dict[str, str]


class ImageMetadataV0(FrozenModel):
    identifier: str
    is_user_image: bool

    @classmethod
    def from_docker_metadata(cls, docker_image_metadata: DockerImageMetadata) -> "ImageMetadataV0":
        if docker_image_metadata.tag.endswith(USER_IMAGE_SUFFIX):
            identifier = docker_image_metadata.tag.removesuffix(USER_IMAGE_SUFFIX)
            is_user_image = True
        else:
            identifier = docker_image_metadata.tag
            is_user_image = False

        return cls(
            identifier=identifier,
            is_user_image=is_user_image,
        )

    def to_docker_metadata(self) -> DockerImageMetadata:
        return DockerImageMetadata(
            tag=self.identifier + (USER_IMAGE_SUFFIX if self.is_user_image else ""),
            labels={},
        )


def get_environment_prefix(settings: SculptorSettings) -> str:
    if settings.TESTING.CONTAINER_PREFIX is not None:
        return settings.TESTING.CONTAINER_PREFIX
    return str(get_non_testing_environment_prefix())


class ImageMetadataV1(FrozenModel):
    created_for: ImageCreatedFor
    identifier: str
    sequence_number: int = 0
    sculptor_instance_id: str = get_or_create_sculptor_instance_id()
    is_user_image: bool = False

    def to_docker_metadata(self) -> DockerImageMetadata:
        return DockerImageMetadata(
            tag=f"v1-{self.created_for}-{self.identifier}-{self.sequence_number}"
            + (f"{USER_IMAGE_SUFFIX}" if self.is_user_image else ""),
            labels={INSTANCE_ID_LABEL_NAME: self.sculptor_instance_id},
        )

    @classmethod
    def from_task(cls, task_id: TaskID, sequence_number: int = 0, is_user_image: bool = False) -> "ImageMetadataV1":
        return cls(
            created_for=ImageCreatedFor.TASK,
            identifier=str(task_id),
            sequence_number=sequence_number,
            is_user_image=is_user_image,
        )

    @classmethod
    def from_daily_cache(cls, day: datetime.date) -> "ImageMetadataV1":
        return cls(
            created_for=ImageCreatedFor.DAILY_CACHE,
            identifier=str(day),
        )

    @classmethod
    def from_testing(cls) -> "ImageMetadataV1":
        return cls(
            created_for=ImageCreatedFor.TESTING,
            identifier="",
        )

    @classmethod
    def from_docker_metadata(cls, docker_metadata: DockerImageMetadata) -> "ImageMetadataV1":
        tag_string = docker_metadata.tag
        is_user_image = tag_string.endswith(USER_IMAGE_SUFFIX)
        tag_without_suffix = tag_string.removesuffix(USER_IMAGE_SUFFIX)
        _, created_for_str, all_identifying_data = tag_without_suffix.split("-", maxsplit=2)
        identifier, _, sequence_number_str = all_identifying_data.rpartition("-")
        created_for = ImageCreatedFor(created_for_str)
        return cls(
            created_for=created_for,
            identifier=identifier,
            sequence_number=int(sequence_number_str),
            is_user_image=is_user_image,
            sculptor_instance_id=docker_metadata.labels[INSTANCE_ID_LABEL_NAME],
        )


ImageMetadata = ImageMetadataV0 | ImageMetadataV1


def get_image_metadata(docker_image_metadata: DockerImageMetadata) -> ImageMetadata:
    tag_string = docker_image_metadata.tag
    maybe_version_number, separator, maybe_tag_contents = tag_string.partition("-")
    if not separator:
        return ImageMetadataV0.from_docker_metadata(docker_image_metadata)
    if maybe_version_number == "v1":
        return ImageMetadataV1.from_docker_metadata(docker_image_metadata)
    else:
        raise ValueError("Unsupported version number in image tag: " + maybe_version_number)


def parse_image_info_associated_with_this_sculptor_instance(image_info: ImageInfo) -> ImageMetadata:
    return get_image_metadata(
        DockerImageMetadata(
            tag=image_info.tag,
            labels={
                INSTANCE_ID_LABEL_NAME: get_or_create_sculptor_instance_id(),
            },
        )
    )


def get_v1_image_ids_and_metadata_for_task(
    task_id: TaskID, concurrency_group: ConcurrencyGroup, environment_prefix: str
) -> list[tuple[str, ImageMetadataV1]]:
    sculptor_image_infos = get_current_sculptor_images_info(concurrency_group, environment_prefix)
    sculptor_image_ids_and_metadata = [
        (image_info.id, parse_image_info_associated_with_this_sculptor_instance(image_info))
        for image_info in sculptor_image_infos
    ]
    return [
        (image_id, image_metadata)
        for image_id, image_metadata in sculptor_image_ids_and_metadata
        if isinstance(image_metadata, ImageMetadataV1)
        and image_metadata.created_for == ImageCreatedFor.TASK
        and image_metadata.identifier == str(task_id)
    ]


def get_latest_v1_image_metadata_for_task(
    task_id: TaskID, concurrency_group: ConcurrencyGroup, settings: SculptorSettings
) -> ImageMetadataV1 | None:
    _, latest_image_metadata = max(
        get_v1_image_ids_and_metadata_for_task(task_id, concurrency_group, get_environment_prefix(settings)),
        key=lambda image_id_and_metadata: image_id_and_metadata[1].sequence_number,
        default=(None, None),
    )
    return latest_image_metadata


def _add_tag_for_fork(
    image_id: str,
    existing_image_metadata: ImageMetadataV1,
    new_task_id: TaskID,
    concurrency_group: ConcurrencyGroup,
) -> None:
    assert existing_image_metadata.created_for == ImageCreatedFor.TASK
    add_tag_to_docker_image(
        ImageMetadataV1.from_task(
            task_id=new_task_id,
            sequence_number=existing_image_metadata.sequence_number,
        )
        .to_docker_metadata()
        .tag,
        image_id,
        concurrency_group,
    )


def add_ancestral_tags_for_fork(
    base_task_id: TaskID,
    forked_task_id: TaskID,
    forked_from_image_id: ImageIDTypes,
    concurrency_group: ConcurrencyGroup,
    settings: SculptorSettings,
) -> None:
    base_task_image_ids_and_metadata = get_v1_image_ids_and_metadata_for_task(
        base_task_id, concurrency_group, get_environment_prefix(settings)
    )
    if len(base_task_image_ids_and_metadata) == 0:
        return
    base_image_metadata = only(
        image_metadata
        for image_id, image_metadata in base_task_image_ids_and_metadata
        if image_id == str(forked_from_image_id)
    )
    assert base_image_metadata.created_for == ImageCreatedFor.TASK
    relevant_task_image_ids_and_metadata = [
        image_id_and_metadata
        for image_id_and_metadata in get_v1_image_ids_and_metadata_for_task(
            TaskID(base_image_metadata.identifier), concurrency_group, get_environment_prefix(settings)
        )
        if image_id_and_metadata[1].sequence_number <= base_image_metadata.sequence_number
    ]

    with ObservableThreadPoolExecutor(concurrency_group=concurrency_group, max_workers=10) as executor:
        executor.map(
            lambda id_and_metadata: _add_tag_for_fork(
                id_and_metadata[0], id_and_metadata[1], forked_task_id, concurrency_group
            ),
            relevant_task_image_ids_and_metadata,
        )


def add_tag_to_docker_image(tag: str, image_id: str, concurrency_group: ConcurrencyGroup) -> None:
    try:
        tags_result = concurrency_group.run_process_to_completion(
            ["docker", "inspect", "--format={{json .RepoTags}}", image_id]
        )
    except ProcessError as e:
        health_status = get_docker_status(concurrency_group)
        if not isinstance(health_status, OkStatus):
            logger.debug("Docker seems to be down, cannot list image information")
            details_msg = f" (details: {health_status.details})" if health_status.details else ""
            raise ProviderError(f"Provider is unavailable: {health_status.message}{details_msg}") from e
        raise
    existing_tags = json.loads(tags_result.stdout.strip())
    existing_name = existing_tags[0].split(":")[0]
    concurrency_group.run_process_to_completion(["docker", "tag", image_id, f"{existing_name}:{tag}"])


def get_non_testing_environment_prefix() -> NonTestingEnvironmentPrefix:
    if is_dev_build():
        return NonTestingEnvironmentPrefix.DEV
    return NonTestingEnvironmentPrefix.PROD


def get_tagged_reference(repo_name: str, image_metadata: ImageMetadata):
    return f"{repo_name}:{image_metadata.to_docker_metadata().tag}"


def get_current_sculptor_images_info(
    concurrency_group: ConcurrencyGroup,
    sculptor_image_prefix: str,
    filter_to_instance_id: bool = False,
    shutdown_event: ReadOnlyEvent | None = None,
) -> tuple[ImageInfo, ...]:
    try:
        result = concurrency_group.run_process_to_completion(
            command=(
                "docker",
                "images",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"reference={sculptor_image_prefix}*",
                *(
                    ("--filter", f"label={INSTANCE_ID_LABEL_NAME}={get_or_create_sculptor_instance_id()}")
                    if filter_to_instance_id
                    else ()
                ),
                "--format={{.Repository}} {{.Tag}} {{.ID}} {{.CreatedAt}}",
            ),
            shutdown_event=shutdown_event,
        )
    except ProcessError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        health_status = get_docker_status(concurrency_group)
        if not isinstance(health_status, OkStatus):
            logger.debug("Docker seems to be down, cannot list images")
            details_msg = f" (details: {health_status.details})" if health_status.details else ""
            raise ProviderError(f"Provider is unavailable: {health_status.message}{details_msg}") from e
        else:
            raise
    image_infos = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        repo, tag, id, created_at = line.split(maxsplit=3)
        if line:
            image_infos.append(
                ImageInfo(
                    repository=repo,
                    tag=tag,
                    id=id,
                    created_at=created_at,
                )
            )
    return tuple(image_infos)
