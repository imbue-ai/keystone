# Quick Test Commands for Sculptor

## Setup remote docker

### OSX

if you are on osx, the default DOCKER_HOST env setting (host.docker.internal:2375) should work, however it might be painful if you are running lots of integration tests while also doing SOS.

### Linux or remote

See https://www.notion.so/imbue-ai/How-to-give-your-sculptor-access-to-gitlab-github-linear-294a550faf95808a8889c54403447154?source=copy_link#294a550faf95802db388c94aa35958e5 for how to get a remote docker host.

Do this INSIDE a sculptor task -- manually in the terminal, and/or tell your agent to set this in its env. Alternatively edit the last couple lines of our dockerfile.
```bash
export DOCKER_HOST="tcp://100.78.204.7:12367"
```


## Run Tests
```bash
# Unit tests only
uv run pytest sculptor/sculptor -v

# Integration tests (requires control plane built)
uv run --project sculptor python sculptor/sculptor/cli/dev.py build-control-plane-locally --skip-clean-check
uv run pytest tests/integration/test_claude_agent.py -v

# Acceptance tests (TODO, remove dependence on Modal)
uv run --project test_shotgun test_shotgun run sculptor_acceptance
```
