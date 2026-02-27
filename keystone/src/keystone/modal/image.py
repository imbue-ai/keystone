"""Modal image with Docker and Claude CLI installed.

This is the shared image used by all Modal-based agent runners.
"""

from pathlib import Path

import modal

_MODAL_DIR = Path(__file__).parent
_REPO_ROOT = _MODAL_DIR.parent.parent.parent.parent  # keystone/src/keystone/modal -> repo root
START_DOCKERD_SCRIPT_PATH = _MODAL_DIR / "start_dockerd.sh"
WAIT_FOR_DOCKER_SCRIPT_PATH = _MODAL_DIR / "wait_for_docker.sh"
TIMESTAMP_SCRIPT_PATH = _MODAL_DIR / "timestamp_process_output.pl"
FAKE_CLAUDE_AGENT_SCRIPT_PATH = _REPO_ROOT / "keystone" / "tests" / "fake_claude_agent.py"
FAKE_CODEX_AGENT_SCRIPT_PATH = _REPO_ROOT / "keystone" / "tests" / "fake_codex_agent.py"


IMAGE_CACHE_BUST = "2026-02-27T00:45:00"  # bump to force Modal image rebuild


def create_modal_image() -> modal.Image:
    """Create the Modal image with Docker and Claude CLI installed."""

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
        # Install devcontainer CLI and agent CLIs
        .run_commands(
            "npm install -g @devcontainers/cli @anthropic-ai/claude-code @openai/codex opencode-ai@latest ccusage @ccusage/codex"
        )
        # Add scripts natively
        .add_local_file(START_DOCKERD_SCRIPT_PATH, "/start-dockerd.sh", copy=True)
        .add_local_file(WAIT_FOR_DOCKER_SCRIPT_PATH, "/wait_for_docker.sh", copy=True)
        .add_local_file(TIMESTAMP_SCRIPT_PATH, "/timestamp_process_output.pl", copy=True)
        # Cache bust: bump IMAGE_CACHE_BUST to force rebuild of layers below
        .run_commands(f"echo 'image cache bust: {IMAGE_CACHE_BUST}'")
        # Fake agents for testing (deterministic, no LLM dependency)
        .add_local_file(
            FAKE_CLAUDE_AGENT_SCRIPT_PATH, "/usr/local/bin/fake_claude_agent.py", copy=True
        )
        .add_local_file(
            FAKE_CODEX_AGENT_SCRIPT_PATH, "/usr/local/bin/fake_codex_agent.py", copy=True
        )
        .run_commands(
            "chmod 4755 /start-dockerd.sh",
            "chmod +x /wait_for_docker.sh",
            "chmod +x /timestamp_process_output.pl",
            "chmod +x /usr/local/bin/fake_claude_agent.py",
            "chmod +x /usr/local/bin/fake_codex_agent.py",
        )
        .run_commands(
            "useradd -m -s /bin/bash agent",
            "usermod -aG docker agent",
        )
    )
