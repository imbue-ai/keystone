import json

from pydantic import BaseModel

from keystone.constants import STATUS_MARKER, SUMMARY_MARKER
from keystone.schema import AgentConfig


class Prompt(BaseModel):
    """The assembled prompt output from :func:`build_prompt`."""

    cli_prompt: str
    agents_md: str | None = None


def generate_devcontainer_json() -> str:
    """Generate the devcontainer.json content."""
    devcontainer: dict[str, object] = {
        "build": {
            "dockerfile": "Dockerfile",
            "context": "..",
            "options": [
                # Necessary on Modal.
                "--network=host",
            ],
        },
        "runArgs": [
            # Necessary on Modal.
            "--network=host",
        ],
    }
    return json.dumps(devcontainer, indent=2) + "\n"


STATUS_UPDATES_AND_SUMMARY_SECTION = f"""
## Status updates

IMPORTANT: As you work, emit status updates before and after each major action as plain text output (not via tool calls).
  Simply include the status line in your assistant message text, like:
  {STATUS_MARKER} Exploring repository structure to identify file types and test locations.
  Do NOT use echo or bash commands to emit these - just write them as regular assistant text output.
  Examples:
  - {STATUS_MARKER} Exploring repository structure to identify file types and test locations.
  - {STATUS_MARKER} Creating initial Dockerfile based on detected Python 3.11 project.
  - {STATUS_MARKER} Build failed due to missing dependency; adding libpq-dev to Dockerfile.

IMPORTANT: When finished, emit a final summary as plain text (not via tool calls).
  Include anything you did that was clever or interesting, and any problems you ran into.
  Examples:
  - {SUMMARY_MARKER} Everything worked. I used python-slim as the base image, and uv for package management. Tip: this project needed uv installed in the container.
  - {SUMMARY_MARKER} At first I was rate-limited by docker hub, and worked around it by using a different base image from ...  I also had to disable 3 tests by editing project's code in the Dockerfile.
  - {SUMMARY_MARKER} All 200 tests pass. My base image was X, and I made sure to only run the apt-install lines once to benefit from layer caching, adding a second layer of apt installs for the dependencies I discovered later.

Please don't forget to emit the summary before ending your turn.
"""

LONG_FORM_AGENT_PROMPT_TEMPLATE = f"""
We need to build an appropriate dev container, Dockerfile, and test runner in which this project's test suite runs successfully.

You are currently at a clean copy of the root of the project's code tree, without any build artifacts or git history.
This copy was created using `git archive`.

Your task is to create and populate a .devcontainer/... folder with an appropriate Dockerfile and test runner script.

IMPORTANT: Only your changes inside .devcontainer/... will be preserved.
When we capture your work, we extract only the .devcontainer/ directory and reapply it to the original repo.
Any changes you make outside the .devcontainer/ directory (e.g., fixing source files, adding config files) will be lost.

## Instructions

1. Copy the pre-generated devcontainer.json into the .devcontainer/ directory:
   ```bash
   cp ./devcontainer.json .devcontainer/devcontainer.json
   ```
   This file is already configured with the correct build context, Dockerfile path,
   network settings, and build cache options. Do NOT modify it.

2. Create a .devcontainer/Dockerfile alongside it that is capable of building and running the project's test suite.

  The Dockerfile MUST contain these lines, ideally early in the file, to create a writable test artifacts directory:
```Dockerfile
# Create test artifacts directory.
RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts
```

  After that inside the Dockerfile: configure, install and build any dependencies that are
  necessary to run the code and test suite.


  The Dockerfile MUST end with these lines, copying the test runner script last since it changes frequently:
```Dockerfile
  # Copy the test runner script last (this changes often during development)
  COPY .devcontainer/run_all_tests.sh /run_all_tests.sh
  RUN chmod +x /run_all_tests.sh
```

3. Create a .devcontainer/run_all_tests.sh script alongside the Dockerfile.
This will be copied to /run_all_tests.sh in the image by the final COPY command.

   a. run_all_tests.sh takes no arguments and is executable: `chmod +x .devcontainer/run_all_tests.sh`.
   b. It always writes test artifacts to /test_artifacts inside the container filesystem.
   c. /test_artifacts should be populated with artifacts from running the tests:
      i. Create JUnit XML test reports in /test_artifacts/junit/.
          All test reports should be JUnit XML format and placed in /test_artifacts/junit/*.xml.
          Create the directory first: `mkdir -p /test_artifacts/junit`
          IMPORTANT: Do NOT hand-write or manually generate the JUnit XML.
          To the extent possible it must be produced by the project's native test framework itself.
          Hand-written XML that doesn't reflect actual test results
          is considered cheating and will cause incorrect pass/fail reporting.
      ii. A file called /test_artifacts/final_result.json stating success/failure.
   d. run_all_tests.sh should forward enough information to stdout/stderr to enable debugging failing tests.
   e. run_all_tests.sh is allowed to fail early (before running all tests) if that helps complete the task faster.
   f. If some of the test runs fail, run_all_tests.sh should fail as well (No need to explicitly verify this behavior, though).
      You can use `set -euo pipefail` to exit the script if any test fails.
   g. There's no need to branch in run_all_tests.sh, because the code tree that you see now will always be the code tree that this script runs against.
   h. If the project uses some framework to run tests (e.g., bazel, buck, CMake, pytest, Jest, Mocha, cargo-nextest), use that framework's built-in reporting capabilities to generate JUnit XML reports.

{STATUS_UPDATES_AND_SUMMARY_SECTION}

## Tips and Notes

* Start by exploring the repository structure. Use commands like:
  - `ls -a` to list all files and directories in the current directory.
  - `cat README.md` or `cat README.rst` to understand the project (check for setup/test instructions)
  - `find . -type f | sed 's/.*\\.//' | sort | uniq -c | sort -rn` to identify file types
  - `find . -iname '*test*'` to find test-related files and folders

* Only make changes in the .devcontainer/... subtree.
  Do NOT create .dockerignore files. You can only write inside the .devcontainer/ directory,
  and a .dockerignore file there won't work correctly with the build context set to "..".

* Run parts of test suites in parallel if feasible, both inside run_all_tests.sh, and as you explore and debug portions of the test suite.

* Optimize the Dockerfile layer ordering for faster rebuilds as you experiment,
  putting the files and lines most likely to change towards the end.
  For example, you might want to `RUN apt-get install` must-have core dependencies first,
  and have subsequent layers of `RUN apt-get install` for dependencies you later discover are necessary.
  This way, the layer caching for the early `RUN apt-get install` will be reused when you later add dependencies.

* IMPORTANT: Do NOT use `COPY . .` to copy the entire source tree, because you are working inside
  the .devcontainer/ directory. Any changes you make there will invalidate the Docker layer cache,
  causing unnecessary rebuilds of all subsequent layers.

  Instead, identify the specific source directories and files needed to build and test the project,
  and copy them explicitly early in the Dockerfile. These files won't change during your work.
  Example:
  ```dockerfile
  # Copy source code (these won't change during agent work)
  WORKDIR /project_src
  COPY src/ ./src/
  COPY tests/ ./tests/
  COPY pyproject.toml uv.lock ./
```

* For Python projects with simple dependencies, using uv for package management speeds up builds significantly.
  Remember to set PYTHONPATH in run_all_tests.sh if your tests import from the project root without an installed package,
  and that there may not already be a PYTHONPATH set in the image: `export PYTHONPATH=/project_src:${{PYTHONPATH:-}}`

* If tests cannot be fixed by Dockerfile environment changes, disable them via command line args in run_all_tests.sh,
  or remove them inside the Dockerfile after copying code into the image.

* If the tests have code coverage enabled by default, disable it in run_all_tests.sh to speed things up.
  (e.g., `pytest --no-cov` or `coverage run` flags) - coverage reports are slow and not needed.

* For polyglot projects (e.g., Python backend + Node frontend), ensure ALL test suites are run.
  This may require installing multiple runtimes (Python, Node, Go, etc.) in the Dockerfile.
  Frontend projects may need Xvfb or Playwright dependencies for browser-based tests.

* Beware of stuck tests. Test suites often hang waiting for conditions that will never occur.
  Use the `timeout` command to limit execution time of test commands.
  Find a balance: too short causes churn, too long wastes time on stuck tests.
  Example: `timeout 300 pytest tests/` limits pytest to 5 minutes.

* If the project source code needs modifications to run tests (e.g., creating config files, setting up test fixtures),
  implement those modifications in the Dockerfile if possible, or in run_all_tests.sh if necessary,
  so that they are preserved.
  Remember that we won't preserve any changes you make outside the .devcontainer/ directory.

* A nice trick that can dramatically speed up subsequent Dockerfile builds is to pre-warm package caches
  and fetch/build dependencies early in the Dockerfile, before copying the entire source tree into the image.
  This can help because if these packages are not present in the image, they will need to be fetched/built
  from the internet every time the image is used.
  If the project depends on TensorFlow or PyTorch, this can speed up the testing step a lot.

  As an example of how to do this in Python and UV:
```
# Copy uv from official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# ... other Dockerfile lines ...

# Pre-create uv virtual environment and install dependencies.
# Install dependencies first (without the project itself) to maximize layer caching.
# See: https://docs.astral.sh/uv/guides/integration/docker/#caching
COPY README.md LICENSE.txt pyproject.toml uv.lock /tmp/deps/
ENV UV_PROJECT_ENVIRONMENT=/venv
RUN cd /tmp/deps && \
    uv sync --locked --no-install-project && \
    echo "Python virtual environment created successfully at $UV_PROJECT_ENVIRONMENT"
```

  If you use uv and preinstall dependencies, please also add this environment variable to the Dockerfile so that this works correctly on Modal:
```
# Very important on Modal -- without this, there is a crazy bug that means that some files do not show up in snapshots!  This was a nightmare to debug.
# This is because Modal's snapshotting mechanism does not work correctly with symlinks.
ENV UV_LINK_MODE=copy
```

  A similar trick can be used with other languages and package managers, such as vcpkg, npm, yarn, pip, and Cargo.

  For compiled languages (C++, Rust, Go) or build systems like Bazel, run the build step in a Dockerfile layer
  so that build artifacts are cached. This avoids rebuilding every time you run the tests.
  Examples:

  For Rust projects:
```dockerfile
# Copy Cargo manifests and source, then build
COPY Cargo.toml Cargo.lock /project_src/
COPY src/ /project_src/src/
WORKDIR /project_src
RUN cargo build --release
# Tests will use the cached build artifacts
```

  For C++/CMake projects:
```dockerfile
COPY CMakeLists.txt /project_src/
COPY src/ /project_src/src/
WORKDIR /project_src
RUN mkdir build && cd build && cmake .. && make -j$(nproc)
```

  For Bazel projects:
```dockerfile
COPY WORKSPACE BUILD.bazel /project_src/
COPY src/ /project_src/src/
WORKDIR /project_src
RUN bazel build //...
# Bazel build artifacts should be preserved in the layer.
```

** You can verify your work using commands like these:

1. To build your image:
```bash
IMAGE_NAME="project_image-$(date +%Y%m%d-%H%M%S)"
devcontainer build \
  --image-name "$IMAGE_NAME" \
  --workspace-folder .
```

2. To run your image:
```
CONTAINER_NAME="project_container-$(date +%Y%m%d-%H%M%S)"
docker run --network host \
  --name "$CONTAINER_NAME" \
  "$IMAGE_NAME" \
  /run_all_tests.sh

# If you want access to the detailed test artifacts from a completed container, you can extract them with:
docker cp "$CONTAINER_NAME:/test_artifacts" "/tmp/test_artifacts.$CONTAINER_NAME"
# Note: If you run a container in detached mode, make sure to `docker wait $CONTAINER_NAME` before trying to extract the test artifacts.
# No need to clean up the container -- you're working in an ephemeral sandbox.

{{GUARDRAIL_SECTION}}
```
"""

# ---------------------------------------------------------------------------
# Guardrail prompt fragments — included only when config.guardrail is True
# ---------------------------------------------------------------------------

_LONGFORM_GUARDRAIL_PROMPT = """\

To aid you in verifying your work, we have provided a guardrail script that will check some basic properties of the
devcontainer and test runner script.  This script simplifies the execution of the docker commands above.

**IMPORTANT: You MUST have a successful guardrail run with 0 exit code BEFORE ending your turn!!**
```bash
timeout 10m ./guardrail.sh  # Timeout may be changed as needed.
```

This script validates that:
- All required files exist (.devcontainer/devcontainer.json, Dockerfile, run_all_tests.sh)
- The Docker image builds successfully
- run_all_tests.sh runs tests and they pass inside the container image, and generates JUnit XML reports in /test_artifacts/junit/*.xml.

If the guardrail exits abnormally, read the error output carefully and fix the issue.

Once you have a successful guardrail run, there's no need to repeat the checks it does;
you can likely end your turn at this point -- using the guardrail can simplify the verification process a lot!

Since both Docker builds and test runs can be slow and even stall, it's a good idea to use some kind of timeout.
You might need to adjust the timeout based on the size of the project, though.  Remember that your first run
of the devcontainer build (which guardrail.sh runs) will be slow because early layers are not cached yet,
so you might need to use a longer timeout for the first run.
"""

_AGENTS_MD_GUARDRAIL_WORKFLOW_STEP = """\
5. Run `timeout 10m ./guardrail.sh` to validate — fix any errors it reports.
"""

_AGENTS_MD_GUARDRAIL_REMINDER = """\
**You MUST run `./guardrail.sh` and get exit code 0 before finishing.**
"""

# ---------------------------------------------------------------------------
# Environment addenda
# ---------------------------------------------------------------------------

MODAL_ADDENDUM = """

IMPORTANT: You are running in a Modal sandbox environment.
Bridge networking does not work in this environment due to gVisor/veth restrictions.
When using `docker build` and `docker run`, you MUST use `--network=host` for containers to have network access.
"""

# FIXME: Why do we tell the agent this?  It's kind of the default assumption, no?
LOCAL_ADDENDUM = """

IMPORTANT: You are running locally (not in a Modal sandbox).
"""


def build_prompt(config: AgentConfig) -> Prompt:
    """Build the prompt for the agent from *config*.

    When ``config.use_agents_md`` is True, the returned :class:`Prompt` has
    ``agents_md`` set to write to the project root and a short
    ``cli_prompt``.  Otherwise ``agents_md`` is ``None`` and ``cli_prompt``
    is the full inline prompt.
    """
    if config.use_agents_md:
        agents_md, cli_prompt = _build_agents_md_prompt(config)
        return Prompt(cli_prompt=cli_prompt, agents_md=agents_md)
    return Prompt(cli_prompt=_build_inline_prompt(config))


def _build_inline_prompt(config: AgentConfig) -> str:
    """Build the full inline agent prompt."""
    prompt = LONG_FORM_AGENT_PROMPT_TEMPLATE.replace(
        "{GUARDRAIL_SECTION}",
        _LONGFORM_GUARDRAIL_PROMPT if config.guardrail else "\n",
    )
    prompt += MODAL_ADDENDUM if config.agent_in_modal else LOCAL_ADDENDUM
    return prompt


# ---------------------------------------------------------------------------
# Codex-optimised prompt
# ---------------------------------------------------------------------------
# Codex-mini has a limited output budget per turn.  A long CLI prompt causes
# it to spend all its reasoning on comprehension, leaving nothing for actual
# tool calls.  The solution: keep the *CLI prompt* very short and put the
# detailed instructions in an AGENTS.md file that codex reads automatically
# as system-level context.
# ---------------------------------------------------------------------------

AGENTS_MD_CONTENTS = f"""\
# Bootstrap Devcontainer - Agent Instructions

You are setting up a reproducible dev container so this project's test suite passes.

## What you must create

All files go inside `.devcontainer/` — nothing outside that directory is preserved.

1. **`.devcontainer/devcontainer.json`** — already pre-generated at `./devcontainer.json`.
   Just copy it: `cp ./devcontainer.json .devcontainer/devcontainer.json`
   Do NOT modify it.

2. **`.devcontainer/Dockerfile`** — must contain, near the top:
   ```dockerfile
   RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts
   ```
   And must end with:
   ```dockerfile
   COPY .devcontainer/run_all_tests.sh /run_all_tests.sh
   RUN chmod +x /run_all_tests.sh
   ```
   - Copy source files explicitly — do NOT use `COPY . .`. You work inside `.devcontainer/`,
     so `COPY . .` would include your own files and invalidate the layer cache on every change.
     Instead: `COPY src/ ./src/`, `COPY pyproject.toml uv.lock ./`, etc.
   - Do NOT create `.dockerignore` files.
   - For compiled languages (Rust, Go, C++), run the build inside a Dockerfile layer so
     artifacts are cached and tests don't need to recompile from scratch each run.
   - If the project needs config files or test fixtures that don't exist in the repo,
     create them in the Dockerfile (or in `run_all_tests.sh`) — changes outside `.devcontainer/` are lost.

3. **`.devcontainer/run_all_tests.sh`** (executable) — should run and document the full test suite:
   - Use `set -euo pipefail`
   - `mkdir -p /test_artifacts/junit` near the top
   - Writes JUnit XML to `/test_artifacts/junit/*.xml` using the test framework's native output.
     Do NOT hand-write or generate JUnit XML manually — it must come from the framework itself.
     Examples: `pytest --junitxml=...`, `cargo nextest run -P ci --config 'profile.ci.junit.path=...'`
   - Writes `/test_artifacts/final_result.json` with `{{"success": true/false}}`
   - For polyglot projects (e.g. Python backend + JS frontend), run ALL test suites and produce separate JUnit XML reports for each test suite.

## Workflow

1. Explore the repo: `ls -a`, `cat README.md`, `cat pyproject.toml`, etc.
2. Identify language, test framework, and dependencies.
3. Create the three files above.
4. Build and test:
   ```bash
   IMAGE_NAME="img-$(date +%s)"
   devcontainer build --image-name "$IMAGE_NAME" --workspace-folder .
   CONTAINER="test-$(date +%s)"
   docker run --network host --name "$CONTAINER" "$IMAGE_NAME" /run_all_tests.sh
   # To inspect artifacts from a completed container:
   docker cp "$CONTAINER:/test_artifacts" /tmp/test_artifacts
   # No need to clean up — you're in an ephemeral sandbox.
   ```

{{GUARDRAIL_WORKFLOW_STEP}}

## More tips

- **Python**: use `uv` for fast installs, and try to have packages pre-installed in the Dockerfile so that they are cached.
  Set `ENV UV_LINK_MODE=copy` in the Dockerfile to avoid problems with Modal's snapshotting —
  without this, Modal's snapshotting breaks on symlinks (hard-won lesson).
  Consider setting `export PYTHONPATH=/project_src:${{PYTHONPATH:-}}` in `run_all_tests.sh` if tests
  import from the project root without an installed package.
- **Stuck tests**: use `timeout` to prevent hangs (e.g. `timeout 300 pytest tests/`).
- **Coverage**: if it's enabled by default, disable it (`--no-cov`, etc.) — it's slow and not needed here.
- **`docker run`** must use `--network=host` in this environment.
- Only changes inside `.devcontainer/` are preserved.

{STATUS_UPDATES_AND_SUMMARY_SECTION}

{{GUARDRAIL_REMINDER}}"""

AGENTS_MD_SHORT_PROMPT = (
    "Set up a .devcontainer with Dockerfile and test runner for this project. "
    "Read the AGENTS.md file first for detailed instructions, then explore the repo and create the files."
)


def _build_agents_md_prompt(config: AgentConfig) -> tuple[str, str]:
    """Return (agents_md_content, short_cli_prompt) for the AGENTS.md path."""
    agents_md = AGENTS_MD_CONTENTS.replace(
        "{GUARDRAIL_WORKFLOW_STEP}",
        _AGENTS_MD_GUARDRAIL_WORKFLOW_STEP if config.guardrail else "",
    ).replace(
        "{GUARDRAIL_REMINDER}",
        _AGENTS_MD_GUARDRAIL_REMINDER if config.guardrail else "",
    )
    if config.agent_in_modal:
        agents_md += "\n\nIMPORTANT: You are in a Modal sandbox. Use `--network=host` for all docker run commands.\n"
    return agents_md, AGENTS_MD_SHORT_PROMPT
