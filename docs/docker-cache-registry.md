# Docker Build Cache Registry Support

The keystone CLI now supports using an external Docker registry for build caching when running in Modal. This can significantly speed up builds by caching Docker layers across runs.

## Configuration

Configure the Docker cache registry via environment variables:

```bash
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY="https://registry.example.com"
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_USERNAME="your-username"
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_PASSWORD="your-password"
```

### Environment Variables

- **`BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY`** (required): The full URL of your Docker registry
  - Example: `https://workspace--docker-registry-cache-registry.modal.run`
  
- **`BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_USERNAME`** (optional): Username for registry authentication
  
- **`BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_PASSWORD`** (optional): Password for registry authentication

If only the registry URL is provided without credentials, the registry will be used but authentication will be skipped (useful for public registries).

## How It Works

1. **Environment Detection**: When the Modal sandbox is created, the environment variables are read from the host environment.

2. **Authentication Setup**: If credentials are provided, a `DOCKER_AUTH_CONFIG` JSON is generated and written to `/root/.docker/config.json` in the sandbox.

3. **Build Cache**: During `docker build`, the CLI adds these flags:
   ```bash
   --cache-from type=registry,ref=<registry>/buildcache:latest
   --cache-to type=registry,ref=<registry>/buildcache:latest,mode=max
   ```

4. **Cache Reuse**: Subsequent builds can pull cached layers from the registry, avoiding redundant work.

## Usage Examples

### Basic CLI Usage

```bash
# Set up environment variables
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY="https://my-registry.example.com"
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_USERNAME="myuser"
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_PASSWORD="mypass"

# Run keystone
uv run keystone \
  --project_root ./my-project \
  --test_artifacts_dir ./artifacts \
  --agent_in_modal
```

### Eval Harness Usage

The eval harness automatically inherits environment variables:

```bash
# Set up environment variables
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY="https://my-registry.example.com"
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_USERNAME="myuser"
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY_PASSWORD="mypass"

# Run eval
uv run python evals/eval_cli.py run \
  --repo_list_path repos.jsonl \
  --max_workers 4
```

## Security Notes

- **Credentials are sensitive**: Use secure methods to set environment variables (e.g., secret management systems)
- **Don't commit credentials**: Never add credentials to `.env` files that are checked into version control
- **Scope credentials appropriately**: Use registry credentials with minimal required permissions

## Troubleshooting

### Cache not being used

Check the logs for messages like:
- `"Configured Docker registry authentication for <url>"` - Auth setup succeeded
- `"Using Docker build cache registry: <ref>"` - Cache is being used during build

### Authentication failures

- Verify credentials are correct
- Check that the registry URL is accessible from Modal sandboxes
- Ensure the registry supports Docker's registry cache format

### No speedup observed

- First builds won't have cache hits (cache needs to be populated first)
- The cache reference (`buildcache:latest`) is currently shared across all projects
- Consider using project-specific cache tags in production deployments
