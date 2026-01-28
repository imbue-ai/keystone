# Modal Docker Sandbox Prototype

Prototype for running Docker builds in a Modal sandbox.

## Setup

```bash
# Install modal if needed
uv add modal

# Authenticate with Modal
modal setup
```

## Usage

1. Start the sandbox:
   ```bash
   cd prototypes/modal_docker
   uv run python sandbox_ssh.py
   ```

2. In another terminal, connect to the sandbox:
   ```bash
   modal shell <sandbox-id>
   ```

3. Start Docker and test:
   ```bash
   /start-dockerd.sh
   cd /root/test-build
   docker build -t hello-test .
   docker run --network host hello-test
   ```

4. Press Ctrl+C in the first terminal to terminate the sandbox.

## Files

- `sandbox_ssh.py` - Creates a sandbox with Docker-in-Docker enabled
- `modal_docker_sandbox.py` - Function-based Docker test (alternative)

## Notes

- Uses `experimental_options={"enable_docker": True}` to enable Docker-in-Docker
- Sandboxes have a 1-hour timeout by default
- A test Dockerfile is pre-populated at `/root/test-build/`

## gVisor Limitations

Modal uses gVisor which has networking limitations:

1. **nftables not supported** - The script switches to iptables-legacy
2. **Container networking limited** - `docker run` may fail with network errors

The `/start-dockerd.sh` script:
- Cleans up stale PID files and docker0 bridge from previous runs
- Switches to iptables-legacy
- Sets up IP forwarding and NAT rules
- Starts dockerd with `--iptables=false --ip6tables=false`

### Running containers

Due to gVisor limitations, you **must** use `--network host` when running containers:
```bash
docker run --network host hello-test
```

This is because gVisor doesn't support creating veth pairs/network namespaces needed for Docker's default bridge networking. Without `--network host`, you'll get:
```
failed to add interface vethXXX to sandbox: failed to subscribe to link updates: permission denied
```

Docker **builds** work normally (including `RUN` commands with network access).
