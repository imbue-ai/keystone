# Bootstrap Dev Container

Automatically generates a working `.devcontainer/` setup for any project using an AI agent. Given a source directory, it analyzes the project structure and creates:

- `devcontainer.json` - VS Code dev container configuration
- `Dockerfile` - Container image definition
- `run_all_tests.sh` - Test runner script with artifact collection

## Usage

Run directly from the repository using `uvx`:

```bash
uvx --from 'git+https://github.com/imbue-ai/bootstrap_devcontainer@prod' bootstrap-devcontainer <project_path>
```

### Options

- `--scratch-dir` - Directory for intermediate files (default: auto-generated temp dir)

---

## Developer Notes

### Running from source

```bash
uv run python bootstrap_devcontainer.py samples/python_project
```

```bash
uv run python bootstrap_devcontainer.py --scratch-dir `mktemp -d` ~/nix_pytest_docker_build.small/tmp/nix-build-python3.11-accuweather-2.1.1.drv-0 
```
