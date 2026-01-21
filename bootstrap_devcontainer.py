import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from process_runner import run_process

app = typer.Typer()
console = Console()

STATUS_MARKER = "BOOTSTRAP_DEVCONTAINER_STATUS:"
SUMMARY_MARKER = "BOOTSTRAP_DEVCONTAINER_SUMMARY:"

AGENT_PROMPT_TEMPLATE = """
We need to build an appropriate dev container and Dockerfile in which this project's test suite runs successfully. You are currently at the project root.

Instructions:

1. Create a .devcontainer/devcontainer.json file at the project root.
2. Create a .devcontainer/Dockerfile alongside that.
3. Create a run_all_tests.sh script alongside the Dockerfile
   a. run_all_tests.sh should take an arg called --test_artifact_dir
   b. It should return 0 (success) IFF all tests pass and forward enough information to stdout/stderr to enable debugging failing tests.
   c. test_artifact_dir should be populated with artifacts from running the tests:
      i. For each command run, create a subdirectory with a good “name”.
      ii. In that directory, put files called stdout.txt and stderr.txt, with timestamps.
      iii. Tee the outputs to stdout/stderr.
      iv. For python code, create an aggregated JSON report in pytest-json-report format at test_artifact_dir/pytest-json-report.json (make sure to install the pytest-json-report plugin for Python)
      v. A file called final_result.json stating success/failure.
4. In the Dockerfile, COPY the input source tree into the image to /project_src as a penultimate step. (no volume mounts)
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
* If tests cannot be fixed by environment changes, disable them via command line args.
* Emit status updates before and after each major action as plain text output (not via tool calls).
  Simply include the status line in your assistant message text, like:
  BOOTSTRAP_DEVCONTAINER_STATUS: Exploring repository structure to identify file types and test locations.
  Do NOT use echo or bash commands to emit these - just write them as regular assistant text output.
  Examples:
  - BOOTSTRAP_DEVCONTAINER_STATUS: Exploring repository structure to identify file types and test locations.
  - BOOTSTRAP_DEVCONTAINER_STATUS: Creating initial Dockerfile based on detected Python 3.11 project.
  - BOOTSTRAP_DEVCONTAINER_STATUS: Build failed due to missing dependency; adding libpq-dev to Dockerfile.

When finished, emit a final summary as plain text (not via tool calls):
BOOTSTRAP_DEVCONTAINER_SUMMARY: <One-line summary of what worked, what didn't, and any tips for future runs.>
Include anything you wish you had been told at the start. Examples:
- BOOTSTRAP_DEVCONTAINER_SUMMARY: Everything worked. Tip: this project needed uv installed in the container.
- BOOTSTRAP_DEVCONTAINER_SUMMARY: Tests pass. I wish I'd known earlier that exposing the docker socket to the devcontainer would allow running nested docker commands.

Please don't forget to emit the summary at the end.

To verify, use something like this (adding arguments as appropriate for permissions, etc.)
1. Build with `devcontainer build --workspace-folder .`
2. Run `docker run -v /tmp/scratch:/test_artifacts IMAGE ./.devcontainer/run_all_tests.sh --test_artifact_dir /test_artifacts` and check return code.
3. Examine /tmp/scratch content.
"""


@app.command()
def main(
    project_root: Path = typer.Argument(..., help="Path to the source project"),
    scratch_dir: Path = typer.Option(
        ..., "--scratch-dir", help="Directory for test artifacts"
    ),
    agent_cmd: str = typer.Option("claude", help="Agent command to run"),
):
    project_root = project_root.resolve()
    scratch_dir = scratch_dir.resolve()
    scratch_dir.mkdir(parents=True, exist_ok=True)

    prompt = AGENT_PROMPT_TEMPLATE

    start_time = time.time()

    print(f"Starting agent with command: {agent_cmd}", file=sys.stderr)

    token_spending = {"input": 0, "cached": 0, "output": 0}

    def check_and_print_status(text: str) -> bool:
        """Check for status/summary markers in text and print in blue if found.

        Returns True if a marker was found.
        """
        found = False
        for line in text.split("\n"):
            if STATUS_MARKER in line:
                # Extract the status message after the marker
                idx = line.find(STATUS_MARKER)
                status_msg = line[idx:].strip()
                console.print(status_msg, style="blue")
                found = True
            elif SUMMARY_MARKER in line:
                # Extract the summary message after the marker
                idx = line.find(SUMMARY_MARKER)
                summary_msg = line[idx:].strip()
                console.print(summary_msg, style="blue")
                found = True
        return found

    def process_agent_stdout(line: str) -> None:
        """Process a line of agent stdout, extracting messages and token usage."""
        try:
            data = json.loads(line)
            msg_type = data.get("type")

            if msg_type == "assistant":
                content = data.get("message", {}).get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        txt = item.get("text", "").strip()
                        if txt:
                            if not check_and_print_status(txt):
                                print(f"Assistant: {txt}", file=sys.stderr)
                    elif item.get("type") == "tool_use":
                        name = item.get("name")
                        input_data = item.get("input", {})
                        print(f"Tool Call: {name}({input_data})", file=sys.stderr)

            elif msg_type == "result":
                usage = data.get("usage", {})
                token_spending["input"] = usage.get("input_tokens", 0)
                token_spending["cached"] = usage.get("cache_read_input_tokens", 0)
                token_spending["output"] = usage.get("output_tokens", 0)

        except json.JSONDecodeError:
            # Not JSON or partial JSON, just ignore
            pass

    def process_agent_stderr(line: str) -> None:
        """Forward agent stderr to our stderr."""
        print(f"Agent stderr: {line}", file=sys.stderr)

    try:
        # We use stream-json and verbose for progressive output and token tracking
        full_cmd = shlex.split(agent_cmd) + [
            "--dangerously-skip-permissions",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        result = run_process(
            full_cmd,
            cwd=str(project_root),
            stdout_callback=process_agent_stdout,
            stderr_callback=process_agent_stderr,
        )

        exit_code = result.returncode
    except Exception as e:
        print(f"Error running agent: {e}", file=sys.stderr)
        exit_code = 1

    total_time = time.time() - start_time

    # Verification step
    print("Verifying agent's work...", file=sys.stderr)
    verification_success = False
    try:
        image_name = f"bootstrap-test-{project_root.name.lower()}"

        # 1. Build the image
        build_cmd = [
            "devcontainer",
            "build",
            "--workspace-folder",
            str(project_root),
            "--image-name",
            image_name,
        ]
        print(f"Building image: {shlex.join(build_cmd)}", file=sys.stderr)
        build_proc = subprocess.run(build_cmd, capture_output=True, text=True)

        if build_proc.returncode == 0:
            # 2. Run tests
            test_cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{scratch_dir}:/test_artifacts",
                image_name,
                "./.devcontainer/run_all_tests.sh",
                "--test_artifact_dir",
                "/test_artifacts",
            ]
            print(f"Running tests: {shlex.join(test_cmd)}", file=sys.stderr)
            test_run = subprocess.run(test_cmd, capture_output=True, text=True)
            if test_run.returncode == 0:
                print("Verification successful!", file=sys.stderr)
                verification_success = True
            else:
                print(
                    f"Test run failed with return code {test_run.returncode}",
                    file=sys.stderr,
                )
                print(f"STDOUT: {test_run.stdout}", file=sys.stderr)
                print(f"STDERR: {test_run.stderr}", file=sys.stderr)
        else:
            print("Build failed", file=sys.stderr)
            print(f"STDOUT: {build_proc.stdout}", file=sys.stderr)
            print(f"STDERR: {build_proc.stderr}", file=sys.stderr)
    except Exception as e:
        print(f"Verification error: {e}", file=sys.stderr)

    output = {
        "success": verification_success and exit_code == 0,
        "total_time": total_time,
        "token_spending": token_spending,
        "agent_exit_code": exit_code,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    app()
