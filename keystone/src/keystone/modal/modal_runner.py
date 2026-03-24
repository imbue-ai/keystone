"""Modal-based agent runner for running Keystone agent in cloud sandbox.

The sandbox is created once and reused for both agent execution and verification of the agent's work.
"""

import io
import json
import logging
import queue
import shlex
import sys
import tarfile
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import modal

from keystone.agent_runner import (
    GUARDRAIL_SCRIPT_PATH,
    TIMEOUT_EXIT_CODE,
    AgentRunner,
)
from keystone.llm_provider import AgentProvider
from keystone.modal.image import create_modal_image
from keystone.prompts import generate_devcontainer_json
from keystone.schema import (
    AgentConfig,
    InferenceCost,
    StreamEvent,
    StreamType,
    TokenSpending,
    VerificationResult,
)
from keystone.timeouts import sandbox_timeout_seconds

logger = logging.getLogger(__name__)


class SandboxCrashedError(Exception):
    """Raised when the Modal sandbox has died (OOM, timeout, internal error)."""

    pass


class ManagedProcess:
    """
    Wraps a Modal ContainerProcess to provide consistent logging and optional streaming.

    Immediately starts daemon threads to pipe stdout/stderr to Python's logging.
    """

    def __init__(
        self,
        proc: Any,
        prefix: str = "",
        capture: bool = False,
    ) -> None:
        self.proc = proc
        self.prefix = prefix
        self.capture = capture
        self._queue: queue.Queue[StreamEvent | None] | None = queue.Queue() if capture else None

        self._stdout_thread = threading.Thread(
            target=self._stream_reader,
            args=(proc.stdout, StreamType.STDOUT),
            name=f"modal-stdout-{prefix}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stream_reader,
            args=(proc.stderr, StreamType.STDERR),
            name=f"modal-stderr-{prefix}",
            daemon=True,
        )

        self._stdout_thread.start()
        self._stderr_thread.start()

    def _stream_reader(self, stream: Iterable[str], stream_name: StreamType) -> None:
        logger = logging.getLogger("keystone.modal")
        try:
            for chunk in stream:
                # Modal may return multiple lines in a single chunk, so split them
                for line in chunk.splitlines():
                    clean_line = line.rstrip()
                    if not clean_line:
                        continue
                    # Log immediately to Python's logging system
                    # Format: [name] STDOUT/STDERR: line
                    if stream_name == StreamType.STDERR:
                        logger.info(f"[{self.prefix}] STDERR: {clean_line}")
                    else:
                        logger.info(f"[{self.prefix}] STDOUT: {clean_line}")
                    if self._queue is not None:
                        self._queue.put(StreamEvent(stream=stream_name, line=clean_line))
        except Exception:
            # Stream closed due to sandbox termination - this is expected
            pass

        if self._queue is not None:
            self._queue.put(None)  # Signal this stream is done

    def wait(self) -> int:
        """Block until the process and its logging threads finish."""
        try:
            self.proc.wait()
        except Exception as e:
            err_msg = str(e).lower()
            if "already finished" in err_msg or "internal server error" in err_msg:
                raise SandboxCrashedError(f"Sandbox died during '{self.prefix}': {e}") from e
            raise
        self._stdout_thread.join(timeout=10)
        self._stderr_thread.join(timeout=10)
        return self.proc.returncode or 0

    def stream(self) -> Iterator[StreamEvent]:
        """Yield captured events until the process finishes."""
        if self._queue is None:
            raise RuntimeError("Process was not started with capture=True")

        streams_done = 0
        while streams_done < 2:
            try:
                event = self._queue.get(timeout=30)
            except queue.Empty:
                # Queue stalled - check if threads are still alive
                alive = self._stdout_thread.is_alive() or self._stderr_thread.is_alive()
                if not alive:
                    break
                continue
            if event is None:
                streams_done += 1
            else:
                yield event
        self.wait()

    def terminate(self) -> None:
        """Terminate the underlying process."""
        self.proc.terminate()


def _is_sandbox_crash(exc: Exception) -> bool:
    """Check if an exception indicates the Modal sandbox has died."""
    msg = str(exc).lower()
    return any(
        pattern in msg
        for pattern in [
            "already finished",
            "internal server error",
            "sandbox terminated",
            "sandbox timed out",
        ]
    )


def run_modal_command(
    sb: modal.Sandbox, *args: str, capture: bool = False, name: str, **kwargs: Any
) -> ManagedProcess:
    """Helper to execute a command and return a ManagedProcess.

    Args:
        sb: Modal sandbox to run command in
        args: Command and arguments
        capture: Whether to capture output for streaming
        name: Short name for this process (required, used in log prefix)
        **kwargs: Additional arguments passed to sb.exec()
    """
    logger.info(f"[{name}] Running: {shlex.join(args)}")
    try:
        proc = sb.exec(*args, **kwargs)
    except Exception as e:
        if _is_sandbox_crash(e):
            raise SandboxCrashedError(f"Sandbox died before exec '{name}': {e}") from e
        raise
    return ManagedProcess(proc, prefix=name, capture=capture)


_SCRIPT_DIR = Path(__file__).parent


class ModalAgentRunner(AgentRunner):
    """Run agent in a Modal sandbox with Docker support.

    The sandbox is created once via ensure_sandbox() and reused for both
    agent execution and verification. This saves the 20-30s cold start
    and benefits from Docker's build cache.
    """

    def __init__(
        self,
        agent_time_limit_seconds: int,
        docker_registry_mirror: str | None = None,
    ) -> None:
        self._agent_time_limit_seconds = agent_time_limit_seconds
        self._docker_registry_mirror = docker_registry_mirror
        self._exit_code: int = 1
        self._devcontainer_tarball: bytes = b""
        self._sandbox: modal.Sandbox | None = None
        self._cached_inference_cost: InferenceCost | None = None
        self._cost_limit_exceeded: bool = False
        self._agent_done: threading.Event = threading.Event()

    def ensure_sandbox(self) -> modal.Sandbox:
        """Create sandbox if not already created. Returns the sandbox."""
        if self._sandbox is not None:
            return self._sandbox

        modal.enable_output()
        print("Creating Modal sandbox with Docker...", file=sys.stderr)

        app = modal.App.lookup("keystone-sandbox", create_if_missing=True)
        image = create_modal_image()

        self._sandbox = modal.Sandbox.create(
            app=app,
            image=image,
            timeout=sandbox_timeout_seconds(self._agent_time_limit_seconds),
            experimental_options={"enable_docker": True},
        )

        sandbox_id = self._sandbox.object_id
        print(f"Modal sandbox created: {sandbox_id}", file=sys.stderr)
        print(
            "  Dashboard: https://modal.com/apps/imbue/main/deployed/keystone-sandbox",
            file=sys.stderr,
        )
        print(f"  Shell:     modal shell {sandbox_id}", file=sys.stderr)

        # Configure Docker Hub mirror BEFORE starting the daemon.  The mirror is a
        # pull-through cache — Docker checks it first for all images, so cached
        # pulls never touch Docker Hub (metadata or layers).  Default: mirror.gcr.io.
        assert self._docker_registry_mirror is not None, (
            "Docker registry mirror must be set when running in modal because otherwise we'll hit Docker Hub rate limits."
        )
        if self._docker_registry_mirror:
            logger.info("Configuring Docker Hub mirror: %s", self._docker_registry_mirror)
            mirror_config = f'{{"registry-mirrors": ["{self._docker_registry_mirror}"]}}'
            with self._sandbox.open("/etc/docker/daemon.json", "w") as f:
                f.write(mirror_config)

        # Start Docker daemon
        run_modal_command(self._sandbox, "/start-dockerd.sh", name="dockerd")
        logger.info("Waiting for Docker daemon to be ready...")
        run_modal_command(self._sandbox, "/wait_for_docker.sh", name="docker-wait").wait()

        return self._sandbox

    def upload_project(
        self, project_archive: bytes, agents_md: str | None = None, guardrail: bool = True
    ) -> None:
        """Upload project archive to sandbox."""
        sb = self.ensure_sandbox()
        logger.info("Uploading project to sandbox...")
        run_modal_command(sb, "rm", "-rf", "/project", name="upload").wait()
        run_modal_command(sb, "mkdir", "-p", "/project", name="upload").wait()

        with sb.open("/tmp/project.tar.gz", "wb") as f:
            f.write(project_archive)
        run_modal_command(
            sb, "tar", "-xzf", "/tmp/project.tar.gz", "-C", "/project", name="upload"
        ).wait()

        # Save clean copy for guardrail.sh to verify the agent didn't modify source files
        run_modal_command(sb, "rm", "-rf", "/project_clean", name="upload").wait()
        run_modal_command(sb, "mkdir", "-p", "/project_clean", name="upload").wait()
        run_modal_command(
            sb, "tar", "-xzf", "/tmp/project.tar.gz", "-C", "/project_clean", name="upload"
        ).wait()

        # Write devcontainer.json directly into .devcontainer/ so the agent
        # doesn't have to copy it there manually.
        devcontainer_json = generate_devcontainer_json()
        run_modal_command(sb, "mkdir", "-p", "/project/.devcontainer", name="upload").wait()
        with sb.open("/project/.devcontainer/devcontainer.json", "w") as f:
            f.write(devcontainer_json)
        logger.info("Wrote /project/.devcontainer/devcontainer.json to sandbox")
        # Also write to /project_clean/.devcontainer/ for the clean copy.
        run_modal_command(sb, "mkdir", "-p", "/project_clean/.devcontainer", name="upload").wait()
        with sb.open("/project_clean/.devcontainer/devcontainer.json", "w") as f:
            f.write(devcontainer_json)
        logger.info("Wrote /project_clean/.devcontainer/devcontainer.json to sandbox")
        run_modal_command(
            sb,
            "cp",
            "/timestamp_process_output.pl",
            "/project/.devcontainer/timestamp_process_output.pl",
            name="upload",
        ).wait()

        # Upload guardrail.sh for agent self-checks (only when guardrail is enabled)
        if guardrail:
            with sb.open("/project/guardrail.sh", "wb") as f:
                f.write(GUARDRAIL_SCRIPT_PATH.read_bytes())
            run_modal_command(sb, "chmod", "+x", "/project/guardrail.sh", name="upload").wait()

        # Write AGENTS.md if provided (used by codex to read instructions as system context)
        if agents_md:
            with sb.open("/project/AGENTS.md", "w") as f:
                f.write(agents_md)
            logger.info("Wrote /project/AGENTS.md (%d chars)", len(agents_md))

        run_modal_command(sb, "chown", "-R", "agent:agent", "/project", name="upload").wait()
        run_modal_command(sb, "chown", "-R", "agent:agent", "/project_clean", name="upload").wait()

    @property
    def cost_limit_exceeded(self) -> bool:
        """Whether the agent was terminated for exceeding the cost limit."""
        return self._cost_limit_exceeded

    def _cost_monitor(
        self,
        max_budget_usd: float,
        provider_name: str,
        agent: ManagedProcess,
        poll_interval: int,
    ) -> None:
        """Background thread: poll ccusage and kill agent if over budget."""
        while not self._agent_done.wait(timeout=poll_interval):
            try:
                cost = self.run_ccusage(provider_name)
                if cost.cost_usd > max_budget_usd:
                    logger.warning(
                        "Cost limit exceeded: $%.4f > $%.4f — terminating agent process",
                        cost.cost_usd,
                        max_budget_usd,
                    )
                    self._cost_limit_exceeded = True
                    agent.terminate()
                    return
                logger.info(
                    "Cost check: $%.4f / $%.4f", cost.cost_usd, max_budget_usd
                )
            except Exception:
                logger.warning("Cost monitor: ccusage poll failed, will retry", exc_info=True)

    def run(
        self,
        prompt: str,
        project_archive: bytes,
        agent_config: AgentConfig,
        provider: AgentProvider,
        agents_md: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Run the agent in the Modal sandbox."""
        self.ensure_sandbox()
        self.upload_project(
            project_archive, agents_md=agents_md, guardrail=agent_config.guardrail
        )

        try:
            yield from self._run_agent(prompt, agent_config, provider)
        except SandboxCrashedError as e:
            logger.error("Sandbox crashed during agent run: %s", e)
            self._exit_code = 1
            self._sandbox = None  # Mark as dead, don't try to terminate
            yield StreamEvent(
                stream=StreamType.STDERR,
                line=f"SANDBOX_CRASHED: {e}",
            )
        except Exception:
            if self._sandbox:
                try:
                    self._cached_inference_cost = self.run_ccusage(provider.name)
                except Exception as ccusage_err:
                    logger.warning("ccusage failed during exception cleanup: %s", ccusage_err)
                self._sandbox.terminate()
                self._sandbox = None
            raise

    def _run_agent(
        self,
        prompt: str,
        agent_config: AgentConfig,
        provider: AgentProvider,
    ) -> Iterator[StreamEvent]:
        """Execute the agent inside the sandbox (sandbox and project already set up)."""
        assert self._sandbox is not None
        sb = self._sandbox

        agent_cmd = agent_config.agent_cmd or provider.default_cmd
        max_budget_usd = agent_config.max_budget_usd
        time_limit_seconds = agent_config.agent_time_limit_seconds
        cost_poll_interval_seconds = agent_config.cost_poll_interval_seconds

        # Reset cost-monitor state for this run
        self._cost_limit_exceeded = False
        self._agent_done.clear()

        # Set up provider-specific env vars (e.g. API keys)
        logger.info("Starting agent (provider=%s)...", provider.name)
        env_vars = provider.env_vars()
        if not env_vars:
            logger.warning("Provider %s returned no env vars (missing API key?)", provider.name)

        # Build agent command via provider
        cmd_parts = provider.build_command(prompt, max_budget_usd, agent_cmd)

        # Run agent in project directory
        # We write a wrapper script to avoid quoting hell with 'su -c'
        export_lines = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in env_vars.items() if v)

        agent_script_content = f"""#!/bin/bash
set -e
cd /project
{export_lines}
exec timeout {time_limit_seconds} {shlex.join(cmd_parts)}
"""
        # Upload script using Modal's native filesystem API
        with sb.open("/run_agent.sh", "w") as f:
            f.write(agent_script_content)
        run_modal_command(sb, "chmod", "+x", "/run_agent.sh", name="setup").wait()
        run_modal_command(sb, "chown", "agent:agent", "/run_agent.sh", name="setup").wait()

        logger.info("Executing: su agent -c /run_agent.sh")
        agent = run_modal_command(
            sb,
            "su",
            "agent",
            "-c",
            "/run_agent.sh",
            env=None,
            pty=True,
            name="agent",
            capture=True,
        )

        # Start cost-monitor thread (if enabled)
        monitor: threading.Thread | None = None
        if cost_poll_interval_seconds > 0:
            monitor = threading.Thread(
                target=self._cost_monitor,
                args=(max_budget_usd, provider.name, agent, cost_poll_interval_seconds),
                daemon=True,
                name="cost-monitor",
            )
            monitor.start()

        try:
            yield from agent.stream()
            self._exit_code = agent.wait()
        finally:
            # Signal the monitor thread to stop and wait for it
            self._agent_done.set()
            if monitor is not None:
                monitor.join(timeout=5)

        if self._cost_limit_exceeded:
            self._exit_code = 1

        # 5. Extract .devcontainer directory
        logger.info("Extracting .devcontainer from sandbox...")

        # List what the agent produced for diagnostics
        ls_proc = run_modal_command(
            sb, "find", "/project/.devcontainer", "-type", "f", name="extract-ls"
        )
        ls_exit = ls_proc.wait()
        if ls_exit != 0:
            logger.warning(
                "Agent did not create /project/.devcontainer (find exit code %d)", ls_exit
            )

        # Create tarball in sandbox, then read it using Modal's native filesystem API
        tar_proc = run_modal_command(
            sb,
            "tar",
            "-czf",
            "/tmp/devcontainer.tar.gz",
            "-C",
            "/project",
            ".devcontainer",
            name="extract",
        )
        tar_exit = tar_proc.wait()
        if tar_exit != 0:
            logger.error(
                "Failed to create devcontainer tarball (tar exit code %d). "
                "The agent may not have created a .devcontainer directory.",
                tar_exit,
            )
            return
        with sb.open("/tmp/devcontainer.tar.gz", "rb") as f:
            self._devcontainer_tarball = f.read()
        if len(self._devcontainer_tarball) == 0:
            raise SandboxCrashedError(
                "Devcontainer tarball is 0 bytes - sandbox likely crashed (OOM during Docker build)"
            )
        logger.info("Captured devcontainer tarball: %d bytes", len(self._devcontainer_tarball))

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def get_devcontainer_tarball(self) -> bytes:
        return self._devcontainer_tarball

    def verify(
        self,
        project_archive: bytes,
        devcontainer_tarball: bytes,
        test_artifacts_dir: Path,
        image_build_timeout_seconds: int,
        test_timeout_seconds: int,
    ) -> VerificationResult:
        """Run verification by building and running docker in the existing sandbox.

        Uses docker commands directly instead of Modal's from_dockerfile, which:
        - Avoids 20-30s cold start for a new sandbox
        - Benefits from Docker's build cache already in this sandbox

        Note: Timeouts are enforced via the timeout command wrapper.
        """
        try:
            sb = self.ensure_sandbox()
        except Exception as e:
            logger.error("Failed to create sandbox for verification: %s", e)
            return VerificationResult(
                success=False,
                error_message=f"Failed to create sandbox for verification: {e}",
            )

        # Upload fresh project source
        logger.info("Uploading project source for verification...")
        try:
            run_modal_command(sb, "rm", "-rf", "/project", name="verify-setup").wait()
            run_modal_command(sb, "mkdir", "-p", "/project", name="verify-setup").wait()
            with sb.open("/tmp/project.tar.gz", "wb") as f:
                f.write(project_archive)
            run_modal_command(
                sb, "tar", "-xzf", "/tmp/project.tar.gz", "-C", "/project", name="verify-setup"
            ).wait()

            # Overlay devcontainer
            logger.info(
                "Uploading .devcontainer for verification (%d bytes)...",
                len(devcontainer_tarball),
            )
            with sb.open("/tmp/devcontainer.tar.gz", "wb") as f:
                f.write(devcontainer_tarball)
            run_modal_command(
                sb, "tar", "-xzf", "/tmp/devcontainer.tar.gz", "-C", "/project", name="verify-setup"
            ).wait()
        except SandboxCrashedError as e:
            logger.error("Sandbox crashed during verification setup: %s", e)
            self._sandbox = None
            return VerificationResult(
                success=False,
                error_message=f"Sandbox crashed during verification setup: {e}",
            )

        # List what ended up in .devcontainer for diagnostics
        ls_proc = run_modal_command(
            sb, "find", "/project/.devcontainer", "-type", "f", name="verify-ls"
        )
        ls_proc.wait()

        # Check Dockerfile exists
        check_proc = run_modal_command(
            sb, "test", "-f", "/project/.devcontainer/Dockerfile", name="verify"
        )
        if check_proc.wait() != 0:
            # List what's actually there for debugging
            logger.error(
                "Dockerfile not found after overlay. Tarball was %d bytes. "
                "Listing /project/.devcontainer/ contents above.",
                len(devcontainer_tarball),
            )
            return VerificationResult(
                success=False,
                error_message="Build failed: .devcontainer/Dockerfile not found.",
            )

        image_name = "keystone-verify"
        container_name = "keystone-verify-container"

        # 1. Build the image
        logger.info("Building devcontainer image with docker...")
        build_start = time.time()

        build_cmd = [
            "timeout",
            str(image_build_timeout_seconds),
            "docker",
            "build",
            "--network=host",
            "-t",
            image_name,
            "-f",
            "/project/.devcontainer/Dockerfile",
            "/project",
        ]
        try:
            build_proc = run_modal_command(sb, *build_cmd, name="docker-build")
            build_exit = build_proc.wait()
        except SandboxCrashedError as e:
            image_build_seconds = time.time() - build_start
            logger.error("Sandbox crashed during Docker build (likely OOM): %s", e)
            self._sandbox = None
            return VerificationResult(
                success=False,
                error_message=f"Sandbox crashed during Docker build (likely OOM): {e}",
                image_build_seconds=image_build_seconds,
            )
        image_build_seconds = time.time() - build_start
        if build_exit == TIMEOUT_EXIT_CODE:
            return VerificationResult(
                success=False,
                error_message=f"Image build timed out after {image_build_timeout_seconds} seconds",
                image_build_seconds=image_build_seconds,
            )
        if build_exit != 0:
            return VerificationResult(
                success=False,
                error_message=f"Build failed with exit code {build_exit}",
                image_build_seconds=image_build_seconds,
            )

        # 2. Run tests
        logger.info("Running tests in container...")
        test_start = time.time()
        try:
            run_modal_command(sb, "docker", "rm", "-f", container_name, name="cleanup").wait()
            test_proc = run_modal_command(
                sb,
                "timeout",
                str(test_timeout_seconds),
                "docker",
                "run",
                "--network=host",
                "--name",
                container_name,
                image_name,
                "/run_all_tests.sh",
                name="docker-test",
            )
            test_exit_code = test_proc.wait()
        except SandboxCrashedError as e:
            test_execution_seconds = time.time() - test_start
            logger.error("Sandbox crashed during test execution: %s", e)
            self._sandbox = None
            return VerificationResult(
                success=False,
                error_message=f"Sandbox crashed during test execution: {e}",
                image_build_seconds=image_build_seconds,
                test_execution_seconds=test_execution_seconds,
            )
        test_execution_seconds = time.time() - test_start

        # 3. Extract test artifacts
        # Clean up any pre-existing /tmp/test_artifacts (the agent may have
        # created it during its run, e.g. by running `docker cp` itself).
        # Without this, `docker cp` would nest the directory inside the
        # existing one even with the trailing "/." fix.
        logger.info("Extracting test artifacts...")
        try:
            run_modal_command(
                sb, "rm", "-rf", "/tmp/test_artifacts", name="cleanup_artifacts"
            ).wait()
            run_modal_command(
                sb,
                "docker",
                "cp",
                f"{container_name}:/test_artifacts/.",
                "/tmp/test_artifacts",
                name="cp_test_artifacts",
            ).wait()
            run_modal_command(
                sb,
                "tar",
                "-czf",
                "/tmp/test_artifacts.tar.gz",
                "-C",
                "/tmp/test_artifacts",
                ".",
                name="extract",
            ).wait()

            with sb.open("/tmp/test_artifacts.tar.gz", "rb") as f:
                tarball = f.read()
            test_artifacts_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
                tar.extractall(test_artifacts_dir, filter="data")
            logger.info(f"Test artifacts extracted to {test_artifacts_dir}")
        except SandboxCrashedError as e:
            logger.warning("Sandbox crashed during artifact extraction: %s", e)
            self._sandbox = None
            # Still return the test result - we already have it
        except Exception as e:
            logger.exception("Error extracting artifacts: %s", e)

        # 4. Clean up container
        try:
            run_modal_command(sb, "docker", "rm", container_name, name="cleanup").wait()
        except SandboxCrashedError:
            self._sandbox = None

        if test_exit_code == TIMEOUT_EXIT_CODE:
            return VerificationResult(
                success=False,
                error_message=f"Test execution timed out after {test_timeout_seconds} seconds",
                image_build_seconds=image_build_seconds,
                test_execution_seconds=test_execution_seconds,
            )
        if test_exit_code == 0:
            logger.info("Verification successful!")
            return VerificationResult(
                success=True,
                image_build_seconds=image_build_seconds,
                test_execution_seconds=test_execution_seconds,
            )
        else:
            logger.error(f"Test run failed with return code {test_exit_code}")
            return VerificationResult(
                success=False,
                error_message=f"Test run failed with return code {test_exit_code}",
                image_build_seconds=image_build_seconds,
                test_execution_seconds=test_execution_seconds,
            )

    # Agent state directories to capture (add new agents here)
    _AGENT_DIRS: ClassVar[list[str]] = [".claude", ".codex", ".gemini"]

    def get_agent_dir_tarball(self) -> bytes | None:
        """Extract tarball of agent state directories from the sandbox.

        Looks for known agent directories (e.g. ~/.claude, ~/.codex, ~/.gemini)
        and tars whichever ones exist into a single gzipped tarball.

        Returns:
            Gzipped tarball of agent directories, or None if none found.
        """
        if self._sandbox is None:
            return None

        sb = self._sandbox
        try:
            # Find which agent directories exist
            found_dirs: list[str] = []
            for dir_name in self._AGENT_DIRS:
                check_proc = run_modal_command(
                    sb, "test", "-d", f"/home/agent/{dir_name}", name=f"check-{dir_name}"
                )
                if check_proc.wait() == 0:
                    found_dirs.append(dir_name)

            if not found_dirs:
                logger.info("No agent state directories found in sandbox")
                return None

            logger.info("Found agent directories: %s", found_dirs)

            # Create tarball containing all found directories
            run_modal_command(
                sb,
                "tar",
                "-czf",
                "/tmp/agent_dir.tar.gz",
                "-C",
                "/home/agent",
                *found_dirs,
                name="tar-agent-dirs",
            ).wait()

            # Read tarball
            with sb.open("/tmp/agent_dir.tar.gz", "rb") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error extracting agent dir tarball: {e}")
            return None

    def run_ccusage(self, provider_name: str) -> InferenceCost:
        """Run ccusage/ccusage-codex in the sandbox to get accurate token counts and costs.

        This should be called after the agent finishes. The sandbox must still be alive.
        ccusage reads the agent's JSONL transcript files and computes token usage and pricing.

        Args:
            provider_name: The LLM provider name ('claude', 'codex', etc.)

        Returns:
            InferenceCost populated from ccusage output, or a zero-cost default on failure.
        """
        if self._sandbox is None:
            logger.warning("Cannot run ccusage: sandbox is None")
            return InferenceCost()

        sb = self._sandbox

        # Pick the right ccusage command based on provider
        if provider_name == "codex":
            ccusage_cmd = ["ccusage-codex", "session", "--json"]
        else:
            # Default to claude ccusage for claude/opencode/other providers
            ccusage_cmd = ["ccusage", "session", "--json"]

        try:
            logger.info("Running ccusage (provider=%s)...", provider_name)
            proc = run_modal_command(
                sb,
                "su",
                "agent",
                "-c",
                shlex.join(ccusage_cmd),
                name="ccusage",
                capture=True,
            )

            # Collect stdout (proc.stream() calls wait() internally)
            stdout_lines: list[str] = []
            for event in proc.stream():
                if event.stream == StreamType.STDOUT:
                    stdout_lines.append(event.line)

            ccusage_exit = proc.proc.returncode or 0
            if ccusage_exit != 0:
                logger.warning("ccusage exited with code %d", ccusage_exit)
                return InferenceCost()

            # Parse JSON output
            raw = "\n".join(stdout_lines)
            data = json.loads(raw)

            # ccusage output format varies by version:
            #   Newer: { "sessions": [...], "totals": {...} }
            #   Older:  { "type": "session", "data": [...], "summary": {...} }
            sessions = data.get("sessions") or data.get("data") or []
            if not sessions:
                logger.warning("ccusage returned no sessions: %s", list(data.keys()))
                return InferenceCost()

            # Use the first (and should be only) session
            session = sessions[0]
            # Cost field: "totalCost" (newer) or "costUSD" (older)
            cost_usd = float(session.get("totalCost") or session.get("costUSD") or 0.0)
            token_spending = TokenSpending(
                input=int(session.get("inputTokens", 0)),
                cached=int(session.get("cacheReadTokens", 0)),
                output=int(session.get("outputTokens", 0)),
                cache_creation=int(session.get("cacheCreationTokens", 0)),
            )

            logger.info(
                "ccusage: cost=$%.4f input=%d cached=%d output=%d cache_creation=%d",
                cost_usd,
                token_spending.input,
                token_spending.cached,
                token_spending.output,
                token_spending.cache_creation,
            )

            return InferenceCost(
                cost_usd=cost_usd,
                token_spending=token_spending,
                ccusage_raw=session,
            )

        except Exception as e:
            logger.error("Error running ccusage: %s", e)
            return InferenceCost()

    def get_inference_cost(self, provider_name: str) -> InferenceCost | None:
        """Get inference cost by running ccusage in the sandbox."""
        if self._cached_inference_cost is not None:
            return self._cached_inference_cost
        return self.run_ccusage(provider_name)

    def cleanup(self) -> None:
        """Terminate the Modal sandbox."""
        if self._sandbox:
            print("Terminating Modal sandbox...", file=sys.stderr)
            try:
                self._sandbox.terminate()
            except Exception as e:
                logger.warning("Error terminating sandbox (may already be dead): %s", e)
            self._sandbox = None
