from bootstrap_devcontainer.constants import STATUS_MARKER, SUMMARY_MARKER

AGENT_PROMPT_TEMPLATE = f"""
We need to build an appropriate dev container, Dockerfile, and test runner in which this project's test suite runs successfully.

Your task is to create and populate a .devcontainer/... folder at the root of the project's code tree.
You should only make changes in the .devcontainer/... subtree.

You are currently at a clean copy of the root of the project's code tree,
without any build artifacts or git history.
This copy was created using `git archive`.

Instructions:

1. Create a .devcontainer/devcontainer.json file at the project root.
   Note: devcontainer.json uses JSON5 format, so comments are allowed.
   This file MUST include these lines, specifying exactly where the Dockerfile should be
   and that the build context is the entire source tree:
```
  "build": {{
    "dockerfile": "Dockerfile",
    "context": ".."
  }}
```

2. Create a .devcontainer/Dockerfile alongside that.

The Dockerfile MUST contain these lines, to create a writable test artifacts directory:
```
# Create test artifacts directory.
RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts
```

A nice trick that can dramatically speed up subsequent Dockerfile builds is to pre-warm package caches
by fetching dependencies early in the Dockerfile, before copying the entire source tree into the image.
This can help because if these packages are not present in the image, they will need to be fetched
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

A similar trick can be used with other languages and package managers, such as npm, yarn, pip, and Cargo.

If you use this trick, please also add this environment variable to the Dockerfile so that this works correctly on Modal:
```
# Very important on Modal -- without this, there is a crazy bug that means that some files do not show up in snapshots!  This was a nightmare to debug.
# This is because Modal's snapshotting mechanism does not work correctly with symlinks.
ENV UV_LINK_MODE=copy
```

The Dockerfile MUST end with these lines, specifying where the input source tree should be copied into the image.
You do not have to worry about .dockerignore files right now because your copy of the source tree is pristine.
```
# Copy the entire source tree into the image.
WORKDIR /project_src
COPY . .
```

Optimize the Dockerfile layer ordering for faster rebuilds as you experiment,
putting the files and lines most likely to change towards the end.
For example, you might want to `RUN apt-get install` must-have core dependencies first,
and have subsequent layers of `RUN apt-get install` for dependencies you later discover are necessary.
This way, the layer caching for the early `RUN apt-get install` will be reused when you later add dependencies.

3. Create a .devcontainer/run_all_tests.sh script alongside the Dockerfile.
Note that this will be copied into the image by the `COPY . .` line,
so that the image can execute its own tests.

   a. run_all_tests.sh takes no arguments.
   b. It always writes test artifacts to /test_artifacts inside the container filesystem.
   c. /test_artifacts should be populated with artifacts from running the tests:
      i. For each command run, create a subdirectory with an identifying “name”.
      ii. In that directory, put files called stdout.txt and stderr.txt, with timestamps.
      iii. Tee the outputs to stdout/stderr.
      iv. Create language-specific JSON test reports in /test_artifacts:
          - Python: /test_artifacts/pytest-json-report.json (use pytest-json-report plugin)
          - Go: /test_artifacts/go-test-report.json (use `go test -json ./...`)
          - Node.js: /test_artifacts/node-test-report.json (use `node --test --test-reporter=json`)
          - Rust: /test_artifacts/cargo-test-report.json (use `cargo test -- -Z unstable-options --format json` or parse output)
      v. A file called /test_artifacts/final_result.json stating success/failure.
   d. run_all_tests.sh should forward enough information to stdout/stderr to enable debugging failing tests.
   e. run_all_tests.sh is allowed to fail early (before running all tests) if that helps complete the task faster.
   f. If some of the test runs fail, run_all_tests.sh should fail as well (No need to explicitlyverify this behavior, though).
      You can use `set -euo pipefail` to exit the script if any test fails.
   g. Make it executable: `chmod +x .devcontainer/run_all_tests.sh`.

Tips and Notes:

* Start by exploring the repository structure. Use commands like:
  - `find . -type f | sed 's/.*\\.//' | sort | uniq -c | sort -rn` to identify file types
  - `find . -iname '*test*'` to find test-related files and folders

* Only make changes in the .devcontainer/... subtree.

* Run parts of test suites in parallel if feasible, both inside run_all_tests.sh, and as you explore and debug portions of the test suite.

* For Python projects with simple dependencies, using uv for package management speeds up builds significantly.
  Remember to set PYTHONPATH in run_all_tests.sh if your tests import from the project root without an installed package.

* If tests cannot be fixed by Dockerfile environment changes, disable them via command line args in run_all_tests.sh.

* If the tests have code coverage enabled by default, disable it in run_all_tests.sh to speed things up.
  (e.g., `pytest --no-cov` or `coverage run` flags) - coverage reports are slow and not needed.

* If the project does docker operations (e.g., runs containers as part of tests), ensure the docker CLI
  is installed in the image and run the container with the docker socket exposed. Example:
  ```
  docker run --rm -it \\
    -v /var/run/docker.sock:/var/run/docker.sock \\
    docker:cli ps
  ```

* For polyglot projects (e.g., Python backend + Node frontend), ensure ALL test suites are run.
  This may require installing multiple runtimes (Python, Node, Go, etc.) in the Dockerfile.
  Frontend projects may need Xvfb or Playwright dependencies for browser-based tests.

* Beware of stuck tests. Test suites often hang waiting for conditions that will never occur.
  Use the `timeout` command to limit execution time of test commands.
  Find a balance: too short causes churn, too long wastes time on stuck tests.
  Example: `timeout 300 pytest tests/` limits pytest to 5 minutes.

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

To verify your work, use something like this (adding arguments as appropriate for permissions, etc.)
1. Build with `devcontainer build --workspace-folder .`
2. To test your work, run this command and check the return code:
   `docker run --rm IMAGE ./.devcontainer/run_all_tests.sh`
   This command should work straight from the image, without any of the devcontainer lifecycle hooks.
3. If needed, extract and examine the test artifacts from the image with:
   `docker cp CONTAINER:/test_artifacts /tmp/test_artifacts_RUN_NAME`.
"""

MODAL_ADDENDUM = """

IMPORTANT: You are running in a Modal sandbox environment.
Bridge networking does not work in this environment due to gVisor/veth restrictions.
When using `docker run`, you MUST use `--network host` for containers to have network access.
When configuring the devcontainer, add "--network=host" to devcontainer.json build options.
Example: `docker run --network host IMAGE CMD`

IMPORTANT: Modal's image builder does not support --chown flags in COPY commands.
Do NOT use `COPY --chown=user:group` syntax. Instead, use separate RUN commands to change ownership:
```
COPY file.txt /path/
RUN chown user:group /path/file.txt
```
"""


def build_agent_prompt(agent_in_modal: bool) -> str:
    """Build the agent prompt, optionally adding Modal-specific guidance."""
    prompt = AGENT_PROMPT_TEMPLATE
    if agent_in_modal:
        prompt = prompt + MODAL_ADDENDUM
    return prompt
