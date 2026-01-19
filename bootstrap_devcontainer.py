import typer
import subprocess
import json
import time
import sys
from pathlib import Path

app = typer.Typer()

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
      iv. For python code, create an aggregated JSON report in pytest-json-report format at test_artifact_dir/pytest-json-report.json
      v. A file called final_result.json stating success/failure.
4. In the Dockerfile, COPY the input source tree into the image to /project_src as a penultimate step. (no volume mounts)
5. The Dockerfile should leave the CWD as /project_src.

Notes:
* Only make changes in the .devcontainer/... subtree.
* Optimize the Dockerfile in stages for faster rebuilds.
* Run parts of test suites in parallel if feasible.
* Monitor test runs to avoid waiting on stuck tests (e.g., use `timeout`).
* If tests cannot be fixed by environment changes, disable them via command line args.

To verify:
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

    try:
        # We use stream-json and verbose for progressive output and token tracking
        full_cmd = [
            agent_cmd,
            "--dangerously-skip-permissions",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        process = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=project_root,
            bufsize=1,
        )

        agent_stdout = ""

        # Stream stdout line by line
        for line in process.stdout:
            agent_stdout += line
            try:
                data = json.loads(line)
                msg_type = data.get("type")

                if msg_type == "assistant":
                    content = data.get("message", {}).get("content", [])
                    for item in content:
                        if item.get("type") == "text":
                            txt = item.get("text", "").strip()
                            if txt:
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
                # Not JSON or partial JSON, just ignore or log if verbose
                pass

        # Capture any remaining stderr
        _, agent_stderr = process.communicate()
        if agent_stderr:
            print(agent_stderr, file=sys.stderr)

        exit_code = process.returncode
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
        print(f"Building image {image_name}...", file=sys.stderr)
        build_proc = subprocess.run(
            [
                "devcontainer",
                "build",
                "--workspace-folder",
                str(project_root),
                "--image-name",
                image_name,
            ],
            capture_output=True,
            text=True,
        )

        if build_proc.returncode == 0:
            # 2. Run tests
            print("Running tests in container...", file=sys.stderr)
            test_run = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{scratch_dir}:/test_artifacts",
                    image_name,
                    "./.devcontainer/run_all_tests.sh",
                    "--test_artifact_dir",
                    "/test_artifacts",
                ],
                capture_output=True,
                text=True,
            )
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
