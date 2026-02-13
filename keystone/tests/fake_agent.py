#!/usr/bin/env python3
"""
A deterministic fake agent that generates devcontainer files for the sample Python project.
This allows testing the Docker mechanics without LLM dependencies.

Usage: fake_agent.py --dangerously-skip-permissions -p PROMPT --output-format stream-json --verbose
"""

import argparse
import json
from pathlib import Path

# The devcontainer files to generate (following the format required by prompts.py)
DEVCONTAINER_JSON = """{
    "name": "Python Test Environment",
    "build": {
        "dockerfile": "Dockerfile",
        "context": ".."
    }
}
"""

DOCKERFILE = """FROM python:3.12-slim

# Install uv for fast dependency management
RUN pip install uv

# Install dependencies that are unlikely to change
RUN apt-get update && apt-get install -y --no-install-recommends \\
    && rm -rf /var/lib/apt/lists/*

# Create test artifacts directory.
RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts

# Copy source tree (excluding .devcontainer which changes frequently).
WORKDIR /project_src
COPY app.py ./
COPY tests/ ./tests/
COPY pyproject.toml ./

# Copy the test runner script last (changes frequently during development).
COPY .devcontainer/run_all_tests.sh /run_all_tests.sh
RUN chmod +x /run_all_tests.sh

# Install project dependencies
RUN uv pip install --system -e ".[dev]" || uv pip install --system pytest
"""

RUN_ALL_TESTS_SH = """#!/bin/bash
set -e

# Test artifacts are always written to /test_artifacts
TEST_ARTIFACT_DIR="/test_artifacts"
mkdir -p "$TEST_ARTIFACT_DIR/pytest"
mkdir -p "$TEST_ARTIFACT_DIR/junit"

# Run pytest with JUnit XML report
cd /project_src
python -m pytest tests/ \\
    --junitxml="$TEST_ARTIFACT_DIR/junit/pytest.xml" \\
    -v 2>&1 | tee "$TEST_ARTIFACT_DIR/pytest/stdout.txt"

PYTEST_EXIT_CODE=${PIPESTATUS[0]}

# Write final result
if [ $PYTEST_EXIT_CODE -eq 0 ]; then
    echo '{"success": true}' > "$TEST_ARTIFACT_DIR/final_result.json"
else
    echo '{"success": false}' > "$TEST_ARTIFACT_DIR/final_result.json"
fi

exit $PYTEST_EXIT_CODE
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("-p", "--prompt", type=str)
    parser.add_argument("--output-format", type=str)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--max-budget-usd", type=float, default=1.0)
    parser.parse_args()

    # Create .devcontainer directory
    devcontainer_dir = Path(".devcontainer")
    devcontainer_dir.mkdir(exist_ok=True)

    # Write the files
    (devcontainer_dir / "devcontainer.json").write_text(DEVCONTAINER_JSON)
    (devcontainer_dir / "Dockerfile").write_text(DOCKERFILE)
    run_script = devcontainer_dir / "run_all_tests.sh"
    run_script.write_text(RUN_ALL_TESTS_SH)
    run_script.chmod(0o755)

    # Output in stream-json format like claude does
    # First emit some status messages as an assistant would
    status_messages = [
        "BOOTSTRAP_DEVCONTAINER_STATUS: Exploring repository structure.",
        "BOOTSTRAP_DEVCONTAINER_STATUS: Creating devcontainer.json and Dockerfile.",
        "BOOTSTRAP_DEVCONTAINER_STATUS: Completed setup of devcontainer files.",
        "BOOTSTRAP_DEVCONTAINER_SUMMARY: Created Python devcontainer with pytest support.",
    ]

    for status in status_messages:
        assistant_msg = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": status}]},
        }
        print(json.dumps(assistant_msg))

    result = {
        "type": "result",
        "usage": {
            "input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 0,
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
