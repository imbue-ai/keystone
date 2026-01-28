import json
from pathlib import Path
from typing import Sequence

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.amber import AmberSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches_test import expect_exact_logged_errors
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.progress_tracking.progress_tracking import RootProgressHandle
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import HelloAgentConfig
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.primitives.ids import RequestID
from sculptor.primitives.ids import UserReference
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_default_devcontainer_json_path,
)
from sculptor.tasks.handlers.run_agent.setup import environment_setup_context
from tests.acceptance.environment.conftest import DEVCONTAINER_NAMES
from tests.acceptance.environment.conftest import SAMPLE_DEVCONTAINERS_DIR
from tests.conftest import directory_containing_tarball_of_initial_commit_repo

assert directory_containing_tarball_of_initial_commit_repo, "Don't autoremove."


class PerTestAmberSnapshotExtension(AmberSnapshotExtension):
    """Each test gets its own .ambr file."""

    @classmethod
    def _get_file_basename(cls, *, test_location: PyTestLocation, index: SnapshotIndex) -> str:
        """Returns file basename without extension. Used to create full filepath."""
        return test_location.nodename

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Get the snapshot name for the assertion index in a test location"""
        index_suffix = ""
        if isinstance(index, (str,)):
            index_suffix = f"[{index}]"
        elif index:
            index_suffix = f".{index:03d}"
        return f"{test_location.snapshot_name}{index_suffix}"


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Each test in this file gets its own .ambr file."""
    return snapshot.use_extension(PerTestAmberSnapshotExtension)


def _create_task_for_devcontainer(
    test_project: Project,
    devcontainer_name: str,
    git_hash: str,
    test_service_collection: CompleteServiceCollection,
) -> Task:
    """Helper function to create a test task with the appropriate devcontainer image config."""
    # Get devcontainer.json path
    if devcontainer_name == "DEFAULT_DEVCONTAINER":
        devcontainer_json_path = get_default_devcontainer_json_path()
    else:
        devcontainer_json_path = SAMPLE_DEVCONTAINERS_DIR / devcontainer_name / "devcontainer.json"

    task = Task(
        object_id=TaskID(),
        organization_reference=test_project.organization_reference,
        user_reference=UserReference("test_user"),
        project_id=test_project.object_id,
        parent_task_id=None,
        input_data=AgentTaskInputsV1(
            agent_config=HelloAgentConfig(),
            image_config=LocalDevcontainerImageConfig(devcontainer_json_path=str(devcontainer_json_path)),
            environment_config=LocalDockerEnvironmentConfig(),
            git_hash=git_hash,
            initial_branch="main",
            is_git_state_clean=True,
        ),
    )
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        test_service_collection.task_service.create_task(task, transaction)
    return task


@pytest.mark.parametrize("devcontainer_name", DEVCONTAINER_NAMES)
def test_environment(
    devcontainer_name: str,
    initial_commit_repo: tuple[Path, str],
    snapshot: SnapshotAssertion,
    test_root_concurrency_group: ConcurrencyGroup,
    test_project: Project,
    test_service_collection: CompleteServiceCollection,
) -> None:
    """Test that the sample devcontainer environment passes its check script."""
    repo_path, git_hash = initial_commit_repo
    del repo_path

    # Create a task for this specific devcontainer
    test_task = _create_task_for_devcontainer(
        test_project=test_project,
        devcontainer_name=devcontainer_name,
        git_hash=git_hash,
        test_service_collection=test_service_collection,
    )

    # Get task data from the task
    task_data = test_task.input_data
    assert isinstance(task_data, AgentTaskInputsV1)
    task_state = AgentTaskStateV1()

    # Makes pytype happy.
    assert isinstance(test_service_collection, CompleteServiceCollection)
    services = test_service_collection

    # Use the real environment_setup_context
    with environment_setup_context(
        project=test_project,
        task=test_task,
        task_data=task_data,
        task_state=task_state,
        services=services,
        secrets={},
        concurrency_group=test_root_concurrency_group,
        root_progress_handle=RootProgressHandle(),
        shutdown_event=test_root_concurrency_group.shutdown_event,
    ) as (docker_environment, updated_task_state):
        assert isinstance(docker_environment, DockerEnvironment)
        assert isinstance(updated_task_state, AgentTaskStateV1)
        assert docker_environment.is_alive()
        assert docker_environment.read_file("/imbue_addons/sculptor_task_id.txt") == str(test_task.object_id)

        # Check that it behaves like the user expects it to behave.
        devcontainer_dir_path = SAMPLE_DEVCONTAINERS_DIR / devcontainer_name
        check_environment_path = devcontainer_dir_path / "check_environment.sh"
        docker_environment.write_file(
            "/check_environment.sh", check_environment_path.read_text("utf-8"), run_as_root=True
        )
        _run_checked_command(
            docker_environment, ["/imbue_addons/bash_with_user_env.sh", "/check_environment.sh"], snapshot
        )
        # Check that it behaves like Imbue expects it to behave.
        _check_control_plane_functionality(docker_environment, snapshot)


def _check_control_plane_functionality(
    docker_environment: DockerEnvironment,
    snapshot: SnapshotAssertion,
) -> None:
    """Tests functionality from the Imbue control plane that should work in any environment."""
    assert docker_environment.is_alive()
    path_check = _run_checked_command(docker_environment, ["bash", "-c", "echo $PATH"], snapshot)
    assert "/imbue_addons/agent_path_extension_bin:/imbue/bin:/imbue/nix_bin" in path_check["stdout"]

    _run_checked_command(
        docker_environment,
        [*("bash", "-c"), "echo $_IMBUE_USER_ORIGINAL_PATH && echo $LD_LIBRARY_PATH && echo $SSL_CERT_FILE"],
        snapshot,
    )
    _run_checked_command(
        docker_environment,
        [
            *("bash", "-c"),
            "which bash && "
            + "which claude && "
            + "which imbue-cli.sh && "
            + "which git && "
            + "which git-receive-pack && "
            + "which tmux && "
            + "which mutagen && "
            + "which nginx && "
            + "which rg && "
            + "which sed && "
            + "which xargs",
        ],
        snapshot,
    )

    # Check that when we ssh in as the sculptoruser, we have the control plane PATH.
    _run_checked_command(
        docker_environment,
        [
            *("/bin/su", "-", docker_environment.get_container_user(), "-c"),
            "which bash && "
            + "which claude && "
            + "which imbue-cli.sh && "
            + "which git && "
            + "which git-receive-pack && "
            + "which tmux && "
            + "which mutagen && "
            + "which nginx && "
            + "which rg && "
            + "which sed && "
            + "which xargs",
        ],
        snapshot,
    )

    _run_checked_command(
        docker_environment,
        [*("bash", "-c"), "bash --version && claude --version && git --version && mutagen --version"],
        snapshot,
    )

    # Codex typically executes commands through /bin/sh -lc, so ensure sed runs in that context too.
    sed_process = docker_environment.run_process_in_background(
        ["/bin/sh", "-lc", "sed --version"],
        secrets={},
        run_as_root=True,
    )
    assert sed_process.wait() == 0

    _run_checked_command(docker_environment, ["imbue-cli.sh", "--help"], snapshot)

    control_plane_version = json.loads(docker_environment.read_file("/imbue/version.json"))
    assert control_plane_version.keys() == snapshot
    assert "unknown" not in str(control_plane_version).lower()

    addons_version = json.loads(docker_environment.read_file("/imbue_addons/version.json"))
    assert addons_version.keys() == snapshot
    assert "unknown" not in str(addons_version).lower()

    # Check whoami when running as non-root (as the container user)
    _run_checked_command(docker_environment, ["whoami"], snapshot, run_as_root=False)

    # We should have already ran mutagen and git + ssh to push the code into the container.
    _run_checked_command(docker_environment, ["ls", "/code"], snapshot)


def _scrub_platform(text: str) -> str:
    """Scrub platform-specific markers from the text.

    Continue adding to the list as we discover more platform-specific markers.
    """
    platform_markers = (
        "aarch64-unknown",
        "x86_64-pc",
        "arm64",
        "amd64",
    )
    for marker in platform_markers:
        text = text.replace(marker, "<PLATFORM>")
    return text


def _run_checked_command(
    docker_environment: DockerEnvironment,
    command: Sequence[str],
    snapshot: SnapshotAssertion,
    run_as_root: bool = True,
) -> dict[str, str]:
    process = docker_environment.run_process_in_background(command, secrets={}, run_as_root=run_as_root)
    exit_code = process.wait()
    stdout = process.read_stdout()
    stderr = process.read_stderr()
    check = {
        "command": command,
        "stdout": _scrub_platform(stdout),
        "stderr": _scrub_platform(stderr),
        "exit_code": exit_code,
    }
    assert check == snapshot
    assert exit_code == 0, f"Command failed with exit code {exit_code}. stdout={stdout}, stderr={stderr}"
    return check


@pytest.mark.parametrize(
    "error_devcontainer_name",
    [
        "error_devcontainer_invalid_json",
        "error_devcontainer_valid_but_empty_json",
        "error_devcontainer_points_to_missing_dockerfile",
        "error_dockerfile_invalid_syntax",
        "error_dockerfile_build_will_fail",
    ],
)
def test_error_dockerfile_build_fails(
    error_devcontainer_name: str,
    initial_commit_repo: tuple[Path, str],
    test_root_concurrency_group: ConcurrencyGroup,
    test_project: Project,
    test_service_collection: CompleteServiceCollection,
) -> None:
    """Test that devcontainers with build failures fall back to default devcontainer."""
    repo_path, git_hash = initial_commit_repo
    del repo_path

    # Create a task for this specific error devcontainer
    test_task = _create_task_for_devcontainer(
        test_project=test_project,
        devcontainer_name=error_devcontainer_name,
        git_hash=git_hash,
        test_service_collection=test_service_collection,
    )

    # Get task data from the task
    task_data = test_task.input_data
    assert isinstance(task_data, AgentTaskInputsV1)
    task_state = AgentTaskStateV1()

    # Makes pytype happy.
    assert isinstance(test_service_collection, CompleteServiceCollection)
    services = test_service_collection

    # Use the real environment_setup_context
    with expect_exact_logged_errors(["Failed to build user Dockerfile, falling back to default devcontainer image"]):
        with environment_setup_context(
            project=test_project,
            task=test_task,
            task_data=task_data,
            task_state=task_state,
            services=services,
            secrets={},
            concurrency_group=test_root_concurrency_group,
            root_progress_handle=RootProgressHandle(),
            shutdown_event=test_root_concurrency_group.shutdown_event,
        ) as (environment, updated_task_state):
            assert environment.is_alive()


@pytest.mark.parametrize(
    "error_devcontainer_name",
    ["error_devcontainer_bad_image_reference", "error_dockerfile_creates_nix_directory"],
)
def test_error_wrapper_image_build_fails(
    error_devcontainer_name: str,
    initial_commit_repo: tuple[Path, str],
    test_root_concurrency_group: ConcurrencyGroup,
    test_project: Project,
    test_service_collection: CompleteServiceCollection,
) -> None:
    """Test that devcontainers with wrapper build failures are handled correctly."""
    repo_path, git_hash = initial_commit_repo
    del repo_path

    # Create a task for this specific error devcontainer
    test_task = _create_task_for_devcontainer(
        test_project=test_project,
        devcontainer_name=error_devcontainer_name,
        git_hash=git_hash,
        test_service_collection=test_service_collection,
    )

    # Get task data from the task
    task_data = test_task.input_data
    assert isinstance(task_data, AgentTaskInputsV1)
    task_state = AgentTaskStateV1()

    # Makes pytype happy.
    assert isinstance(test_service_collection, CompleteServiceCollection)
    services = test_service_collection

    # Use the real environment_setup_context
    with expect_exact_logged_errors(["Failed to build Imbue's wrapper around user_image_tag="]):
        with environment_setup_context(
            project=test_project,
            task=test_task,
            task_data=task_data,
            task_state=task_state,
            services=services,
            secrets={},
            concurrency_group=test_root_concurrency_group,
            root_progress_handle=RootProgressHandle(),
            shutdown_event=test_root_concurrency_group.shutdown_event,
        ) as (environment, updated_task_state):
            assert environment.is_alive()
