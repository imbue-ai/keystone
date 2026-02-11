"""Agent runner abstraction for local and Modal execution."""

import io
import shlex
import shutil
import subprocess
import tarfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import Literal

from bootstrap_devcontainer.agent_log import create_devcontainer_tarball
from bootstrap_devcontainer.process_runner import run_process
from bootstrap_devcontainer.schema import VerifyResult

logger = getLogger(__name__)

DEFAULT_AGENT_TIMEOUT = 3600
TIMEOUT_EXIT_CODE = 124  # Exit code used by GNU timeout command


@dataclass
class StreamEvent:
    """A line of output from the agent process."""

    stream: Literal["stdout", "stderr"]
    line: str


def build_claude_command(
    prompt: str, max_budget_usd: float, agent_cmd: str = "claude"
) -> list[str]:
    """Build the command to run the Claude agent.

    Uses the splat pattern for grouping arguments for better readability.
    """
    return [
        *shlex.split(agent_cmd),
        "--dangerously-skip-permissions",
        *("--output-format", "stream-json"),
        "--verbose",
        *("--max-budget-usd", str(max_budget_usd)),
        *("-p", prompt),
    ]


class AgentRunner(ABC):
    """Abstract base class for running the bootstrap agent."""

    @abstractmethod
    def run(
        self,
        prompt: str,
        project_archive: bytes,
        max_budget_usd: float,
        agent_cmd: str,
        time_limit_secs: int,
    ) -> Iterator[StreamEvent]:
        """Run the agent and yield output events.

        Args:
            prompt: The prompt to send to the agent.
            project_archive: Git archive tarball of the project.
            max_budget_usd: Maximum budget for agent inference.
            agent_cmd: Base command to run the agent (e.g., "claude").
            time_limit_secs: Maximum time in seconds for agent execution.

        Yields:
            StreamEvent for each line of stdout/stderr.
        """
        ...

    @property
    @abstractmethod
    def exit_code(self) -> int:
        """Return code from the agent process. Available after run() completes."""
        ...

    @abstractmethod
    def get_devcontainer_tarball(self) -> bytes:
        """Get tarball of .devcontainer/ directory for caching."""
        ...

    @abstractmethod
    def verify(
        self,
        project_archive: bytes,
        devcontainer_tarball: bytes,
        test_artifacts_dir: Path,
        image_build_timeout_secs: int,
        test_timeout_secs: int,
    ) -> VerifyResult:
        """Run verification tests on pristine source + agent's devcontainer.

        Args:
            project_archive: Git archive tarball of the original project source.
            devcontainer_tarball: Tarball of .devcontainer/ created by agent.
            test_artifacts_dir: Directory to store test artifacts.
            image_build_timeout_secs: Timeout for building the devcontainer image.
            test_timeout_secs: Timeout for running tests.

        Returns:
            VerifyResult with success status and optional error message.
        """
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Perform any necessary cleanup (e.g. terminating sandboxes)."""
        ...

    def get_claude_dir_tarball(self) -> bytes | None:
        """Get tarball of ~/.claude directory if available.

        This is optional - only Modal runner implements it since it has access
        to the sandbox filesystem. Local runner returns None.

        Returns:
            Gzipped tarball of ~/.claude, or None if not available.
        """
        return None


class LocalAgentRunner(AgentRunner):
    """Run agent locally using subprocess.

    The agent runs in a clean directory extracted from git archive, ensuring
    repeatability and better Docker build caching.
    """

    def __init__(self) -> None:
        self._exit_code: int = 1
        self._work_dir: Path | None = None  # Temp dir from git archive

    def _check_docker_available(self) -> bool:
        """Check if Docker is available locally."""
        try:
            result = subprocess.run(
                ["docker", "ps"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def run(
        self,
        prompt: str,
        project_archive: bytes,
        max_budget_usd: float,
        agent_cmd: str,
        time_limit_secs: int,
    ) -> Iterator[StreamEvent]:
        if not self._check_docker_available():
            yield StreamEvent(
                stream="stderr",
                line="Error: Docker is required for local agent execution but not available.",
            )
            self._exit_code = 1
            return

        # Extract archive to temp directory
        yield StreamEvent(
            stream="stderr",
            line="Extracting project archive to working directory...",
        )
        import tempfile

        self._work_dir = Path(tempfile.mkdtemp(prefix="bootstrap-agent-"))
        with tarfile.open(fileobj=io.BytesIO(project_archive), mode="r:gz") as tar:
            tar.extractall(self._work_dir)

        events: list[StreamEvent] = []

        def collect_stdout(line: str) -> None:
            events.append(StreamEvent(stream="stdout", line=line))

        def collect_stderr(line: str) -> None:
            events.append(StreamEvent(stream="stderr", line=line))

        full_cmd = build_claude_command(prompt, max_budget_usd, agent_cmd)

        # Add timeout if available
        try:
            subprocess.run(["timeout", "--version"], capture_output=True)
            full_cmd = ["timeout", str(time_limit_secs), *full_cmd]
        except FileNotFoundError:
            pass

        result = run_process(
            full_cmd,
            cwd=str(self._work_dir),
            stdout_callback=collect_stdout,
            stderr_callback=collect_stderr,
        )

        self._exit_code = result.returncode

        # Yield all collected events
        yield from events

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def get_devcontainer_tarball(self) -> bytes:
        if self._work_dir is None:
            raise RuntimeError("run() must be called before get_devcontainer_tarball()")
        return create_devcontainer_tarball(self._work_dir)

    def verify(
        self,
        project_archive: bytes,
        devcontainer_tarball: bytes,
        test_artifacts_dir: Path,
        image_build_timeout_secs: int,
        test_timeout_secs: int,
    ) -> VerifyResult:
        """Run verification tests locally using Docker.

        Extracts pristine project source + agent's devcontainer to a temp dir,
        then builds and runs tests.
        """
        import tempfile

        if not self._check_docker_available():
            return VerifyResult(
                success=False,
                error_message="Docker is required for local verification but not available.",
            )

        # Extract to fresh temp directory
        work_dir = Path(tempfile.mkdtemp(prefix="bootstrap-verify-"))
        try:
            # Extract project archive
            with tarfile.open(fileobj=io.BytesIO(project_archive), mode="r:gz") as tar:
                tar.extractall(work_dir)

            # Overlay devcontainer
            with tarfile.open(fileobj=io.BytesIO(devcontainer_tarball), mode="r:gz") as tar:
                tar.extractall(work_dir)

            # Check if devcontainer.json exists
            devcontainer_json = work_dir / ".devcontainer" / "devcontainer.json"
            if not devcontainer_json.exists():
                return VerifyResult(
                    success=False,
                    error_message="Build failed: .devcontainer/devcontainer.json not found.",
                )

            import time

            image_name = "bootstrap-verify-local"
            container_name = "bootstrap-verify-local-container"

            # 1. Build the image
            build_start = time.time()
            build_cmd = [
                "timeout",
                str(image_build_timeout_secs),
                "devcontainer",
                "build",
                "--workspace-folder",
                str(work_dir),
                "--image-name",
                image_name,
            ]
            logger.info("Building image: %s", " ".join(build_cmd))
            build_proc = subprocess.run(build_cmd, capture_output=True, text=True)
            image_build_seconds = time.time() - build_start
            if build_proc.returncode == TIMEOUT_EXIT_CODE:
                return VerifyResult(
                    success=False,
                    error_message=f"Image build timed out after {image_build_timeout_secs} seconds",
                    image_build_seconds=image_build_seconds,
                )
            if build_proc.returncode != 0:
                return VerifyResult(
                    success=False,
                    error_message=f"Build failed:\n{build_proc.stderr}",
                    image_build_seconds=image_build_seconds,
                )

            # 2. Run tests
            test_start = time.time()
            # Remove any existing container
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            test_cmd = [
                "timeout",
                str(test_timeout_secs),
                "docker",
                "run",
                "--name",
                container_name,
                image_name,
                "/run_all_tests.sh",
            ]
            test_run = subprocess.run(test_cmd, capture_output=True, text=True)
            test_execution_seconds = time.time() - test_start

            # 3. Extract artifacts
            test_artifacts_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["docker", "cp", f"{container_name}:/test_artifacts/.", str(test_artifacts_dir)],
                capture_output=True,
            )

            # 4. Clean up container
            subprocess.run(["docker", "rm", container_name], capture_output=True)

            if test_run.returncode == TIMEOUT_EXIT_CODE:
                return VerifyResult(
                    success=False,
                    error_message=f"Test execution timed out after {test_timeout_secs} seconds",
                    image_build_seconds=image_build_seconds,
                    test_execution_seconds=test_execution_seconds,
                )
            if test_run.returncode == 0:
                return VerifyResult(
                    success=True,
                    image_build_seconds=image_build_seconds,
                    test_execution_seconds=test_execution_seconds,
                )
            else:
                return VerifyResult(
                    success=False,
                    error_message=f"Test run failed with return code {test_run.returncode}",
                    image_build_seconds=image_build_seconds,
                    test_execution_seconds=test_execution_seconds,
                )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def cleanup(self) -> None:
        """Clean up the temporary work directory."""
        if self._work_dir is not None and self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = None
