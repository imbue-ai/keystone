"""Agent runner abstraction for local and Modal execution."""

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from logging import getLogger
from pathlib import Path

from keystone.agent_log import create_devcontainer_tarball
from keystone.llm_provider import AgentProvider
from keystone.modal.image import TIMESTAMP_SCRIPT_PATH
from keystone.process_runner import run_process
from keystone.prompts import generate_devcontainer_json
from keystone.schema import AgentConfig, InferenceCost, StreamEvent, StreamType, VerificationResult

GUARDRAIL_SCRIPT_PATH = Path(__file__).parent / "guardrail.sh"
BUDGET_SCRIPT_PATH = Path(__file__).parent / "keystone_budget.sh"

logger = getLogger(__name__)

DEFAULT_AGENT_TIMEOUT = 3600
TIMEOUT_EXIT_CODE = 124  # Exit code used by GNU timeout command


class AgentRunner(ABC):
    """Abstract base class for running the keystone agent."""

    @abstractmethod
    def run(
        self,
        prompt: str,
        project_archive: bytes,
        agent_config: AgentConfig,
        provider: AgentProvider,
        agents_md: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Run the agent and yield output events.

        Args:
            prompt: The prompt to send to the agent.
            project_archive: Git archive tarball of the project.
            agent_config: Agent configuration (budget, timeouts, guardrail, etc.).
            provider: LLM provider for command building and output parsing.
            agents_md: Optional AGENTS.md content to write into the project directory.

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
        image_build_timeout_seconds: int,
        test_timeout_seconds: int,
    ) -> VerificationResult:
        """Run verification tests on pristine source + agent's devcontainer.

        Args:
            project_archive: Git archive tarball of the original project source.
            devcontainer_tarball: Tarball of .devcontainer/ created by agent.
            test_artifacts_dir: Directory to store test artifacts.
            image_build_timeout_seconds: Timeout for building the devcontainer image.
            test_timeout_seconds: Timeout for running tests.

        Returns:
            VerificationResult with success status and optional error message.
        """
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Perform any necessary cleanup (e.g. terminating sandboxes)."""
        ...

    def get_agent_dir_tarball(self) -> bytes | None:
        """Get tarball of agent state directories if available.

        Captures whichever agent directories exist in the sandbox
        (e.g. ~/.claude, ~/.codex, ~/.gemini) as a single gzipped tarball.

        This is optional - only Modal runner implements it since it has access
        to the sandbox filesystem. Local runner returns None.

        Returns:
            Gzipped tarball of agent directories, or None if not available.
        """
        return None

    def get_inference_cost(self, provider_name: str) -> InferenceCost | None:  # noqa: ARG002
        """Get inference cost via ccusage after agent execution.

        Only available on Modal runner where ccusage is installed and the sandbox
        contains only this agent's session data. Returns None for local runs.

        Args:
            provider_name: The LLM provider name ('claude', 'codex', etc.)

        Returns:
            InferenceCost from ccusage, or None if not available.
        """
        return None


class LocalAgentRunner(AgentRunner):
    """Run agent locally using subprocess.

    The agent runs in a clean directory extracted from git archive, ensuring
    repeatability and better Docker build caching.
    """

    def __init__(self) -> None:
        self._exit_code: int = 1
        self._work_dir: Path | None = None
        self._work_dir_td: tempfile.TemporaryDirectory | None = None

    def _check_docker_available(self) -> bool:
        """Check if Docker is available locally."""
        try:
            result = subprocess.run(
                ["docker", "ps"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _with_timeout(seconds: int, cmd: list[str]) -> list[str]:
        """Prepend GNU timeout to cmd if available, otherwise return cmd unchanged."""
        try:
            subprocess.run(["timeout", "--version"], capture_output=True, check=False)
            return ["timeout", str(seconds), *cmd]
        except FileNotFoundError:
            return cmd

    def run(
        self,
        prompt: str,
        project_archive: bytes,
        agent_config: AgentConfig,
        provider: AgentProvider,
        agents_md: str | None = None,
    ) -> Iterator[StreamEvent]:
        agent_cmd = agent_config.agent_cmd or provider.default_cmd
        max_budget_usd = agent_config.max_budget_usd
        time_limit_seconds = agent_config.agent_time_limit_seconds
        guardrail = agent_config.guardrail

        if not self._check_docker_available():
            yield StreamEvent(
                stream=StreamType.STDERR,
                line="Error: Docker is required for local agent execution but not available.",
            )
            self._exit_code = 1
            return

        # Extract archive to temp directory
        yield StreamEvent(
            stream=StreamType.STDERR,
            line="Extracting project archive to working directory...",
        )
        self._work_dir_td = tempfile.TemporaryDirectory(prefix="keystone-agent-")
        self._work_dir = Path(self._work_dir_td.name)
        with tarfile.open(fileobj=io.BytesIO(project_archive), mode="r:gz") as tar:
            tar.extractall(self._work_dir, filter="data")

        # Save a clean copy for guardrail.sh to verify the agent didn't modify source files
        clean_dir = self._work_dir / ".project_clean"
        clean_dir.mkdir()
        with tarfile.open(fileobj=io.BytesIO(project_archive), mode="r:gz") as tar:
            tar.extractall(clean_dir, filter="data")

        # Initialize a git repo so agents that require one (e.g. codex) work correctly.
        subprocess.run(
            ["git", "init"],
            cwd=str(self._work_dir),
            capture_output=True,
            check=False,
        )

        # Seed pre-generated helper files into the work directory.
        # Write devcontainer.json directly into .devcontainer/ so the agent
        # doesn't have to copy it there manually.
        devcontainer_dir = self._work_dir / ".devcontainer"
        devcontainer_dir.mkdir(parents=True, exist_ok=True)
        (devcontainer_dir / "devcontainer.json").write_text(generate_devcontainer_json())
        # Place timestamp helper in .devcontainer/ (not project root) so agents
        # don't accidentally COPY it into their Dockerfile from the wrong path.
        dest_pl = devcontainer_dir / "timestamp_process_output.pl"
        dest_pl.write_bytes(TIMESTAMP_SCRIPT_PATH.read_bytes())
        dest_pl.chmod(0o755)

        # Copy guardrail script into workspace for agent self-checks
        if guardrail:
            dest_guardrail = self._work_dir / "guardrail.sh"
            dest_guardrail.write_bytes(GUARDRAIL_SCRIPT_PATH.read_bytes())
            dest_guardrail.chmod(0o755)

        # Copy budget script so the agent can check remaining time/budget
        dest_budget = self._work_dir / "keystone_budget.sh"
        dest_budget.write_bytes(BUDGET_SCRIPT_PATH.read_bytes())
        dest_budget.chmod(0o755)

        # Write AGENTS.md if provided (used by codex to read instructions as system context)
        if agents_md:
            (self._work_dir / "AGENTS.md").write_text(agents_md)

        events: list[StreamEvent] = []

        def collect_stdout(line: str) -> None:
            events.append(StreamEvent(stream=StreamType.STDOUT, line=line))

        def collect_stderr(line: str) -> None:
            events.append(StreamEvent(stream=StreamType.STDERR, line=line))

        full_cmd = provider.build_command(prompt, max_budget_usd, agent_cmd)
        full_cmd = self._with_timeout(time_limit_seconds, full_cmd)

        # Set budget/time env vars for budget.sh
        ccusage_command = "ccusage-codex" if provider.name == "codex" else "ccusage"
        budget_env = {
            "AGENT_TIME_DEADLINE": str(int(time.time()) + time_limit_seconds),
            "AGENT_BUDGET_CAP_USD": str(max_budget_usd),
            "CCUSAGE_COMMAND": ccusage_command,
        }

        result = run_process(
            full_cmd,
            log_prefix="[local_agent]",
            env={**os.environ, **provider.env_vars(), **budget_env},
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
        image_build_timeout_seconds: int,
        test_timeout_seconds: int,
    ) -> VerificationResult:
        """Run verification tests locally using Docker.

        Extracts pristine project source + agent's devcontainer to a temp dir,
        then builds and runs tests.
        """
        if not self._check_docker_available():
            return VerificationResult(
                success=False,
                error_message="Docker is required for local verification but not available.",
            )

        # Extract to fresh temp directory
        work_dir = Path(tempfile.mkdtemp(prefix="keystone-verify-"))
        try:
            # Extract project archive
            with tarfile.open(fileobj=io.BytesIO(project_archive), mode="r:gz") as tar:
                tar.extractall(work_dir, filter="data")

            # Overlay devcontainer
            with tarfile.open(fileobj=io.BytesIO(devcontainer_tarball), mode="r:gz") as tar:
                tar.extractall(work_dir, filter="data")

            # Check if devcontainer.json exists
            devcontainer_json = work_dir / ".devcontainer" / "devcontainer.json"
            if not devcontainer_json.exists():
                return VerificationResult(
                    success=False,
                    error_message="Build failed: .devcontainer/devcontainer.json not found.",
                )

            image_name = "keystone-verify-local"
            container_name = "keystone-verify-local-container"

            # 1. Build the image
            build_start = time.time()
            build_cmd = self._with_timeout(
                image_build_timeout_seconds,
                [
                    "devcontainer",
                    "build",
                    "--workspace-folder",
                    str(work_dir),
                    "--image-name",
                    image_name,
                ],
            )
            logger.info("Building image: %s", " ".join(build_cmd))
            build_proc = subprocess.run(build_cmd, capture_output=True, text=True)
            image_build_seconds = time.time() - build_start
            if build_proc.returncode == TIMEOUT_EXIT_CODE:
                return VerificationResult(
                    success=False,
                    error_message=f"Image build timed out after {image_build_timeout_seconds} seconds",
                    image_build_seconds=image_build_seconds,
                )
            if build_proc.returncode != 0:
                return VerificationResult(
                    success=False,
                    error_message=f"Build failed:\n{build_proc.stderr}",
                    image_build_seconds=image_build_seconds,
                )

            # 2. Run tests
            test_start = time.time()
            # Remove any existing container
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            test_cmd = self._with_timeout(
                test_timeout_seconds,
                [
                    "docker",
                    "run",
                    "--name",
                    container_name,
                    image_name,
                    "/run_all_tests.sh",
                ],
            )
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
                return VerificationResult(
                    success=False,
                    error_message=f"Test execution timed out after {test_timeout_seconds} seconds",
                    image_build_seconds=image_build_seconds,
                    test_execution_seconds=test_execution_seconds,
                )
            if test_run.returncode == 0:
                return VerificationResult(
                    success=True,
                    image_build_seconds=image_build_seconds,
                    test_execution_seconds=test_execution_seconds,
                )
            else:
                return VerificationResult(
                    success=False,
                    error_message=f"Test run failed with return code {test_run.returncode}",
                    image_build_seconds=image_build_seconds,
                    test_execution_seconds=test_execution_seconds,
                )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def cleanup(self) -> None:
        """Clean up the temporary work directory."""
        if self._work_dir_td is not None:
            self._work_dir_td.cleanup()
            self._work_dir_td = None
            self._work_dir = None
