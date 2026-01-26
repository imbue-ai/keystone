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

3. Test Docker:
   ```bash
   cd /root/test-build
   docker build -t hello-test .
   docker run hello-test
   ```

4. Press Ctrl+C in the first terminal to terminate the sandbox.

## Files

- `sandbox_ssh.py` - Creates a sandbox with Docker-in-Docker enabled
- `modal_docker_sandbox.py` - Function-based Docker test (alternative)

## Notes

- Uses `experimental_options={"enable_docker": True}` to enable Docker-in-Docker
- Sandboxes have a 1-hour timeout by default
- A test Dockerfile is pre-populated at `/root/test-build/`
