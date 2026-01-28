"""Modal-based agent runner for running bootstrap agent in cloud sandbox."""

import base64
import io
import os
import queue
import shlex
import sys
import tarfile
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import modal

from bootstrap_devcontainer.agent_runner import AgentRunner, StreamEvent

# Script directory for bundled files


def _stream_reader(
    stream: Iterable[str],
    stream_name: str,
    output_queue: "queue.Queue[StreamEvent | None]",
) -> None:
    """Read lines from stream and put them on the queue."""
    for line in stream:
        output_queue.put(StreamEvent(stream=stream_name, line=line.rstrip("\n")))
    output_queue.put(None)  # Signal this stream is done


def stream_modal_process(proc: Any) -> Iterator[StreamEvent]:
    """
    Stream stdout and stderr from a Modal process using threads.

    Similar to process_runner.run_process but for Modal sandbox exec results.
    Uses a queue to interleave stdout and stderr as they arrive.
    """
    output_queue: queue.Queue[StreamEvent | None] = queue.Queue()

    stdout_thread = threading.Thread(
        target=_stream_reader,
        args=(proc.stdout, "stdout", output_queue),
        name="modal-stdout-reader",
    )
    stderr_thread = threading.Thread(
        target=_stream_reader,
        args=(proc.stderr, "stderr", output_queue),
        name="modal-stderr-reader",
    )

    stdout_thread.start()
    stderr_thread.start()

    streams_done = 0
    while streams_done < 2:
        event = output_queue.get()
        if event is None:
            streams_done += 1
        else:
            yield event

    stdout_thread.join()
    stderr_thread.join()
    proc.wait()


_SCRIPT_DIR = Path(__file__).parent

# start-dockerd.sh content (embedded to avoid file path issues at import time)
START_DOCKERD_SCRIPT = """\
#!/bin/bash
set -xe -o pipefail

# Clean up stale state from previous runs
rm -f /var/run/docker.pid /run/docker/containerd/containerd.pid \\
      /var/run/docker/containerd/containerd.pid /var/run/docker.sock

# Remove stale docker0 bridge if it exists (can take time to take effect)
if ip link show docker0 &>/dev/null; then
    ip link delete docker0 || true
    sleep 2
fi

# Find default network device and IP
dev=$(ip route show default | awk '/default/ {print $5}')
if [ -z "$dev" ]; then
    echo "Error: No default device found."
    ip route show
    exit 1
else
    echo "Default device: $dev"
fi
addr=$(ip addr show dev "$dev" | grep -w inet | awk '{print $2}' | cut -d/ -f1)
if [ -z "$addr" ]; then
    echo "Error: No IP address found for device $dev."
    ip addr show dev "$dev"
    exit 1
else
    echo "IP address for $dev: $addr"
fi

# Set up IP forwarding and NAT for container networking
echo 1 > /proc/sys/net/ipv4/ip_forward
iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p tcp
iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p udp

# gVisor doesn't support nftables yet (https://github.com/google/gvisor/issues/10510)
# Explicitly use iptables-legacy
update-alternatives --set iptables /usr/sbin/iptables-legacy
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy

exec /usr/bin/dockerd --iptables=false --ip6tables=false -D
"""


def create_modal_image() -> modal.Image:
    """Create the Modal image with Docker and Claude CLI installed."""
    # Base64 encode the script to avoid heredoc issues in Modal
    script_b64 = base64.b64encode(START_DOCKERD_SCRIPT.encode()).decode()

    return (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install(
            "ca-certificates",
            "curl",
            "gnupg",
            "lsb-release",
            "git",
            "vim",
            "iptables",
            "iproute2",
            "wget",
            # Nice-to-have CLI utilities
            "ncdu",
            "less",
            "gawk",
            "mawk",
            "coreutils",  # includes cut, head, tail, etc.
            "findutils",  # find, xargs
            "grep",
            "sed",
            "diffutils",
            "procps",  # ps, top, etc.
            "htop",
            "tree",
            "jq",
            "file",
            "ripgrep",
            "fd-find",
        )
        # Install Docker
        .run_commands(
            "install -m 0755 -d /etc/apt/keyrings",
            "curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg",
            "chmod a+r /etc/apt/keyrings/docker.gpg",
            'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list',
        )
        .apt_install(
            "docker-ce",
            "docker-ce-cli",
            "containerd.io",
            "docker-buildx-plugin",
            "docker-compose-plugin",
        )
        # Fix runc for Modal/gVisor compatibility
        .run_commands(
            "rm -f $(which runc) || true",
            "wget https://github.com/opencontainers/runc/releases/download/v1.3.0/runc.amd64",
            "chmod +x runc.amd64",
            "mv runc.amd64 /usr/local/bin/runc",
        )
        # Install Node.js (required for devcontainer CLI)
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
        )
        .apt_install("nodejs")
        # Install devcontainer CLI and Claude CLI
        .run_commands("npm install -g @devcontainers/cli @anthropic-ai/claude-code")
        # Add start-dockerd script via base64 (heredocs don't work in Modal)
        .run_commands(
            f"echo '{script_b64}' | base64 -d > /start-dockerd.sh",
            "chmod 4755 /start-dockerd.sh",
        )
        .run_commands(
            "useradd -m -s /bin/bash agent",
            "usermod -aG docker agent",
        )
    )


def _create_project_tarball(project_root: Path) -> bytes:
    """Create a tarball of the project directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(project_root, arcname=".")
    return buf.getvalue()


def _read_claude_auth() -> dict[str, str]:
    """Read Claude authentication from ~/.claude.json or environment."""
    auth_env: dict[str, str] = {}

    # Check for API key in environment first
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        auth_env["ANTHROPIC_API_KEY"] = api_key
        return auth_env

    # Try ~/.claude.json
    claude_config = Path.home() / ".claude.json"
    if claude_config.exists():
        auth_env["CLAUDE_CONFIG_JSON"] = claude_config.read_text()

    return auth_env


class ModalAgentRunner(AgentRunner):
    """Run agent in a Modal sandbox with Docker support."""

    def __init__(self, timeout_seconds: int = 3600) -> None:
        self._timeout_seconds = timeout_seconds
        self._exit_code: int = 1
        self._devcontainer_tarball: bytes = b""
        self._sandbox: modal.Sandbox | None = None

    def run(
        self,
        prompt: str,
        project_root: Path,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> Iterator[StreamEvent]:
        """Run the agent in a Modal sandbox."""
        modal.enable_output()

        print("Creating Modal sandbox with Docker...", file=sys.stderr)

        # Get or create app
        app = modal.App.lookup("bootstrap-devcontainer-sandbox", create_if_missing=True)

        # Create image
        image = create_modal_image()

        # Create sandbox with Docker enabled
        self._sandbox = modal.Sandbox.create(
            app=app,
            image=image,
            timeout=self._timeout_seconds,
            region="us-west-2",
            experimental_options={"enable_docker": True},
        )

        # Print sandbox info for debugging
        sandbox_id = self._sandbox.object_id
        print(f"Modal sandbox created: {sandbox_id}", file=sys.stderr)
        print("  Dashboard: https://modal.com/apps/bootstrap-devcontainer-sandbox", file=sys.stderr)
        print(f"  Shell:     modal shell {sandbox_id}", file=sys.stderr)

        try:
            yield from self._run_in_sandbox(prompt, project_root, max_budget_usd, agent_cmd)
        finally:
            if self._sandbox:
                self._sandbox.terminate()
                self._sandbox = None

    def _run_in_sandbox(
        self,
        prompt: str,
        project_root: Path,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> Iterator[StreamEvent]:
        """Execute agent workflow inside the sandbox."""
        assert self._sandbox is not None
        sb = self._sandbox

        # 1. Start Docker daemon
        yield StreamEvent(stream="stderr", line="Starting Docker daemon in sandbox...")
        sb.exec("/start-dockerd.sh")
        time.sleep(10)  # Give Docker time to start

        # 2. Upload project
        yield StreamEvent(stream="stderr", line="Uploading project to sandbox...")
        project_tarball = _create_project_tarball(project_root)
        sb.exec("mkdir", "-p", "/project").wait()

        # Write tarball via base64 encoding (Modal stdin API uses bytes differently)
        tarball_b64 = base64.b64encode(project_tarball).decode("ascii")
        sb.exec("sh", "-c", f"echo '{tarball_b64}' | base64 -d | tar -xzf - -C /project").wait()
        sb.exec("chown", "-R", "agent:agent", "/project").wait()

        # 3. Set up Claude auth
        yield StreamEvent(stream="stderr", line="Setting up Claude authentication...")
        auth_env = _read_claude_auth()

        if "CLAUDE_CONFIG_JSON" in auth_env:
            # Write config file
            config_content = auth_env["CLAUDE_CONFIG_JSON"]
            sb.exec("mkdir", "-p", "/home/agent").wait()
            sb.exec(
                "sh", "-c", f"cat > /home/agent/.claude.json << 'EOF'\n{config_content}\nEOF"
            ).wait()
            sb.exec("chown", "agent:agent", "/home/agent/.claude.json").wait()

        # 4. Run the agent
        yield StreamEvent(stream="stderr", line="Starting agent...")

        # Debug: check what auth we have
        if "CLAUDE_CONFIG_JSON" in auth_env:
            yield StreamEvent(stream="stderr", line="Using ~/.claude.json for authentication")
        elif "ANTHROPIC_API_KEY" in auth_env:
            yield StreamEvent(stream="stderr", line="Using ANTHROPIC_API_KEY for authentication")
        else:
            yield StreamEvent(stream="stderr", line="WARNING: No Claude authentication found!")

        env_vars = {}
        if "ANTHROPIC_API_KEY" in auth_env:
            env_vars["ANTHROPIC_API_KEY"] = auth_env["ANTHROPIC_API_KEY"]

        # Build agent command
        # Note: agent_cmd might be "claude" or a full path
        cmd_parts = [
            agent_cmd,
            "--dangerously-skip-permissions",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-budget-usd",
            str(max_budget_usd),
        ]

        # Run agent in project directory
        # Run agent in project directory
        # We write a wrapper script to avoid quoting hell with 'su -c'
        agent_script_content = f"""#!/bin/bash
set -e
cd /project
{f"export ANTHROPIC_API_KEY={shlex.quote(env_vars['ANTHROPIC_API_KEY'])}" if "ANTHROPIC_API_KEY" in env_vars else ""}
exec {shlex.join(cmd_parts)}
"""
        # Upload script
        # encode to base64 to avoid heredoc issues
        script_b64 = base64.b64encode(agent_script_content.encode()).decode()
        sb.exec("sh", "-c", f"echo '{script_b64}' | base64 -d > /run_agent.sh").wait()
        sb.exec("chmod", "+x", "/run_agent.sh").wait()
        sb.exec("chown", "agent:agent", "/run_agent.sh").wait()

        # Log just the command without the full prompt
        yield StreamEvent(
            stream="stderr",
            line="Executing: su agent -c /run_agent.sh",
        )
        agent_proc = sb.exec(
            "su",
            "agent",
            "-c",
            "/run_agent.sh",
            env=None,
            pty=True,
        )

        yield StreamEvent(stream="stderr", line="Agent process started, streaming output...")
        # Stream stdout and stderr using threaded reader (like process_runner.py)
        yield from stream_modal_process(agent_proc)
        self._exit_code = agent_proc.returncode or 0

        # 5. Extract .devcontainer directory
        yield StreamEvent(stream="stderr", line="Extracting .devcontainer from sandbox...")
        # Use base64 to handle binary data through text streams
        tar_proc = sb.exec("sh", "-c", "tar -czf - -C /project .devcontainer | base64")
        tar_proc.wait()
        tarball_b64 = tar_proc.stdout.read()
        self._devcontainer_tarball = base64.b64decode(tarball_b64)

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def get_devcontainer_tarball(self) -> bytes:
        return self._devcontainer_tarball
