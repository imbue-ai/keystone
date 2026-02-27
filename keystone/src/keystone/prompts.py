from __future__ import annotations

import json

from keystone.constants import STATUS_MARKER, SUMMARY_MARKER


def generate_devcontainer_json(cache_registry_url: str | None = None) -> str:
    """Generate the devcontainer.json content.

    When *cache_registry_url* is provided, ``--cache-from`` and ``--cache-to``
    build options are included so that ``devcontainer build`` (and any
    ``docker build`` the agent runs) benefits from the remote registry cache.
    """
    build_options: list[str] = [
        "--network=host",
    ]
    if cache_registry_url is not None:
        cache_ref = f"{cache_registry_url}/buildcache:latest"
        build_options.extend(
            [
                f"--cache-from=type=registry,ref={cache_ref}",
                f"--cache-to=type=registry,ref={cache_ref},mode=max",
            ]
        )

    devcontainer: dict[str, object] = {
        "build": {
            "dockerfile": "Dockerfile",
            "context": "..",
            "options": build_options,
        },
        "runArgs": [
            "--network=host",
        ],
    }
    return json.dumps(devcontainer, indent=2) + "\n"


AGENT_PROMPT_TEMPLATE = f"""
We need to build an appropriate dev container, Dockerfile, and test runner in which this project's test suite runs successfully.

You are currently at a clean copy of the root of the project's code tree, without any build artifacts or git history.
This copy was created using `git archive`.

Your task is to create and populate a .devcontainer/... folder at the root of the project's code tree.

IMPORTANT: Only your changes inside .devcontainer/... will be preserved.
When we capture your work, we extract only the .devcontainer/ directory and reapply it to the original repo.
Any changes you make outside the .devcontainer/ directory (e.g., fixing source files, adding config files) will be lost.

Instructions:

1. Copy the pre-generated devcontainer.json into the .devcontainer/ directory:
   ```bash
   cp ./devcontainer.json .devcontainer/devcontainer.json
   ```
   This file is already configured with the correct build context, Dockerfile path,
   network settings, and build cache options. Do NOT modify it.

2. Create a .devcontainer/Dockerfile alongside that.

  The Dockerfile MUST contain these lines, ideally early in the file, to create a writable test artifacts directory:
```
# Create test artifacts directory.
RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts
```

  A nice trick that can dramatically speed up subsequent Dockerfile builds is to pre-warm package caches
  and fetch/build dependencies early in the Dockerfile, before copying the entire source tree into the image.
  This can help because if these packages are not present in the image, they will need to be fetched/built
  from the internet every time the image is used.
  If the project depends on TensorFlow or PyTorch, this can speed things up a lot.

  As an example of how to do this in Python and UV:
```
# Copy uv from official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# ... other Dockerfile lines ...

# Pre-create uv virtual environment and install dependencies.
# Install dependencies first (without the project itself) to maximize layer caching.
# See: https://docs.astral.sh/uv/guides/integration/docker/#caching
COPY pyproject.toml uv.lock /tmp/deps/
ENV UV_PROJECT_ENVIRONMENT=/venv
RUN cd /tmp/deps && \
    uv sync --locked --no-install-project && \
    echo "Python virtual environment created successfully at $UV_PROJECT_ENVIRONMENT"
```

  If you use this trick, please also add this environment variable to the Dockerfile so that this works correctly on Modal:
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
# Bazel cache will be preserved in the layer
```

IMPORTANT: Do NOT use `COPY . .` to copy the entire source tree, because you are working inside
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

Note: Do NOT create .dockerignore files. You can only write inside the .devcontainer/ directory,
and a .dockerignore file there won't work correctly with the build context set to "..".

The Dockerfile MUST end with these lines, copying the test runner script last since it changes frequently:
```dockerfile
# Copy the test runner script last (this changes often during development)
COPY .devcontainer/run_all_tests.sh /run_all_tests.sh
RUN chmod +x /run_all_tests.sh
```

Optimize the Dockerfile layer ordering for faster rebuilds as you experiment,
putting the files and lines most likely to change towards the end.
For example, you might want to `RUN apt-get install` must-have core dependencies first,
and have subsequent layers of `RUN apt-get install` for dependencies you later discover are necessary.
This way, the layer caching for the early `RUN apt-get install` will be reused when you later add dependencies.

3. Create a .devcontainer/run_all_tests.sh script alongside the Dockerfile.
This will be copied to /run_all_tests.sh in the image by the final COPY command.

   a. run_all_tests.sh takes no arguments.
   b. It always writes test artifacts to /test_artifacts inside the container filesystem.
   c. /test_artifacts should be populated with artifacts from running the tests:
      i. Create JUnit XML test reports in /test_artifacts/junit/.
          All test reports should be JUnit XML format and placed in /test_artifacts/junit/*.xml.
          Create the directory first: `mkdir -p /test_artifacts/junit`
          Examples for common frameworks:
          - Python: `pytest --junitxml=/test_artifacts/junit/pytest.xml`
          - Go: `go test -v ./... 2>&1 | go-junit-report > /test_artifacts/junit/go.xml`
            (install go-junit-report: `go install github.com/jstemmer/go-junit-report/v2@latest`)
          - Node.js: `node --test --test-reporter=junit > /test_artifacts/junit/node.xml`
            For Jest: `npx jest --reporters=jest-junit` then `mv junit.xml /test_artifacts/junit/`
            For Mocha: `npx mocha --reporter mocha-junit-reporter --reporter-options mochaFile=/test_artifacts/junit/mocha.xml`
          - Rust: Use cargo-nextest (install: `RUN cargo install cargo-nextest --locked`)
            Command: `cargo nextest run --profile default`
            Copy report: `cp target/nextest/default/junit.xml /test_artifacts/junit/cargo.xml`
      ii. A file called /test_artifacts/final_result.json stating success/failure.
   d. run_all_tests.sh should forward enough information to stdout/stderr to enable debugging failing tests.
   e. run_all_tests.sh is allowed to fail early (before running all tests) if that helps complete the task faster.
   f. If some of the test runs fail, run_all_tests.sh should fail as well (No need to explicitly verify this behavior, though).
      You can use `set -euo pipefail` to exit the script if any test fails.
   g. There's no need to branch in run_all_tests.sh, because the code tree that you see now will always be the code tree that this script runs against.
   h. If the project uses some framework to run tests (e.g., bazel, buck, CMake, pytest, Jest, Mocha, cargo-nextest), use that framework's built-in reporting capabilities to generate JUnit XML reports.
   i. Make it executable: `chmod +x .devcontainer/run_all_tests.sh`.

Tips and Notes:

* Start by exploring the repository structure. Use commands like:
  - `ls -a` to list all files and directories in the current directory.
  - `cat README.md` or `cat README.rst` to understand the project (check for setup/test instructions)
  - `find . -type f | sed 's/.*\\.//' | sort | uniq -c | sort -rn` to identify file types
  - `find . -iname '*test*'` to find test-related files and folders

* Only make changes in the .devcontainer/... subtree.

* Run parts of test suites in parallel if feasible, both inside run_all_tests.sh, and as you explore and debug portions of the test suite.

* For Python projects with simple dependencies, using uv for package management speeds up builds significantly.
  Remember to set PYTHONPATH in run_all_tests.sh if your tests import from the project root without an installed package,
  and that there may not already be a PYTHONPATH set in the image: `export PYTHONPATH=/project_src:${{PYTHONPATH:-}}`

* If tests cannot be fixed by Dockerfile environment changes, disable them via command line args in run_all_tests.sh.

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

* As you work, emit status updates before and after each major action as plain text output (not via tool calls).
  Simply include the status line in your assistant message text, like:
  {STATUS_MARKER} Exploring repository structure to identify file types and test locations.
  Do NOT use echo or bash commands to emit these - just write them as regular assistant text output.
  Examples:
  - {STATUS_MARKER} Exploring repository structure to identify file types and test locations.
  - {STATUS_MARKER} Creating initial Dockerfile based on detected Python 3.11 project.
  - {STATUS_MARKER} Build failed due to missing dependency; adding libpq-dev to Dockerfile.

* When finished, emit a final summary as plain text (not via tool calls):
{SUMMARY_MARKER} <One-line summary of what worked, what didn't, and any tips for future runs.>
Include anything you wish you had been told at the start. Examples:
- {SUMMARY_MARKER} Everything worked. Tip: this project needed uv installed in the container.
- {
    SUMMARY_MARKER
} Tests pass. I wish I'd known earlier that exposing the docker socket to the devcontainer would allow running nested docker commands.

Please don't forget to emit the summary at the end.

IMPORTANT: Before doing your final verification, run the guardrail check script to catch common mistakes:
```bash
timeout 10m ./guardrail.sh
```
This script validates that:
- All required files exist (.devcontainer/devcontainer.json, Dockerfile, run_all_tests.sh)
- Dockerfile has correct structure (FROM, test_artifacts, COPY run_all_tests.sh)
- run_all_tests.sh has correct structure (JUnit output, final_result.json)
- The Docker image builds successfully

Since both Docker builds and test runs can be slow and even stall, it's a good idea to use some kind of timeout.
You might need to adjust the timeout based on the size of the project, though.

Run this script after creating your files, and fix any reported errors before proceeding.
If the guardrail reports a build failure, read the error output carefully and fix the issue.

Then verify your work using commands like these:

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
```
"""

MODAL_ADDENDUM = """

IMPORTANT: You are running in a Modal sandbox environment.
Bridge networking does not work in this environment due to gVisor/veth restrictions.
When using `docker run`, you MUST use `--network=host` for containers to have network access.
"""

LOCAL_ADDENDUM = """

IMPORTANT: You are running locally (not in a Modal sandbox).
"""

OLD_PART = """
IMPORTANT: Modal's image builder does not support --chown flags in COPY commands.
Do NOT use `COPY --chown=user:group` syntax. Instead, use separate RUN commands to change ownership:
```
COPY file.txt /path/
RUN chown user:group /path/file.txt
"""


def build_agent_prompt(agent_in_modal: bool) -> str:
    """Build the agent prompt, optionally adding Modal-specific guidance."""
    prompt = AGENT_PROMPT_TEMPLATE
    prompt = prompt + MODAL_ADDENDUM if agent_in_modal else prompt + LOCAL_ADDENDUM
    return prompt
