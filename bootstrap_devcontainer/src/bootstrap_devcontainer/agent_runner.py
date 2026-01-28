"""Agent runner abstraction for local and Modal execution."""

import shlex
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bootstrap_devcontainer.agent_cache import create_devcontainer_tarball
from bootstrap_devcontainer.process_runner import run_process


@dataclass
class StreamEvent:
    """A line of output from the agent process."""

    stream: Literal["stdout", "stderr"]
    line: str


class AgentRunner(ABC):
    """Abstract base class for running the bootstrap agent."""

    @abstractmethod
    def run(
        self,
        prompt: str,
        project_root: Path,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> Iterator[StreamEvent]:
        """Run the agent and yield output events.

        Args:
            prompt: The prompt to send to the agent.
            project_root: Path to the project being bootstrapped.
            max_budget_usd: Maximum budget for agent inference.
            agent_cmd: Base command to run the agent (e.g., "claude").

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


class LocalAgentRunner(AgentRunner):
    """Run agent locally using subprocess."""

    def __init__(self) -> None:
        self._exit_code: int = 1
        self._project_root: Path | None = None

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
        project_root: Path,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> Iterator[StreamEvent]:
        if not self._check_docker_available():
            yield StreamEvent(
                stream="stderr",
                line="Error: Docker is required for local agent execution but not available.",
            )
            self._exit_code = 1
            return

        self._project_root = project_root
        events: list[StreamEvent] = []

        def collect_stdout(line: str) -> None:
            events.append(StreamEvent(stream="stdout", line=line))

        def collect_stderr(line: str) -> None:
            events.append(StreamEvent(stream="stderr", line=line))

        full_cmd = [
            *shlex.split(agent_cmd),
            "--dangerously-skip-permissions",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-budget-usd",
            str(max_budget_usd),
            "-p",
            prompt,
        ]

        result = run_process(
            full_cmd,
            cwd=str(project_root),
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
        if self._project_root is None:
            raise RuntimeError("run() must be called before get_devcontainer_tarball()")
        return create_devcontainer_tarball(self._project_root)
