"""Parser for Node.js test output (TAP, Jest, Mocha formats)."""

import json
import re
import sys
from pathlib import Path

from bootstrap_devcontainer.schema import TestSummary


def parse_node_report(report_path: Path) -> TestSummary | None:
    """Parse Node.js test report file.

    Supports multiple formats:
    - TAP format (.tap file or content starting with "TAP version")
    - Jest JSON format (testResults array)
    - Mocha JSON format (stats + tests)
    """
    if not report_path.exists():
        return None

    try:
        passed, failed, skipped = [], [], []
        content = report_path.read_text().strip()

        if report_path.suffix == ".tap" or content.startswith("TAP version"):
            _parse_tap_format(content, passed, failed, skipped)
        else:
            _parse_json_format(content, passed, failed, skipped)

        return TestSummary(
            passed_count=len(passed),
            failed_count=len(failed),
            skipped_count=len(skipped),
            passed_tests=sorted(passed),
            failed_tests=sorted(failed),
            skipped_tests=sorted(skipped),
        )
    except Exception as e:
        print(f"Error parsing Node test report: {e}", file=sys.stderr)
        return None


def _parse_tap_format(
    content: str,
    passed: list[str],
    failed: list[str],
    skipped: list[str],
) -> None:
    """Parse TAP format test output."""
    for line in content.split("\n"):
        # TAP format: "ok 1 - test name" or "not ok 2 - test name"
        match = re.match(r"^(ok|not ok)\s+\d+\s*-?\s*(.*)", line)
        if match:
            status, name = match.groups()
            name = name.strip()
            if "# SKIP" in name or "# skip" in name:
                skipped.append(name.split("#")[0].strip())
            elif status == "ok":
                passed.append(name)
            else:
                failed.append(name)


def _parse_json_format(
    content: str,
    passed: list[str],
    failed: list[str],
    skipped: list[str],
) -> None:
    """Parse Jest or Mocha JSON format."""
    try:
        report_data = json.loads(content)
        if "testResults" in report_data:
            # Jest format
            for test_file in report_data.get("testResults", []):
                for assertion in test_file.get("assertionResults", []):
                    name = assertion.get("fullName") or assertion.get("title", "")
                    status = assertion.get("status", "")
                    if status == "passed":
                        passed.append(name)
                    elif status == "failed":
                        failed.append(name)
                    elif status in ("pending", "skipped"):
                        skipped.append(name)
        elif "stats" in report_data and "tests" in report_data:
            # Mocha JSON format
            for test in report_data.get("tests", []):
                name = test.get("fullTitle") or test.get("title", "")
                if test.get("pass"):
                    passed.append(name)
                elif test.get("fail"):
                    failed.append(name)
                elif test.get("pending"):
                    skipped.append(name)
    except json.JSONDecodeError:
        pass  # Not valid JSON, skip
