from pathlib import Path
from typing import Final

from pytest import raises

from imbue_core.agents.data_types.ids import TaskID
from sculptor.interfaces.environments.errors import ImageConfigError
from sculptor.services.environment_service.api import TaskImageCleanupData
from sculptor.services.environment_service.providers.docker.image_utils import DeletionTier
from sculptor.services.environment_service.providers.docker.image_utils import UncachedBuildDetector
from sculptor.services.environment_service.providers.docker.image_utils import _classify_image_tier
from sculptor.services.environment_service.providers.docker.image_utils import _get_task_ids_by_image_id
from sculptor.services.environment_service.providers.docker.image_utils import _get_tier_by_image_id
from sculptor.services.environment_service.providers.docker.image_utils import _preprocess_lifecycle_command
from sculptor.services.environment_service.providers.docker.image_utils import _repository_root_from_devcontainer_path
from sculptor.services.environment_service.providers.docker.image_utils import calculate_image_ids_to_delete


def test_classify_deleted_task_returns_always_delete():
    task_metadata = TaskImageCleanupData(
        task_id=TaskID("tsk_01abc123def456789012345678"),
        last_image_id="image-latest",
        is_deleted=True,
        is_archived=False,
        all_image_ids=("image-1", "image-2", "image-latest"),
    )
    assert _classify_image_tier("image-1", task_metadata) == DeletionTier.ALWAYS_DELETE
    assert _classify_image_tier("image-latest", task_metadata) == DeletionTier.ALWAYS_DELETE


# def test_something_always_fails():
#     logger.info("hmmm, does this show up")
#     raise Exception("oops")


def test_classify_latest_image_on_active_task_returns_never_delete():
    task_metadata = TaskImageCleanupData(
        task_id=TaskID("tsk_01abc123def456789012345678"),
        last_image_id="image-latest",
        is_deleted=False,
        is_archived=False,
        all_image_ids=("image-1", "image-2", "image-latest"),
    )
    assert _classify_image_tier("image-latest", task_metadata) == DeletionTier.NEVER_DELETE


def test_classify_historical_image_on_active_task_returns_rarely_delete():
    task_metadata = TaskImageCleanupData(
        task_id=TaskID("tsk_01abc123def456789012345678"),
        last_image_id="image-latest",
        is_deleted=False,
        is_archived=False,
        all_image_ids=("image-1", "image-2", "image-latest"),
    )
    assert _classify_image_tier("image-1", task_metadata) == DeletionTier.RARELY_DELETE
    assert _classify_image_tier("image-2", task_metadata) == DeletionTier.RARELY_DELETE


def test_classify_historical_image_on_archived_task_returns_sometimes_delete():
    task_metadata = TaskImageCleanupData(
        task_id=TaskID("tsk_01abc123def456789012345678"),
        last_image_id="image-latest",
        is_deleted=False,
        is_archived=True,
        all_image_ids=("image-1", "image-2", "image-latest"),
    )
    assert _classify_image_tier("image-1", task_metadata) == DeletionTier.SOMETIMES_DELETE
    assert _classify_image_tier("image-2", task_metadata) == DeletionTier.SOMETIMES_DELETE


def test_classify_latest_image_on_archived_task_returns_never_delete():
    task_metadata = TaskImageCleanupData(
        task_id=TaskID("tsk_01abc123def456789012345678"),
        last_image_id="image-latest",
        is_deleted=False,
        is_archived=True,
        all_image_ids=("image-1", "image-2", "image-latest"),
    )
    assert _classify_image_tier("image-latest", task_metadata) == DeletionTier.NEVER_DELETE


def test_get_task_ids_single_task_single_image():
    task_id = TaskID("tsk_01abc123def456789012345678")
    task_metadata_by_task_id = {
        task_id: TaskImageCleanupData(
            task_id=task_id,
            last_image_id="image-1",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("image-1",),
        )
    }
    result = _get_task_ids_by_image_id(task_metadata_by_task_id)
    assert result == {"image-1": [TaskID("tsk_01abc123def456789012345678")]}


def test_get_task_ids_single_task_multiple_images():
    task_id = TaskID("tsk_01abc123def456789012345678")
    task_metadata_by_task_id = {
        task_id: TaskImageCleanupData(
            task_id=task_id,
            last_image_id="image-3",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("image-1", "image-2", "image-3"),
        )
    }
    result = _get_task_ids_by_image_id(task_metadata_by_task_id)
    assert result == {
        "image-1": [TaskID("tsk_01abc123def456789012345678")],
        "image-2": [TaskID("tsk_01abc123def456789012345678")],
        "image-3": [TaskID("tsk_01abc123def456789012345678")],
    }


def test_get_task_ids_multiple_tasks_sharing_images():
    task_id_1 = TaskID("tsk_01abc123def456789012345678")
    task_id_2 = TaskID("tsk_02def456789012345678abc123")
    task_metadata_by_task_id = {
        task_id_1: TaskImageCleanupData(
            task_id=task_id_1,
            last_image_id="image-2",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("image-1", "image-2"),
        ),
        task_id_2: TaskImageCleanupData(
            task_id=task_id_2,
            last_image_id="image-3",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("image-2", "image-3"),
        ),
    }
    result = _get_task_ids_by_image_id(task_metadata_by_task_id)
    assert result == {
        "image-1": [TaskID("tsk_01abc123def456789012345678")],
        "image-2": [TaskID("tsk_01abc123def456789012345678"), TaskID("tsk_02def456789012345678abc123")],
        "image-3": [TaskID("tsk_02def456789012345678abc123")],
    }


def test_get_tier_active_image_returns_never_delete():
    task_id = TaskID("tsk_01abc123def456789012345678")
    task_metadata_by_task_id = {
        task_id: TaskImageCleanupData(
            task_id=task_id,
            last_image_id="image-2",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("image-1", "image-2"),
        )
    }
    active_image_ids = ("image-1",)
    result = _get_tier_by_image_id(task_metadata_by_task_id, active_image_ids)
    assert result["image-1"] == DeletionTier.NEVER_DELETE


def test_get_tier_image_shared_by_multiple_tasks_takes_lowest_tier():
    task_id_1 = TaskID("tsk_01abc123def456789012345678")
    task_id_2 = TaskID("tsk_02def456789012345678abc123")
    task_metadata_by_task_id = {
        task_id_1: TaskImageCleanupData(
            task_id=task_id_1,
            last_image_id="image-2",
            is_deleted=True,  # Would be ALWAYS_DELETE
            is_archived=False,
            all_image_ids=("image-1", "image-2"),
        ),
        task_id_2: TaskImageCleanupData(
            task_id=task_id_2,
            last_image_id="image-3",
            is_deleted=False,
            is_archived=False,  # Would be RARELY_DELETE for image-1
            all_image_ids=("image-1", "image-3"),
        ),
    }
    active_image_ids = ()
    result = _get_tier_by_image_id(task_metadata_by_task_id, active_image_ids)
    assert result["image-1"] == DeletionTier.RARELY_DELETE  # Takes the lowest tier


def test_get_tier_latest_image_always_never_delete():
    task_id = TaskID("tsk_01abc123def456789012345678")
    task_metadata_by_task_id = {
        task_id: TaskImageCleanupData(
            task_id=task_id,
            last_image_id="image-2",
            is_deleted=False,
            is_archived=True,
            all_image_ids=("image-1", "image-2"),
        )
    }
    active_image_ids = ()
    result = _get_tier_by_image_id(task_metadata_by_task_id, active_image_ids)
    assert result["image-1"] == DeletionTier.SOMETIMES_DELETE
    assert result["image-2"] == DeletionTier.NEVER_DELETE  # Latest image


def test_get_tier_deleted_task_with_no_sharing():
    task_id = TaskID("tsk_01abc123def456789012345678")
    task_metadata_by_task_id = {
        task_id: TaskImageCleanupData(
            task_id=task_id,
            last_image_id="image-2",
            is_deleted=True,
            is_archived=False,
            all_image_ids=("image-1", "image-2"),
        )
    }
    active_image_ids = ()
    result = _get_tier_by_image_id(task_metadata_by_task_id, active_image_ids)
    assert result["image-1"] == DeletionTier.ALWAYS_DELETE
    assert result["image-2"] == DeletionTier.ALWAYS_DELETE


def test_get_tier_complex_scenario_with_all_tiers():
    task_id_1 = TaskID("tsk_01abc123def456789012345678")
    task_id_2 = TaskID("tsk_02def456789012345678abc123")
    task_id_3 = TaskID("tsk_03abc789012345678def456123")
    task_metadata_by_task_id = {
        task_id_1: TaskImageCleanupData(
            task_id=task_id_1,
            last_image_id="image-active-latest",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("image-active-old", "image-active-latest", "image-shared"),
        ),
        task_id_2: TaskImageCleanupData(
            task_id=task_id_2,
            last_image_id="image-archived-latest",
            is_deleted=False,
            is_archived=True,
            all_image_ids=("image-archived-old", "image-archived-latest", "image-shared"),
        ),
        task_id_3: TaskImageCleanupData(
            task_id=task_id_3,
            last_image_id="image-deleted",
            is_deleted=True,
            is_archived=False,
            all_image_ids=("image-deleted",),
        ),
    }
    active_image_ids = ("image-running",)
    result = _get_tier_by_image_id(task_metadata_by_task_id, active_image_ids)

    assert result["image-active-latest"] == DeletionTier.NEVER_DELETE
    assert result["image-active-old"] == DeletionTier.RARELY_DELETE
    assert result["image-archived-latest"] == DeletionTier.NEVER_DELETE
    assert result["image-archived-old"] == DeletionTier.SOMETIMES_DELETE
    assert result["image-deleted"] == DeletionTier.ALWAYS_DELETE
    assert result["image-shared"] == DeletionTier.RARELY_DELETE  # Shared by active task


def test_calculate_image_ids_to_delete_no_images():
    task_metadata_by_task_id = {}
    active_image_ids = ()
    existing_image_ids = ()
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.NEVER_DELETE
    )
    assert result == ()


def test_calculate_image_ids_to_delete_all_active_images():
    task_id = TaskID("tsk_01abc123def456789012345678")
    task_metadata_by_task_id = {
        task_id: TaskImageCleanupData(
            task_id=task_id,
            last_image_id="image-2",
            is_deleted=True,  # Would normally be deleted
            is_archived=False,
            all_image_ids=("image-1", "image-2"),
        )
    }
    active_image_ids = ("image-1", "image-2")  # Both images are active
    existing_image_ids = ("image-1", "image-2")
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.NEVER_DELETE
    )
    assert result == ()  # No images should be deleted since they're all active


def test_calculate_image_ids_to_delete_respects_minimum_tier():
    task_id_1 = TaskID("tsk_01abc123def456789012345678")
    task_id_2 = TaskID("tsk_02def456789012345678abc123")
    task_metadata_by_task_id = {
        task_id_1: TaskImageCleanupData(
            task_id=task_id_1,
            last_image_id="image-latest",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("image-old", "image-latest"),
        ),
        task_id_2: TaskImageCleanupData(
            task_id=task_id_2,
            last_image_id="image-archived-latest",
            is_deleted=False,
            is_archived=True,
            all_image_ids=("image-archived-old", "image-archived-latest"),
        ),
    }
    active_image_ids = ()
    existing_image_ids = ("image-old", "image-latest", "image-archived-old", "image-archived-latest")

    # With NEVER_DELETE minimum, only images above NEVER_DELETE are deleted
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.NEVER_DELETE
    )
    assert set(result) == {"image-old", "image-archived-old"}  # RARELY_DELETE and SOMETIMES_DELETE

    # With RARELY_DELETE minimum, only SOMETIMES_DELETE and above are deleted
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.RARELY_DELETE
    )
    assert set(result) == {"image-archived-old"}  # Only SOMETIMES_DELETE

    # With SOMETIMES_DELETE minimum, nothing gets deleted (no ALWAYS_DELETE images)
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.SOMETIMES_DELETE
    )
    assert result == ()


def test_calculate_image_ids_to_delete_only_existing_images():
    task_id = TaskID("tsk_01abc123def456789012345678")
    task_metadata_by_task_id = {
        task_id: TaskImageCleanupData(
            task_id=task_id,
            last_image_id="image-3",
            is_deleted=True,  # All images should be ALWAYS_DELETE
            is_archived=False,
            all_image_ids=("image-1", "image-2", "image-3"),
        )
    }
    active_image_ids = ()
    existing_image_ids = ("image-1", "image-3")  # image-2 doesn't exist
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.NEVER_DELETE
    )
    assert set(result) == {"image-1", "image-3"}  # Only existing images are returned


def test_calculate_image_ids_to_delete_complex_scenario():
    task_id_1 = TaskID("tsk_01abc123def456789012345678")
    task_id_2 = TaskID("tsk_02def456789012345678abc123")
    task_id_3 = TaskID("tsk_03abc789012345678def456123")

    task_metadata_by_task_id = {
        task_id_1: TaskImageCleanupData(
            task_id=task_id_1,
            last_image_id="active-latest",
            is_deleted=False,
            is_archived=False,
            all_image_ids=("active-old", "active-latest", "shared"),
        ),
        task_id_2: TaskImageCleanupData(
            task_id=task_id_2,
            last_image_id="archived-latest",
            is_deleted=False,
            is_archived=True,
            all_image_ids=("archived-old", "archived-latest", "shared"),
        ),
        task_id_3: TaskImageCleanupData(
            task_id=task_id_3,
            last_image_id="deleted-latest",
            is_deleted=True,
            is_archived=False,
            all_image_ids=("deleted-old", "deleted-latest"),
        ),
    }

    active_image_ids = ("running",)  # One image has a running container
    existing_image_ids = (
        "active-old",
        "active-latest",
        "archived-old",
        "archived-latest",
        "deleted-old",
        "deleted-latest",
        "shared",
        "running",
    )

    # Test with NEVER_DELETE minimum
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.NEVER_DELETE
    )
    # Should delete: active-old (RARELY), archived-old (SOMETIMES), deleted-old and deleted-latest (ALWAYS), shared (RARELY)
    assert set(result) == {"active-old", "archived-old", "deleted-old", "deleted-latest", "shared"}

    # Test with RARELY_DELETE minimum
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.RARELY_DELETE
    )
    # Should delete: archived-old (SOMETIMES), deleted-old and deleted-latest (ALWAYS)
    assert set(result) == {"archived-old", "deleted-old", "deleted-latest"}

    # Test with SOMETIMES_DELETE minimum
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.SOMETIMES_DELETE
    )
    # Should delete: deleted-old and deleted-latest (ALWAYS)
    assert set(result) == {"deleted-old", "deleted-latest"}

    # Test with ALWAYS_DELETE minimum (nothing gets deleted)
    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.ALWAYS_DELETE
    )
    assert result == ()


def test_calculate_image_ids_to_delete_shared_images_take_lowest_tier():
    task_id_1 = TaskID("tsk_01abc123def456789012345678")
    task_id_2 = TaskID("tsk_02def456789012345678abc123")

    task_metadata_by_task_id = {
        task_id_1: TaskImageCleanupData(
            task_id=task_id_1,
            last_image_id="task1-latest",
            is_deleted=True,  # Deleted task
            is_archived=False,
            all_image_ids=("shared-image", "task1-latest"),
        ),
        task_id_2: TaskImageCleanupData(
            task_id=task_id_2,
            last_image_id="shared-image",  # This is the latest for task2
            is_deleted=False,
            is_archived=False,
            all_image_ids=("shared-image",),
        ),
    }

    active_image_ids = ()
    existing_image_ids = ("shared-image", "task1-latest")

    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.NEVER_DELETE
    )
    # shared-image is NEVER_DELETE (latest for task2), task1-latest is ALWAYS_DELETE
    assert set(result) == {"task1-latest"}


def test_calculate_image_ids_to_delete_empty_task_metadata():
    # No tasks exist, so no images should be deleted
    task_metadata_by_task_id = {}
    active_image_ids = ()
    existing_image_ids = ("orphan-1", "orphan-2", "orphan-3")

    result = calculate_image_ids_to_delete(
        task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.NEVER_DELETE
    )
    assert result == ()  # No task metadata means no images to delete


def test_repository_root_from_devcontainer_path():
    root = Path("/some/repository/root")
    valid_devcontainer_paths = [
        root / ".devcontainer" / "devcontainer.json",
        root / ".devcontainer.json",
        root / ".devcontainer" / "some" / "devcontainer.json",
        root / ".devcontainer" / "other" / "devcontainer.json",
    ]

    for path in valid_devcontainer_paths:
        assert _repository_root_from_devcontainer_path(path) == root


def test_invalid_repository_root_from_devcontainer_path():
    root = Path("/some/repository/root")
    valid_devcontainer_paths = [
        root / ".devcontainersss" / "devcontainer.json",
        root / "devcontainer.json",
        root / ".devcontainer" / "asdfasd" / "some" / "devcontainer.json",
    ]

    for path in valid_devcontainer_paths:
        with raises(ImageConfigError):
            _repository_root_from_devcontainer_path(path)


def test_preprocess_lifecycle_command_single_string():
    """Test preprocessing a single command string"""
    commands = [
        ("one_call", ["one_call"]),
        ("one_call with args", ["one_call", "with", "args"]),
        ("one_call with 'quoted args'", ["one_call", "with", "quoted args"]),
    ]
    for in_cmd, split_cmd in commands:
        assert _preprocess_lifecycle_command(in_cmd, command_property="testCommand") == [
            ("testCommand", split_cmd),
        ]


def test_preprocess_lifecycle_command_single_list():
    """Test preprocessing a single command as a list"""
    commands = [
        (["asdfasd"], ["asdfasd"]),
        (["asdfasd", "asdfas adfas"], ["asdfasd", "asdfas adfas"]),
    ]
    for in_cmd, split_cmd in commands:
        assert _preprocess_lifecycle_command(in_cmd, command_property="testCommand") == [
            ("testCommand", split_cmd),
        ]


def test_preprocess_lifecycle_command_object():
    """Test preprocessing multiple named commands as an object"""
    val = {
        "command": "one_call",
        "args": ["one_call", "with", "args"],
        "command_quoted": "one_call with 'quoted args'",
    }
    result = _preprocess_lifecycle_command(val, command_property="testCommand")

    # sort the keys to make the test immune to object / dict iteration details
    result.sort(key=lambda x: x[0])
    assert result == [
        ("args", ["one_call", "with", "args"]),
        ("command", ["one_call"]),
        ("command_quoted", ["one_call", "with", "quoted args"]),
    ]


def test_preprocess_lifecycle_command_failures():
    """Test that invalid command types raise TypeError"""
    invalid_commands = [12, 1234.3, [1, 2, 3], {1: 1}]
    for cmd in invalid_commands:
        with raises(TypeError):
            _preprocess_lifecycle_command(cmd, command_property="testCommand")


def test_preprocess_lifecycle_command_with_different_properties():
    """Test that different command properties work correctly for single commands"""
    # Test with initializeCommand property
    result = _preprocess_lifecycle_command("npm install", command_property="initializeCommand")
    assert result == [("initializeCommand", ["npm", "install"])]

    # Test with onCreateCommand property
    result = _preprocess_lifecycle_command("npm install", command_property="onCreateCommand")
    assert result == [("onCreateCommand", ["npm", "install"])]

    # Test with postCreateCommand property
    result = _preprocess_lifecycle_command(["npm", "run", "build"], command_property="postCreateCommand")
    assert result == [("postCreateCommand", ["npm", "run", "build"])]


def test_preprocess_lifecycle_command_object_ignores_property_name():
    """Test that object commands use their own names, not the command_property"""
    val = {
        "install": "npm install",
        "build": "npm run build",
    }
    # Even with a custom command_property, object commands use their own names
    result = _preprocess_lifecycle_command(val, command_property="initializeCommand")
    result.sort(key=lambda x: x[0])
    assert result == [
        ("build", ["npm", "run", "build"]),
        ("install", ["npm", "install"]),
    ]


_UNCACHED_BUILD_LOGS: Final[str] = """\
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile_full
#1 transferring dockerfile: 1.70kB done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/alpine:3.18
#2 DONE 1.1s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [internal] load build context
#4 transferring context: 49B done
#4 DONE 0.1s

#5 [5/6] ADD https://www.google.com/robots.txt ./google_robots.txt
#5 DONE 0.1s

#6 [1/6] FROM docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f
#6 resolve docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f done
#6 sha256:95459497489f07b9d71d294c852a09f9bbf1af51bb35db752a31f6f48935e293 0B / 3.34MB 0.2s
#6 sha256:95459497489f07b9d71d294c852a09f9bbf1af51bb35db752a31f6f48935e293 2.10MB / 3.34MB 0.3s
#6 sha256:95459497489f07b9d71d294c852a09f9bbf1af51bb35db752a31f6f48935e293 3.34MB / 3.34MB 0.3s done
#6 extracting sha256:95459497489f07b9d71d294c852a09f9bbf1af51bb35db752a31f6f48935e293 0.1s done
#6 DONE 0.5s

#7 [2/6] WORKDIR /app
#7 DONE 0.0s

#8 [3/6] RUN echo "Installing packages... (Cache value: 1)" &&     apk add --no-cache curl
#8 0.220 Installing packages... (Cache value: 1)
#8 0.232 fetch https://dl-cdn.alpinelinux.org/alpine/v3.18/main/aarch64/APKINDEX.tar.gz
#8 0.370 fetch https://dl-cdn.alpinelinux.org/alpine/v3.18/community/aarch64/APKINDEX.tar.gz
#8 0.469 (1/8) Installing ca-certificates (20241121-r1)
#8 0.541 (2/8) Installing brotli-libs (1.0.9-r14)
#8 0.571 (3/8) Installing libunistring (1.1-r1)
#8 0.611 (4/8) Installing libidn2 (2.3.4-r1)
#8 0.632 (5/8) Installing nghttp2-libs (1.57.0-r0)
#8 0.654 (6/8) Installing libpsl (0.21.5-r0)
#8 0.680 (7/8) Installing libcurl (8.12.1-r0)
#8 0.716 (8/8) Installing curl (8.12.1-r0)
#8 0.746 Executing busybox-1.36.1-r7.trigger
#8 0.781 Executing ca-certificates-20241121-r1.trigger
#8 0.873 OK: 13 MiB in 23 packages
#8 DONE 0.9s

#9 [4/6] COPY local.txt ./local_copy.txt
#9 DONE 0.0s

#10 [5/6] ADD https://www.google.com/robots.txt ./google_robots.txt
#10 DONE 0.0s

#11 [6/6] RUN adduser -D appuser
#11 DONE 0.1s

#12 exporting to image
#12 exporting layers 0.1s done
#12 exporting manifest sha256:e9468f5e5b1b192e02cd2f1f5ea456e105f5fc3669e50faccca0b4be0b03f5fe
#12 exporting manifest sha256:e9468f5e5b1b192e02cd2f1f5ea456e105f5fc3669e50faccca0b4be0b03f5fe done
#12 exporting config sha256:b0ec42bb5054f09b14eea5db95acf4a5f0352a8a5a7fb87b6d3649e879d24ce1 done
#12 exporting attestation manifest sha256:b99e9dc3f8b04ebceda8946d3f6db93bb63d3002ba10c50c92192c193e1169da done
#12 exporting manifest list sha256:377ddc195f796a5c92ab040a0989aa4398db058385a446bb55ad78e994d54707 done
#12 naming to docker.io/library/sam:full1 done
#12 unpacking to docker.io/library/sam:full1 0.1s done
#12 DONE 0.2s
"""


class _CacheMissSignal:
    def __init__(self):
        self._detected = False

    def mark_miss(self):
        self._detected = True

    def was_miss_detected(self):
        return self._detected


def test_uncached_build_detector_uncached():
    signal = _CacheMissSignal()
    with UncachedBuildDetector(signal.mark_miss) as detector:
        for line in _UNCACHED_BUILD_LOGS.splitlines():
            detector.process_output_line(line)
    assert signal.was_miss_detected()


_CACHED_BUILD_LOGS: Final[str] = """\
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile_full
#1 transferring dockerfile: 1.70kB done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/alpine:3.18
#2 DONE 0.1s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [1/6] FROM docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f
#4 resolve docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f done
#4 DONE 0.0s

#5 [internal] load build context
#5 transferring context: 30B done
#5 DONE 0.0s

#6 [5/6] ADD https://www.google.com/robots.txt ./google_robots.txt
#6 DONE 0.0s

#7 [4/6] COPY local.txt ./local_copy.txt
#7 CACHED

#8 [5/6] ADD https://www.google.com/robots.txt ./google_robots.txt
#8 CACHED

#9 [3/6] RUN echo "Installing packages... (Cache value: 1)" &&     apk add --no-cache curl
#9 CACHED

#10 [2/6] WORKDIR /app
#10 CACHED

#11 [6/6] RUN adduser -D appuser
#11 CACHED

#12 exporting to image
#12 exporting layers done
#12 exporting manifest sha256:e9468f5e5b1b192e02cd2f1f5ea456e105f5fc3669e50faccca0b4be0b03f5fe done
#12 exporting config sha256:b0ec42bb5054f09b14eea5db95acf4a5f0352a8a5a7fb87b6d3649e879d24ce1 done
#12 exporting attestation manifest sha256:72be862909b80f3f57d3e3203dec5db193ddb2c078ea00a1a7a0ce2bf040077b done
#12 exporting manifest list sha256:33bbcafcf7b59ee7589f3c41924e82a3addd16f5d2680c8cd65b9f3d5cfae0c4 done
#12 naming to docker.io/library/sam:full1 done
#12 unpacking to docker.io/library/sam:full1 done
#12 DONE 0.0s
"""


def test_uncached_build_detector_cached():
    signal = _CacheMissSignal()
    with UncachedBuildDetector(signal.mark_miss) as detector:
        for line in _CACHED_BUILD_LOGS.splitlines():
            detector.process_output_line(line)
    assert not signal.was_miss_detected()


_UNCACHED_MULTI_STAGE_LOGS: Final[str] = """\
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile_multi
#1 transferring dockerfile: 1.23kB done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/alpine:3.18
#2 DONE 0.3s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [network_waiter 1/3] FROM docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f
#4 resolve docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f 0.0s done
#4 CACHED

#5 [final 2/4] WORKDIR /app
#5 CACHED

#6 [network_waiter 2/3] WORKDIR /download
#6 DONE 0.0s

#7 [slow_calculator 2/3] WORKDIR /build
#7 DONE 0.0s

#8 [slow_calculator 3/3] RUN echo "--- CALCULATOR STARTED ---" &&     for i in 1 2 3 4 5; do         echo "[Calculator] Compiling module $i/5...";         sleep 1;     done &&     echo "Calculation Complete" > result.txt
#8 0.241 --- CALCULATOR STARTED ---
#8 0.241 [Calculator] Compiling module 1/5...
#8 1.250 [Calculator] Compiling module 2/5...
#8 2.252 [Calculator] Compiling module 3/5...
#8 3.259 [Calculator] Compiling module 4/5...
#8 ...

#9 [network_waiter 3/3] RUN echo "--- NETWORK STARTED ---" &&     echo "[Network] Resolving host..." &&     sleep 2 &&     echo "[Network] Downloading assets..." &&     sleep 2 &&     echo "[Network] Unpacking..." &&     touch assets.tar.gz
#9 0.242 --- NETWORK STARTED ---
#9 0.242 [Network] Resolving host...
#9 2.247 [Network] Downloading assets...
#9 4.253 [Network] Unpacking...
#9 DONE 4.3s

#8 [slow_calculator 3/3] RUN echo "--- CALCULATOR STARTED ---" &&     for i in 1 2 3 4 5; do         echo "[Calculator] Compiling module $i/5...";         sleep 1;     done &&     echo "Calculation Complete" > result.txt
#8 4.261 [Calculator] Compiling module 5/5...
#8 DONE 5.3s

#10 [final 3/4] COPY --from=slow_calculator /build/result.txt .
#10 DONE 0.0s

#11 [final 4/4] COPY --from=network_waiter /download/assets.tar.gz .
#11 DONE 0.0s

#12 exporting to image
#12 exporting layers 0.0s done
#12 exporting manifest sha256:af53e92e093f2d527c8a529fc817b99d79b183dbaaa7f9729c972eb69beef867 done
#12 exporting config sha256:2843c42f4143f00b2d336811003437bc0d8e032071f3722a0dee6a0d71d72705 done
#12 exporting attestation manifest sha256:4b7600cbc33ebe4140968e58152be5066d2402c2ca46083ab984a8441dee7d40 done
#12 exporting manifest list sha256:ac0e5b3ce36f8aeead14e958e9f76c3320a03a2ff3973dd4d3f6d9a845608f77 done
#12 naming to docker.io/library/sam:multi1
#12 naming to docker.io/library/sam:multi1 done
#12 unpacking to docker.io/library/sam:multi1 0.0s done
#12 DONE 0.1s
"""


def test_uncached_build_detector_uncached_multi_stage():
    signal = _CacheMissSignal()
    with UncachedBuildDetector(signal.mark_miss) as detector:
        for line in _UNCACHED_MULTI_STAGE_LOGS.splitlines():
            detector.process_output_line(line)
    assert signal.was_miss_detected()


_CACHED_MULTI_STAGE_LOGS: Final[str] = """\
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile_multi
#1 transferring dockerfile: 1.23kB done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/alpine:3.18
#2 DONE 0.1s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [network_waiter 1/3] FROM docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f
#4 resolve docker.io/library/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f done
#4 DONE 0.0s

#5 [final 2/4] WORKDIR /app
#5 CACHED

#6 [slow_calculator 3/3] RUN echo "--- CALCULATOR STARTED ---" &&     for i in 1 2 3 4 5; do         echo "[Calculator] Compiling module $i/5...";         sleep 1;     done &&     echo "Calculation Complete" > result.txt
#6 CACHED

#7 [slow_calculator 2/3] WORKDIR /build
#7 CACHED

#8 [final 3/4] COPY --from=slow_calculator /build/result.txt .
#8 CACHED

#9 [network_waiter 2/3] WORKDIR /download
#9 CACHED

#10 [network_waiter 3/3] RUN echo "--- NETWORK STARTED ---" &&     echo "[Network] Resolving host..." &&     sleep 2 &&     echo "[Network] Downloading assets..." &&     sleep 2 &&     echo "[Network] Unpacking..." &&     touch assets.tar.gz
#10 CACHED

#11 [final 4/4] COPY --from=network_waiter /download/assets.tar.gz .
#11 CACHED

#12 exporting to image
#12 exporting layers done
#12 exporting manifest sha256:af53e92e093f2d527c8a529fc817b99d79b183dbaaa7f9729c972eb69beef867 done
#12 exporting config sha256:2843c42f4143f00b2d336811003437bc0d8e032071f3722a0dee6a0d71d72705 done
#12 exporting attestation manifest sha256:9b1115542b7abec5104a2737988ed33e6e21d98760e60d35fae074aa3a7011ac done
#12 exporting manifest list sha256:61940c0f8b04f00c0744a6fa09343bd6bf721c07d92a96ac68aedab83db87dac done
#12 naming to docker.io/library/sam:multi1 done
#12 unpacking to docker.io/library/sam:multi1 done
#12 DONE 0.0s
"""


def test_uncached_build_detector_cached_multi_stage():
    signal = _CacheMissSignal()
    with UncachedBuildDetector(signal.mark_miss) as detector:
        for line in _CACHED_MULTI_STAGE_LOGS.splitlines():
            detector.process_output_line(line)
    assert not signal.was_miss_detected()


_SIMPLE_BUILD_WITH_UNCACHED_FINAL_LAYER: Final[str] = """\
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile_simple_uncached
#1 transferring dockerfile: 91B done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/alpine:latest
#2 DONE 0.3s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [internal] load build context
#4 transferring context: 24B done
#4 DONE 0.0s

#5 [1/2] FROM docker.io/library/alpine:latest@sha256:4b7ce07002c69e8f3d704a9c5d6fd3053be500b7f1c69fc0d80990c2ad8dd412
#5 resolve docker.io/library/alpine:latest@sha256:4b7ce07002c69e8f3d704a9c5d6fd3053be500b7f1c69fc0d80990c2ad8dd412 done
#5 CACHED

#6 [2/2] COPY foo /tmp/foo
#6 DONE 0.0s

#7 exporting to image
#7 exporting layers 0.0s done
#7 exporting manifest sha256:9098c823dfacbcb1a92541eb1cdc852806c9c1ee00d9466eec0135f9dd49afda done
#7 exporting config sha256:c50a67906a990b9242fd6440747af9cceed689558ce09efb1ccdb7ab1068fd2c done
#7 exporting attestation manifest sha256:f2e7a1b46700ac6a5fefe0acc836ca17cef1dc34fc93e3afe7acc144516eafef done
#7 exporting manifest list sha256:17fa0f659273c0b882ff7d014f0656cd7b1f5c7608a459998c13422c5d367879 done
#7 naming to docker.io/library/sam:simpleun1 done
#7 unpacking to docker.io/library/sam:simpleun1 done
#7 DONE 0.1s
"""


def test_uncached_build_detector_simple_with_uncached_final_layer():
    signal = _CacheMissSignal()
    with UncachedBuildDetector(signal.mark_miss) as detector:
        for line in _SIMPLE_BUILD_WITH_UNCACHED_FINAL_LAYER.splitlines():
            detector.process_output_line(line)
    assert signal.was_miss_detected()
