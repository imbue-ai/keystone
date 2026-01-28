"""Unit tests for control_plane_volume_garbage_collector module."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import Mock

import pytest

from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.subprocess_utils import FinishedProcess
from sculptor.services.environment_service.providers.docker.control_plane_volume_garbage_collector import (
    ControlPlaneVolumeGarbageCollector,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    ControlPlaneVolumeInformation,
)

# The latest/current volume that should never be pruned
EXAMPLE_SHAS = [hashlib.sha256(f"{i}".encode("utf-8")).hexdigest() for i in range(30)]
LATEST_INFO = ControlPlaneVolumeInformation(
    is_dev_build=False,
    commit_hash="latestcommit",
    sha256=EXAMPLE_SHAS[0],
)
LATEST_VOLUME_NAME = LATEST_INFO.as_volume_name()


@dataclass
class MockVolume:
    """Helper class to represent a mock volume for testing."""

    name: str
    created_at: datetime | None = None  # None = no CreatedAt field
    in_use: bool = False


def mock_docker_volumes_state(mock_concurrency_group: Mock, volumes: list[MockVolume]) -> None:
    """Set up mock docker volume state.

    Args:
        mock_concurrency_group: The mock concurrency group to configure
        volumes: List of MockVolume objects representing the docker volumes state
    """
    # Create a mapping from volume name to volume info
    volumes_by_name = {v.name: v for v in volumes}

    def side_effect(command: list[str], shutdown_event: ReadOnlyEvent | None = None) -> FinishedProcess:
        if command[1] == "volume" and command[2] == "ls":
            # Return all volume names
            volume_names = "\n".join(v.name for v in volumes)
            return FinishedProcess(
                returncode=0, stdout=volume_names, stderr="", command=tuple(command), is_output_already_logged=False
            )

        elif command[1] == "ps":
            # Check if the queried volume is in use
            # Command format: ["docker", "ps", "-a", "--filter", "volume=<name>", "--format", "{{.ID}}"]
            volume_filter = command[4]  # "volume=<name>"
            volume_name = volume_filter.split("=", 1)[1]

            volume = volumes_by_name.get(volume_name)
            if volume and volume.in_use:
                # Return a mock container ID
                return FinishedProcess(
                    returncode=0,
                    stdout="container_id_123\n",
                    stderr="",
                    command=tuple(command),
                    is_output_already_logged=False,
                )
            else:
                # No containers using this volume
                return FinishedProcess(
                    returncode=0, stdout="", stderr="", command=tuple(command), is_output_already_logged=False
                )

        elif command[1] == "volume" and command[2] == "inspect":
            # Return volume creation date
            volume_name = command[3]
            volume = volumes_by_name.get(volume_name)

            if volume and volume.created_at is not None:
                volume_info = {"CreatedAt": volume.created_at.isoformat().replace("+00:00", "Z")}
                return FinishedProcess(
                    returncode=0,
                    stdout=json.dumps(volume_info),
                    stderr="",
                    command=tuple(command),
                    is_output_already_logged=False,
                )
            else:
                # Volume not found or no creation date (created_at is None)
                volume_info = {}
                return FinishedProcess(
                    returncode=0,
                    stdout=json.dumps(volume_info),
                    stderr="",
                    command=tuple(command),
                    is_output_already_logged=False,
                )

        elif command[1] == "volume" and command[2] == "rm":
            # Successfully remove volume
            return FinishedProcess(
                returncode=0, stdout="", stderr="", command=tuple(command), is_output_already_logged=False
            )

        raise ValueError(f"Unexpected command: {command}")

    mock_concurrency_group.run_process_to_completion.side_effect = side_effect


@pytest.fixture
def mock_concurrency_group() -> Mock:
    """Create a mock ConcurrencyGroup for testing."""
    return Mock()


@pytest.fixture
def garbage_collector(mock_concurrency_group: Mock) -> ControlPlaneVolumeGarbageCollector:
    """Create a ControlPlaneVolumeGarbageCollector instance with a mock concurrency group."""
    return ControlPlaneVolumeGarbageCollector(
        latest_volume_name=LATEST_VOLUME_NAME, concurrency_group=mock_concurrency_group
    )


def test_volume_is_in_use_returns_true_when_containers_use_volume(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _volume_is_in_use returns True when containers are using the volume."""
    name = ControlPlaneVolumeInformation(
        is_dev_build=False,
        commit_hash="abcdef1",
        sha256=EXAMPLE_SHAS[1],
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(name=name, in_use=True),
        ],
    )

    result = garbage_collector._volume_is_in_use(name)

    assert result is True


def test_volume_is_in_use_returns_false_when_no_containers_use_volume(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _volume_is_in_use returns False when no containers are using the volume."""
    name = ControlPlaneVolumeInformation(
        is_dev_build=False,
        commit_hash="abcdef2",
        sha256=EXAMPLE_SHAS[2],
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(name=name, in_use=False),
        ],
    )

    result = garbage_collector._volume_is_in_use(name)
    assert result is False


def test_get_volume_creation_date_returns_datetime(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _get_volume_creation_date returns a datetime for a valid timestamp."""
    created_at = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
    name = ControlPlaneVolumeInformation(
        is_dev_build=False,
        commit_hash="abcdef3",
        sha256=EXAMPLE_SHAS[3],
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(
                name=name,
                created_at=created_at,
            ),
        ],
    )

    result = garbage_collector._get_volume_creation_date(name)

    assert result == created_at


def test_should_prune_volume_returns_false_for_latest_volume(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _should_prune_volume returns False for the latest volume."""
    result = garbage_collector._should_prune_prod_volume(LATEST_VOLUME_NAME)

    assert result is False
    # Should not make any docker calls for the latest volume
    mock_concurrency_group.run_process_to_completion.assert_not_called()


def test_should_prune_volume_returns_false_when_volume_is_in_use(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _should_prune_volume returns False when volume is in use."""
    name = ControlPlaneVolumeInformation(
        is_dev_build=False,
        commit_hash="abcdef4",
        sha256=EXAMPLE_SHAS[4],
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(
                name=name,
                in_use=True,
            ),
        ],
    )

    result = garbage_collector._should_prune_prod_volume(name)

    assert result is False


def test_should_prune_volume_returns_false_when_volume_is_too_recent(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _should_prune_volume returns False when volume was created less than 7 days ago."""
    # Volume created 3 days ago (too recent)
    created_at = datetime.now(timezone.utc) - timedelta(days=3)
    name = ControlPlaneVolumeInformation(
        is_dev_build=False,
        commit_hash="abcdef5",
        sha256=EXAMPLE_SHAS[5],
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(
                name=name,
                created_at=created_at,
                in_use=False,
            ),
        ],
    )

    result = garbage_collector._should_prune_prod_volume(name)

    assert result is False


def test_should_prune_volume_returns_true_when_volume_meets_all_criteria(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _should_prune_volume returns True when volume meets all pruning criteria."""
    # Volume created 10 days ago (old enough to prune)
    created_at = datetime.now(timezone.utc) - timedelta(days=10)
    name = ControlPlaneVolumeInformation(
        is_dev_build=False,
        commit_hash="abcdef6",
        sha256=EXAMPLE_SHAS[6],
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(
                name=name,
                created_at=created_at,
                in_use=False,
            ),
        ],
    )

    result = garbage_collector._should_prune_prod_volume(name)

    assert result is True


def test_get_volumes_to_prune_filters_control_plane_volumes(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _get_volumes_to_prune only considers volumes starting with imbue_control_plane_."""
    # All volumes are old enough and not in use
    created_at = datetime.now(timezone.utc) - timedelta(days=10)
    name1 = ControlPlaneVolumeInformation(
        is_dev_build=False, commit_hash="abcdef7", sha256=EXAMPLE_SHAS[7]
    ).as_volume_name()
    name2 = ControlPlaneVolumeInformation(
        is_dev_build=False, commit_hash="abcdef8", sha256=EXAMPLE_SHAS[8]
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(name=name1, created_at=created_at, in_use=False),
            MockVolume(name=name2, created_at=created_at, in_use=False),
            MockVolume(name="other_volume", created_at=created_at, in_use=False),
            MockVolume(name="random_volume_name", created_at=created_at, in_use=False),
        ],
    )

    result = garbage_collector._get_volumes_to_prune()

    # Should only return control plane volumes that meet pruning criteria
    assert name1 in result
    assert name2 in result
    assert "other_volume" not in result
    assert "random_volume_name" not in result


def test_get_volumes_to_prune_excludes_latest_volume(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _get_volumes_to_prune excludes the latest volume."""
    # Both volumes are old enough and not in use
    created_at = datetime.now(timezone.utc) - timedelta(days=10)
    name1 = ControlPlaneVolumeInformation(
        is_dev_build=False, commit_hash="abcdef9", sha256=EXAMPLE_SHAS[9]
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(name=name1, created_at=created_at, in_use=False),
            MockVolume(name=LATEST_VOLUME_NAME, created_at=created_at, in_use=False),  # latest volume
        ],
    )

    result = garbage_collector._get_volumes_to_prune()

    # Should include old version but not latest
    assert name1 in result
    assert LATEST_VOLUME_NAME not in result


def test_prune_volumes_does_nothing_when_list_is_empty(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _prune_volumes does nothing when given an empty list."""
    garbage_collector._prune_volumes([])

    mock_concurrency_group.run_process_to_completion.assert_not_called()


def test_prune_volumes_removes_all_volumes_in_list(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that _prune_volumes attempts to remove all volumes in the list."""
    name1 = ControlPlaneVolumeInformation(
        is_dev_build=False, commit_hash="abcdef9", sha256=EXAMPLE_SHAS[9]
    ).as_volume_name()
    name2 = ControlPlaneVolumeInformation(
        is_dev_build=False, commit_hash="abcdef10", sha256=EXAMPLE_SHAS[10]
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(name=name1),
            MockVolume(name=name2),
        ],
    )

    garbage_collector._prune_volumes([name1, name2])
    assert mock_concurrency_group.run_process_to_completion.call_count == 2
    mock_concurrency_group.run_process_to_completion.assert_any_call(
        command=["docker", "volume", "rm", name1], shutdown_event=None
    )
    mock_concurrency_group.run_process_to_completion.assert_any_call(
        command=["docker", "volume", "rm", name2], shutdown_event=None
    )


def test_prune_old_control_plane_volumes_completes_successfully(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that prune_old_control_plane_volumes completes the full workflow."""
    # Old volume is pruneable, latest is not
    created_at = datetime.now(timezone.utc) - timedelta(days=10)
    name1 = ControlPlaneVolumeInformation(
        is_dev_build=False, commit_hash="abcdef11", sha256=EXAMPLE_SHAS[11]
    ).as_volume_name()
    mock_docker_volumes_state(
        mock_concurrency_group,
        [
            MockVolume(name=name1, created_at=created_at, in_use=False),
            MockVolume(name=LATEST_VOLUME_NAME, created_at=created_at, in_use=False),  # latest
        ],
    )

    garbage_collector.prune_old_control_plane_volumes()

    # Should have called docker volume rm for the old volume
    mock_concurrency_group.run_process_to_completion.assert_any_call(
        command=["docker", "volume", "rm", name1], shutdown_event=None
    )


def test_prune_old_control_plane_volumes_dev_build(
    garbage_collector: ControlPlaneVolumeGarbageCollector, mock_concurrency_group: Mock
) -> None:
    """Test that dev build volumes are handled correctly.

    Critically there are often several volumes for a single commit hash, so we need to ensure
    that only the most recent one within the last 7 days is kept, and others are pruned.
    """

    now = datetime.now(timezone.utc)
    volumes = [
        # Multiple volumes for the same commit hash
        MockVolume(
            name=ControlPlaneVolumeInformation(
                is_dev_build=True, commit_hash="devcommit1", sha256=EXAMPLE_SHAS[12]
            ).as_volume_name(),
            created_at=now - timedelta(days=10),
            in_use=False,
        ),
        MockVolume(
            name=ControlPlaneVolumeInformation(
                is_dev_build=True, commit_hash="devcommit1", sha256=EXAMPLE_SHAS[13]
            ).as_volume_name(),
            created_at=now - timedelta(days=5),  # most recent within 7 days, should be kept
            in_use=False,
        ),
        MockVolume(
            name=ControlPlaneVolumeInformation(
                is_dev_build=True, commit_hash="devcommit1", sha256=EXAMPLE_SHAS[14]
            ).as_volume_name(),
            created_at=now - timedelta(days=8),
            in_use=False,
        ),
        # Another commit hash with only old volumes
        MockVolume(
            name=ControlPlaneVolumeInformation(
                is_dev_build=True, commit_hash="devcommit2", sha256=EXAMPLE_SHAS[15]
            ).as_volume_name(),
            created_at=now - timedelta(days=15),
            in_use=False,
        ),
        # Volume in use should be kept
        MockVolume(
            name=ControlPlaneVolumeInformation(
                is_dev_build=True, commit_hash="devcommit3", sha256=EXAMPLE_SHAS[16]
            ).as_volume_name(),
            created_at=now - timedelta(days=20),
            in_use=True,
        ),
    ]
    mock_docker_volumes_state(mock_concurrency_group, volumes)

    garbage_collector.prune_old_control_plane_volumes()

    # Should prune the two old volumes for devcommit1 and devcommit2
    pruned_volume_names = [
        ControlPlaneVolumeInformation(
            is_dev_build=True, commit_hash="devcommit1", sha256=EXAMPLE_SHAS[12]
        ).as_volume_name(),
        ControlPlaneVolumeInformation(
            is_dev_build=True, commit_hash="devcommit1", sha256=EXAMPLE_SHAS[14]
        ).as_volume_name(),
        ControlPlaneVolumeInformation(
            is_dev_build=True, commit_hash="devcommit2", sha256=EXAMPLE_SHAS[15]
        ).as_volume_name(),
    ]
    rm_calls = [
        call
        for call in mock_concurrency_group.run_process_to_completion.call_args_list
        if call.kwargs.get("command", [])[:3] == ["docker", "volume", "rm"]
    ]
    pruned_names_called = [call.kwargs["command"][3] for call in rm_calls]
    for pruned_name in pruned_volume_names:
        assert pruned_name in pruned_names_called
    assert len(pruned_names_called) == len(pruned_volume_names)
