"""Parser for pytest-json-report output."""

import json
import sys
from pathlib import Path

from bootstrap_devcontainer.schema import TestSummary


def parse_pytest_report(report_path: Path) -> TestSummary | None:
    """Parse pytest JSON report file.

    Expected format: pytest-json-report plugin output with 'tests' array.
    """
    if not report_path.exists():
        return None

    try:
        report_data = json.loads(report_path.read_text())
        tests = report_data.get("tests", [])
        passed = sorted([t["nodeid"] for t in tests if t.get("outcome") == "passed"])
        failed = sorted([t["nodeid"] for t in tests if t.get("outcome") == "failed"])
        skipped = sorted([t["nodeid"] for t in tests if t.get("outcome") == "skipped"])
        return TestSummary(
            passed_count=len(passed),
            failed_count=len(failed),
            skipped_count=len(skipped),
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
        )
    except Exception as e:
        print(f"Error parsing pytest report: {e}", file=sys.stderr)
        return None
