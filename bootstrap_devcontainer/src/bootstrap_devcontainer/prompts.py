from bootstrap_devcontainer.constants import STATUS_MARKER, SUMMARY_MARKER

AGENT_PROMPT_TEMPLATE = f"""
We need to build an appropriate dev container and Dockerfile in which this project's test suite runs successfully. You are currently at the project root.

Instructions:

1. Create a .devcontainer/devcontainer.json file at the project root.
   a. This file should include these lines, specifying where the Dockerfile should be and that the build context includes the entire source tree:
```
  "build": {{
    "dockerfile": "Dockerfile",
    "context": "..",
    // ...
  }}
```
2. Create a .devcontainer/Dockerfile alongside that.
3. Create a .devcontainer/run_all_tests.sh script alongside the Dockerfile (don't forget to make it executable!)
   a. run_all_tests.sh takes no arguments. It always writes test artifacts to /test_artifacts.
   b. It should return 0 (success) IFF all tests pass and forward enough information to stdout/stderr to enable debugging failing tests.
   c. /test_artifacts should be populated with artifacts from running the tests:
      i. For each command run, create a subdirectory with a good “name”.
      ii. In that directory, put files called stdout.txt and stderr.txt, with timestamps.
      iii. Tee the outputs to stdout/stderr.
      iv. Create language-specific JSON test reports in /test_artifacts:
          - Python: /test_artifacts/pytest-json-report.json (use pytest-json-report plugin)
          - Go: /test_artifacts/go-test-report.json (use `go test -json ./...`)
          - Node.js: /test_artifacts/node-test-report.json (use `node --test --test-reporter=json`)
          - Rust: /test_artifacts/cargo-test-report.json (use `cargo test -- -Z unstable-options --format json` or parse output)
      v. A file called /test_artifacts/final_result.json stating success/failure.
4. In the Dockerfile, COPY the input source tree into the image to /project_src as a penultimate step. (no volume mounts for the code.)
5. The Dockerfile should leave the CWD as /project_src.

Notes:
* Start by exploring the repository structure. Use commands like:
  - `find . -type f | sed 's/.*\\.//' | sort | uniq -c | sort -rn` to identify file types
  - `find . -iname '*test*'` to find test-related files and folders
* Only make changes in the .devcontainer/... subtree.
* Optimize the Dockerfile in stages for faster rebuilds.
* Run parts of test suites in parallel if feasible.
* Prefix commands with `time` to see how long they take, and `timeout` to set deadlines.
  Example: `time timeout 300 pytest tests/` to run tests with a 5-minute limit.
* If the project does docker operations (e.g., runs containers as part of tests), ensure the docker CLI
  is installed in the image and run the container with the docker socket exposed. Example:
  ```
  docker run --rm -it \\
    -v /var/run/docker.sock:/var/run/docker.sock \\
    docker:cli ps
  ```
* If tests cannot be fixed by Dockerfile changes, disable them via command line args.
* Disable code coverage collection in run_all_tests.sh (e.g., `pytest --no-cov` or `coverage run` flags) - coverage reports are slow and not needed.
* Emit status updates before and after each major action as plain text output (not via tool calls).
  Simply include the status line in your assistant message text, like:
  {STATUS_MARKER} Exploring repository structure to identify file types and test locations.
  Do NOT use echo or bash commands to emit these - just write them as regular assistant text output.
  Examples:
  - {STATUS_MARKER} Exploring repository structure to identify file types and test locations.
  - {STATUS_MARKER} Creating initial Dockerfile based on detected Python 3.11 project.
  - {STATUS_MARKER} Build failed due to missing dependency; adding libpq-dev to Dockerfile.

When finished, emit a final summary as plain text (not via tool calls):
{SUMMARY_MARKER} <One-line summary of what worked, what didn't, and any tips for future runs.>
Include anything you wish you had been told at the start. Examples:
- {SUMMARY_MARKER} Everything worked. Tip: this project needed uv installed in the container.
- {
    SUMMARY_MARKER
} Tests pass. I wish I'd known earlier that exposing the docker socket to the devcontainer would allow running nested docker commands.

Please don't forget to emit the summary at the end.

To verify, use something like this (adding arguments as appropriate for permissions, etc.)
1. Build with `devcontainer build --workspace-folder .`
2. Run `docker run IMAGE ./.devcontainer/run_all_tests.sh` and check return code.  This should work straight from the image, without the devcontainer lifecycle hooks.
3. If needed, extract artifacts with `docker cp CONTAINER:/test_artifacts ./test_artifacts`.
"""

MODAL_ADDENDUM = """
IMPORTANT: You are running in a Modal sandbox environment.
Bridge networking does not work in this environment due to gVisor/veth restrictions.
When using `docker run`, you MUST use `--network host` for containers to have network access.
When configuring the devcontainer, add "--network=host" to devcontainer.json build options.
Example: `docker run --network host IMAGE CMD`
"""


def build_agent_prompt(agent_in_modal: bool) -> str:
    """Build the agent prompt, optionally adding Modal-specific guidance."""
    prompt = AGENT_PROMPT_TEMPLATE
    if agent_in_modal:
        prompt = prompt + MODAL_ADDENDUM
    return prompt
