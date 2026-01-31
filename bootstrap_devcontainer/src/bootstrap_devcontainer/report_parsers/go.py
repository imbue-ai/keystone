"""Parser for Go test JSON output."""

import json
import sys
from pathlib import Path

from bootstrap_devcontainer.schema import TestSummary


def parse_go_report(report_path: Path) -> TestSummary | None:
    """Parse Go test JSON report file.

    Expected format: newline-delimited JSON from `go test -json ./...`
    """
    if not report_path.exists():
        return None

    try:
        passed, failed, skipped = [], [], []
        for line in report_path.read_text().strip().split("\n"):
            if not line:
                continue
            event = json.loads(line)
            if event.get("Action") == "pass" and event.get("Test"):
                passed.append(event["Test"])
            elif event.get("Action") == "fail" and event.get("Test"):
                failed.append(event["Test"])
            elif event.get("Action") == "skip" and event.get("Test"):
                skipped.append(event["Test"])
        return TestSummary(
            passed_count=len(passed),
            failed_count=len(failed),
            skipped_count=len(skipped),
            passed_tests=sorted(passed),
            failed_tests=sorted(failed),
            skipped_tests=sorted(skipped),
        )
    except Exception as e:
        print(f"Error parsing Go test report: {e}", file=sys.stderr)
        return None
