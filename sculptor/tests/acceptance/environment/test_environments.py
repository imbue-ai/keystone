import os
import tempfile
from pathlib import Path
from queue import Empty
from typing import Generator
from typing import cast

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.common import generate_id
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.git import get_git_repo_root
from imbue_core.processes.local_process import RunningProcess
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.interfaces.environments.base import LocalImage
from sculptor.interfaces.environments.base import ModalEnvironmentConfig
from sculptor.interfaces.environments.base import ModalImage
from sculptor.interfaces.environments.constants import CONTAINER_SSH_PORT
from sculptor.interfaces.environments.constants import ENVIRONMENT_WORKSPACE_DIRECTORY
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.environments.modal_environment import ModalEnvironment
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_devcontainer_json_path_from_repo_or_default,
)
from sculptor.services.environment_service.providers.docker.docker_provider import DockerProvider
from sculptor.services.environment_service.providers.docker.docker_provider import build_local_devcontainer_image
from sculptor.services.environment_service.providers.docker.environment_utils import build_docker_environment
from sculptor.services.environment_service.providers.local.environment_utils import build_local_environment
from sculptor.services.environment_service.providers.local.image_utils import build_local_image
from sculptor.services.environment_service.providers.modal.environment_utils import build_modal_environment
from sculptor.services.environment_service.providers.modal.image_utils import build_modal_image
from sculptor.testing.server_utils import TEST_ENVIRONMENT_PREFIX
from tests.conftest import directory_containing_tarball_of_initial_commit_repo

TEST_DATA_DIR = get_git_repo_root() / "sculptor" / "tests" / "acceptance" / "environment" / "test_data"

assert directory_containing_tarball_of_initial_commit_repo, "Don't autoremove."


@pytest.fixture
def project_id_for_custom_docker_image() -> ProjectID:
    return ProjectID()


@pytest.fixture
def custom_docker_image(
    project_id_for_custom_docker_image: ProjectID,
    directory_containing_tarball_of_initial_commit_repo: Path,
    test_root_concurrency_group: ConcurrencyGroup,
    tmp_path: Path,
) -> LocalDockerImage:
    tmp_devcontainer_json_path = tmp_path / "devcontainer.json"
    tmp_devcontainer_json_path.write_text(
        f"""\
{{
    "name": "Test Devcontainer",
    "build": {{
        "dockerfile": "{(TEST_DATA_DIR / "Dockerfile.test.docker").as_posix()}"
    }}
}}
"""
    )
    config = LocalDevcontainerImageConfig(devcontainer_json_path=tmp_devcontainer_json_path.as_posix())
    image_repo = f"{TEST_ENVIRONMENT_PREFIX}-{generate_id()}"
    return build_local_devcontainer_image(
        config=config,
        cached_repo_tarball_parent_directory=directory_containing_tarball_of_initial_commit_repo,
        project_id=project_id_for_custom_docker_image,
        image_repo=image_repo,
        concurrency_group=test_root_concurrency_group,
        image_metadata=ImageMetadataV1.from_testing(),
    )


@pytest.fixture
def docker_sandbox_config() -> LocalDockerEnvironmentConfig:
    return LocalDockerEnvironmentConfig()


@pytest.fixture
def docker_environment(
    custom_docker_image: LocalDockerImage,
    docker_sandbox_config: LocalDockerEnvironmentConfig,
    test_root_concurrency_group: ConcurrencyGroup,
) -> Generator[DockerEnvironment, None, None]:
    environment = None
    try:
        environment, _create_command = build_docker_environment(
            custom_docker_image, name=None, config=docker_sandbox_config, concurrency_group=test_root_concurrency_group
        )
        yield environment
    finally:
        if environment is not None:
            environment.close()


@pytest.fixture
def local_image(project_id_for_custom_docker_image: ProjectID) -> LocalImage:
    code_directory = Path(__file__).parent / "test_data"
    return build_local_image(code_directory, project_id_for_custom_docker_image)


@pytest.fixture
def local_environment(
    local_image: LocalImage,
    test_root_concurrency_group: ConcurrencyGroup,
) -> Generator[LocalEnvironment, None, None]:
    environment = None
    try:
        environment = build_local_environment(local_image, LocalEnvironmentConfig(), test_root_concurrency_group)
        yield environment
    finally:
        if environment is not None:
            environment.close()


@pytest.fixture
def test_modal_app_name() -> str:
    return f"sculptor-test-sandbox-{generate_id()}"


@pytest.fixture
def modal_image(
    test_modal_app_name: str, project_id_for_custom_docker_image: ProjectID
) -> Generator[ModalImage, None, None]:
    with tempfile.NamedTemporaryFile() as temp_file:
        dockerfile_content = f"""
# start with base image
FROM ubuntu:20.04

# install system dependencies
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git tmux

WORKDIR {ENVIRONMENT_WORKSPACE_DIRECTORY}

COPY {TEST_DATA_DIR} {ENVIRONMENT_WORKSPACE_DIRECTORY}
    """
        temp_file.write(dockerfile_content.encode())
        temp_file.flush()
        relative_file_path = str(temp_file.name)
        image = build_modal_image(relative_file_path, test_modal_app_name, project_id_for_custom_docker_image)
        yield image


@pytest.fixture
def modal_environment(
    modal_image: ModalImage,
) -> Generator[ModalEnvironment, None, None]:
    environment = None
    try:
        environment = build_modal_environment(
            modal_image,
            config=ModalEnvironmentConfig(unencrypted_ports=[CONTAINER_SSH_PORT]),
            project_id=modal_image.project_id,
        )
        yield environment
    finally:
        if environment is not None:
            environment.close()


# TODO(sam): Add Modal back in once `_run_process_in_background` is implemented.
@pytest.fixture(params=["docker", "local"])
def environment(request: pytest.FixtureRequest) -> Environment:
    """Parameterized fixture that provides all environment types."""
    if request.param == "docker":
        return cast(DockerEnvironment, request.getfixturevalue("docker_environment"))
    elif request.param == "local":
        return cast(LocalEnvironment, request.getfixturevalue("local_environment"))
    elif request.param == "modal":
        return cast(ModalEnvironment, request.getfixturevalue("modal_environment"))
    elif request.param == "local_devcontainer":
        return request.getfixturevalue("local_devcontainer_environment")
    else:
        raise ValueError(f"Unknown environment type: {request.param}")


def test_is_alive(environment: Environment) -> None:
    """Test that environment is alive after creation."""
    assert environment.is_alive()


def test_write_and_read_file_text(environment: Environment) -> None:
    """Test writing and reading text files."""
    test_content = "Hello, Environment!"
    test_path = get_test_file_path(environment, "test_file.txt")

    environment.write_file(test_path, test_content, "w")

    read_content = environment.read_file(test_path, "r")
    assert isinstance(read_content, str)
    assert read_content.strip() == test_content


def test_write_and_read_file_binary(environment: Environment) -> None:
    """Test writing and reading binary files."""
    if isinstance(environment, DockerEnvironment):
        pytest.skip("DockerEnvironment does not support binary file operations correctly yet.")
    binary_content = b"Binary data \x00\x01\x02\xff"
    test_path = get_test_file_path(environment, "test_binary.bin")

    environment.write_file(test_path, binary_content, "wb")

    read_content = environment.read_file(test_path, "rb")
    assert isinstance(read_content, bytes)
    assert read_content == binary_content


def test_write_file_append_mode(environment: Environment) -> None:
    """Test writing files in append mode."""
    test_path = get_test_file_path(environment, "append_test.txt")

    environment.write_file(test_path, "First line\n", "w")
    environment.write_file(test_path, "Second line\n", "a")

    content = environment.read_file(test_path, "r")
    assert isinstance(content, str)
    assert "First line" in content
    assert "Second line" in content


def test_write_file_creates_parent_directories(environment: Environment) -> None:
    """Test that writing files creates parent directories."""
    test_path = get_test_file_path(environment, "deep/nested/directory/file.txt")
    test_content = "nested file content"

    environment.write_file(test_path, test_content, "w")

    content = environment.read_file(test_path, "r")
    assert isinstance(content, str)
    assert content.strip() == test_content


def test_read_nonexistent_file(environment: Environment) -> None:
    """Test that reading non-existent file raises appropriate error."""
    nonexistent_path = get_test_file_path(environment, "nonexistent/file.txt")

    with pytest.raises((FileNotFoundError, OSError)):
        environment.read_file(nonexistent_path, "r")


def test_run_process_in_background_simple_command(environment: Environment) -> None:
    """Test launching a simple process."""
    process = environment.run_process_in_background(["echo", "Hello from process"], {})

    assert isinstance(process, RunningProcess)

    exit_code = process.wait()
    assert exit_code == 0

    stdout = process.read_stdout()
    assert isinstance(stdout, str)
    assert "Hello from process" in stdout


def test_run_process_in_background_with_secrets(environment: Environment) -> None:
    """Test launching processes with environment variables/secrets."""
    secrets = {"TEST_VAR": "secret_value"}
    process = environment.run_process_in_background(["bash", "-c", "echo $TEST_VAR"], secrets)

    exit_code = process.wait()
    assert exit_code == 0

    stdout = process.read_stdout()
    assert isinstance(stdout, str)
    assert "secret_value" in stdout


def test_run_process_in_background_with_multiple_secrets(environment: Environment) -> None:
    """Test launching processes with multiple environment variables."""
    secrets = {"VAR1": "value1", "VAR2": "value2", "VAR3": "value3"}
    process = environment.run_process_in_background(["bash", "-c", "echo $VAR1-$VAR2-$VAR3"], secrets)

    exit_code = process.wait()
    assert exit_code == 0

    stdout = process.read_stdout()
    assert isinstance(stdout, str)
    assert "value1-value2-value3" in stdout


def test_process_stderr_output(environment: Environment) -> None:
    """Test capturing stderr output from a process."""
    process = environment.run_process_in_background(["bash", "-c", "echo 'Error message' >&2"], {})

    exit_code = process.wait()
    assert exit_code == 0

    stderr = process.read_stderr()
    assert isinstance(stderr, str)
    assert "Error message" in stderr


def test_process_exit_codes(environment: Environment) -> None:
    """Test that process exit codes are properly captured."""
    success_process = environment.run_process_in_background(["true"], {})
    exit_code = success_process.wait()
    assert exit_code == 0
    assert success_process.returncode == 0

    failure_process = environment.run_process_in_background(["false"], {})
    exit_code = failure_process.wait()
    assert exit_code == 1
    assert failure_process.returncode == 1


def test_process_poll_and_is_finished(environment: Environment) -> None:
    """Test process polling and finished status."""
    process = environment.run_process_in_background(["bash", "-c", "echo done"], {})

    exit_code = process.wait()
    assert exit_code == 0

    assert process.is_finished()

    result = process.poll()
    assert result == 0


def test_process_stream_stdout(environment: Environment) -> None:
    """Test streaming stdout from a process."""
    process = environment.run_process_in_background(["bash", "-c", "for i in {1..3}; do echo line$i; done"], {})

    queue = process.get_queue()
    streamed_lines = []
    while not process.is_finished() or not queue.empty():
        try:
            line, is_stdout = queue.get(timeout=0.1)
        except Empty:
            continue
        if not is_stdout:
            continue
        streamed_lines.append(line)
        if len(streamed_lines) >= 3:
            break

    # Now that streaming is guaranteed to be line-by-line, we can check directly
    assert len(streamed_lines) >= 3

    # Check that each expected line appears in the streamed output
    assert any("line1" in line for line in streamed_lines)
    assert any("line2" in line for line in streamed_lines)
    assert any("line3" in line for line in streamed_lines)


def test_process_stream_stderr(environment: Environment) -> None:
    """Test streaming stderr from a process."""
    process = environment.run_process_in_background(["bash", "-c", "for i in {1..3}; do echo error$i >&2; done"], {})
    queue = process.get_queue()
    streamed_lines = []
    # We expect these to come in fairly quickly, but it isn't safe to check for process completion in the loop condition.
    while len(streamed_lines) < 3:
        streamed_lines.append(queue.get(timeout=0.1)[0])
    process.wait()

    # Now that streaming is guaranteed to be line-by-line, we can check directly
    assert len(streamed_lines) >= 3

    # Check that each expected line appears in the streamed output
    assert any("error1" in line for line in streamed_lines)
    assert any("error2" in line for line in streamed_lines)
    assert any("error3" in line for line in streamed_lines)


def test_process_terminate(environment: Environment) -> None:
    """Test terminating a long-running process."""
    process = environment.run_process_in_background(["sleep", "5"], {})

    assert not process.is_finished()
    assert process.poll() is None

    process.terminate()

    assert process.is_finished()


def get_test_file_path(environment: Environment, relative_path: str) -> str:
    """Get appropriate file path for the environment type."""
    if isinstance(environment, DockerEnvironment):
        return f"/tmp/{relative_path}"
    elif isinstance(environment, LocalEnvironment):
        return relative_path
    elif isinstance(environment, ModalEnvironment):
        return f"/tmp/{relative_path}"
    else:
        raise ValueError(f"Unknown environment type: {type(environment)}")


@pytest.mark.skip(reason="test will fail in CI because the tests are run in docker themselves")
def test_docker_with_bind_mounts(
    custom_docker_image: LocalDockerImage, test_root_concurrency_group: ConcurrencyGroup
) -> None:
    config = LocalDockerEnvironmentConfig()
    environment: Environment | None = None
    try:
        environment, _create_command = build_docker_environment(
            custom_docker_image, name=None, config=config, concurrency_group=test_root_concurrency_group
        )
        assert environment.is_alive()
        file_contents = environment.read_file(ENVIRONMENT_WORKSPACE_DIRECTORY / "fake_file.py", "r")
        assert (
            file_contents
            == """if __name__ == "__main__":
    print("Hello, world!")\n"""
        )
    finally:
        if environment is not None:
            environment.close()


@pytest.mark.skip(reason="`_run_process_in_background` not implemented for ModalEnvironment yet")
def test_modal_duplicate_process_termination(
    modal_environment: ModalEnvironment,
) -> None:
    """Special test for modal environment to ensure that processes are terminated correctly."""
    first_process = modal_environment.create_process(["sleep", "10"], {})
    second_process = modal_environment.create_process(["sleep", "10"], {})

    assert not first_process.is_finished()
    assert first_process.poll() is None

    assert not second_process.is_finished()
    assert second_process.poll() is None

    first_process.terminate()
    assert first_process.is_finished()
    assert not second_process.is_finished()

    second_process.terminate()
    assert second_process.is_finished()


def test_docker_duplicate_process_termination(
    docker_environment: DockerEnvironment,
) -> None:
    """Special test for docker environment to ensure that processes are terminated correctly."""
    first_process = docker_environment.run_process_in_background(["sleep", "60"], {})
    second_process = docker_environment.run_process_in_background(["sleep", "60"], {})

    assert not first_process.is_finished()
    assert first_process.poll() is None

    assert not second_process.is_finished()
    assert second_process.poll() is None

    first_process.terminate()
    assert first_process.is_finished()
    assert not second_process.is_finished()

    second_process.terminate()
    assert second_process.is_finished()


def test_copy_from_local_file(docker_environment: DockerEnvironment) -> None:
    """Test copying a single file from local filesystem to environment."""
    # Create a temporary local file
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as temp_file:
        temp_file.write("Hello from local file!")
        temp_file_path = Path(temp_file.name)

    try:
        # Copy file to environment
        env_path = get_test_file_path(docker_environment, "copied_file.txt")
        docker_environment.copy_from_local(temp_file_path, env_path, recursive=False)

        # Verify file exists and has correct content
        content = docker_environment.read_file(env_path, "r")
        assert content.strip() == "Hello from local file!"
    finally:
        # Clean up local temp file
        temp_file_path.unlink()


def test_copy_from_local_directory(docker_environment: DockerEnvironment) -> None:
    """Test copying a directory from local filesystem to environment."""
    # Create a temporary directory with some files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create some test files
        (temp_path / "file1.txt").write_text("Content of file 1")
        (temp_path / "file2.txt").write_text("Content of file 2")

        # Create a subdirectory with a file
        subdir = temp_path / "subdir"
        subdir.mkdir()
        (subdir / "file3.txt").write_text("Content of file 3")

        # Copy directory to environment
        env_path = get_test_file_path(docker_environment, "copied_dir")
        docker_environment.copy_from_local(temp_path, env_path, recursive=True)

        # Verify files exist and have correct content
        content1 = docker_environment.read_file(f"{env_path}/file1.txt", "r")
        assert content1.strip() == "Content of file 1"

        content2 = docker_environment.read_file(f"{env_path}/file2.txt", "r")
        assert content2.strip() == "Content of file 2"

        content3 = docker_environment.read_file(f"{env_path}/subdir/file3.txt", "r")
        assert content3.strip() == "Content of file 3"


def test_copy_from_local_directory_not_recursive(
    docker_environment: DockerEnvironment,
) -> None:
    """Test that copying a directory with recursive=False raises an error."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        env_path = get_test_file_path(docker_environment, "should_not_exist")

        with pytest.raises(IsADirectoryError):
            docker_environment.copy_from_local(temp_path, env_path, recursive=False)


def test_copy_to_local_file(docker_environment: DockerEnvironment) -> None:
    """Test copying a single file from environment to local filesystem."""
    # Create a file in the environment
    env_path = get_test_file_path(docker_environment, "source_file.txt")
    docker_environment.write_file(env_path, "Hello from environment!", "w")

    # Copy file to local filesystem
    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = Path(temp_dir) / "copied_file.txt"
        docker_environment.copy_to_local(env_path, local_path, recursive=False)

        # Verify file exists and has correct content
        assert local_path.exists()
        content = local_path.read_text()
        assert content.strip() == "Hello from environment!"


def test_copy_to_local_directory(docker_environment: DockerEnvironment) -> None:
    """Test copying a directory from environment to local filesystem."""
    # Create a directory structure in the environment
    base_path = get_test_file_path(docker_environment, "source_dir")
    docker_environment.write_file(f"{base_path}/file1.txt", "Content of file 1", "w")
    docker_environment.write_file(f"{base_path}/file2.txt", "Content of file 2", "w")
    docker_environment.write_file(f"{base_path}/subdir/file3.txt", "Content of file 3", "w")

    # Copy directory to local filesystem
    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = Path(temp_dir) / "copied_dir"
        docker_environment.copy_to_local(base_path, local_path, recursive=True)

        # Verify directory structure and contents
        assert local_path.exists()
        assert (local_path / "file1.txt").exists()
        assert (local_path / "file2.txt").exists()
        assert (local_path / "subdir" / "file3.txt").exists()

        content1 = (local_path / "file1.txt").read_text()
        assert content1.strip() == "Content of file 1"

        content2 = (local_path / "file2.txt").read_text()
        assert content2.strip() == "Content of file 2"

        content3 = (local_path / "subdir" / "file3.txt").read_text()
        assert content3.strip() == "Content of file 3"


def test_copy_to_local_directory_not_recursive(
    docker_environment: DockerEnvironment,
) -> None:
    """Test that copying a directory with recursive=False raises an error."""
    # Create a directory in the environment
    dir_path = get_test_file_path(docker_environment, "source_dir_no_recursive")
    docker_environment.write_file(f"{dir_path}/file.txt", "content", "w")

    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = Path(temp_dir) / "should_not_exist"

        with pytest.raises(IsADirectoryError):
            docker_environment.copy_to_local(dir_path, local_path, recursive=False)


def test_copy_from_local_nonexistent_path(
    docker_environment: DockerEnvironment,
) -> None:
    """Test that copying from a non-existent local path raises an error."""
    nonexistent_path = Path("/tmp/this_does_not_exist_12345.txt")
    env_path = get_test_file_path(docker_environment, "destination.txt")

    with pytest.raises(FileNotFoundError):
        docker_environment.copy_from_local(nonexistent_path, env_path)


def test_copy_to_local_nonexistent_path(docker_environment: DockerEnvironment) -> None:
    """Test that copying from a non-existent environment path raises an error."""
    nonexistent_path = get_test_file_path(docker_environment, "this_does_not_exist_12345.txt")

    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = Path(temp_dir) / "destination.txt"

        with pytest.raises(FileNotFoundError):
            docker_environment.copy_to_local(nonexistent_path, local_path)


@pytest.fixture
def agent_docker_image(
    initial_commit_repo: tuple[Path, str],
    directory_containing_tarball_of_initial_commit_repo: Path,
    test_root_concurrency_group: ConcurrencyGroup,
) -> LocalDockerImage:
    devcontainer_json_path = get_devcontainer_json_path_from_repo_or_default(repo_path=initial_commit_repo[0])
    config = LocalDevcontainerImageConfig(devcontainer_json_path=str(devcontainer_json_path))
    docker_provider = DockerProvider(concurrency_group=test_root_concurrency_group)
    image = docker_provider.create_image(
        config=config,
        secrets={},
        cached_repo_tarball_parent_directory=directory_containing_tarball_of_initial_commit_repo,
        environment_prefix=f"{TEST_ENVIRONMENT_PREFIX}-{generate_id()}",
        project_id=ProjectID(),
        image_metadata=ImageMetadataV1.from_testing(),
    )
    # TODO: Assert that the image.image_id is tagged so that it's not garbage collected.
    return image


@pytest.fixture
def agent_docker_environment(
    agent_docker_image: LocalDockerImage,
    docker_sandbox_config: LocalDockerEnvironmentConfig,
    test_root_concurrency_group: ConcurrencyGroup,
) -> Generator[DockerEnvironment, None, None]:
    environment = None
    try:
        environment, _create_command = build_docker_environment(
            agent_docker_image, name=None, config=docker_sandbox_config, concurrency_group=test_root_concurrency_group
        )
        yield environment
    finally:
        if environment is not None:
            environment.close()


def test_agent_docker_environment_runs_imbue_cli(agent_docker_environment: DockerEnvironment) -> None:
    """Test that the agent docker environment is working."""
    assert agent_docker_environment.is_alive()
    process = agent_docker_environment.run_process_in_background(["/imbue/bin/imbue-cli.sh", "--help"], secrets={})
    assert process.wait() == 0, f"{process.read_stdout()=}, {process.read_stderr()=}"


def test_agent_docker_environment_runs_claude(agent_docker_environment: DockerEnvironment) -> None:
    """Test that the agent docker environment is working."""
    assert agent_docker_environment.is_alive()
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    assert anthropic_api_key, "ANTHROPIC_API_KEY is not set."
    process = agent_docker_environment.run_process_in_background(
        ["bash", "-c", "uname -a | claude -p explain"],
        secrets={"ANTHROPIC_API_KEY": anthropic_api_key},
    )
    assert process.wait() == 0, f"{process.read_stdout()=}, {process.read_stderr()=}"
    assert "Linux" in process.read_stdout(), (
        f"Should at least mention Linux, {process.read_stdout()=}, {process.read_stderr()=}"
    )


def test_agent_docker_environment_runs_post_container_build(agent_docker_environment: DockerEnvironment) -> None:
    """Test that the agent docker environment is working."""
    assert agent_docker_environment.is_alive()
    process = agent_docker_environment.run_process_in_background(["ls", "/root/.tmux.conf"], secrets={})
    assert process.wait() == 0, f"{process.read_stdout()=}, {process.read_stderr()=}"
