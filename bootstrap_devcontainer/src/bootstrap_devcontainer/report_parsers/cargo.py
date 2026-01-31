"""Parser for Cargo test JSON output."""

import json
import sys
from pathlib import Path

from bootstrap_devcontainer.schema import TestSummary


def parse_cargo_report(report_path: Path) -> TestSummary | None:
    """Parse Cargo test JSON report file.

    Expected format: newline-delimited JSON from `cargo test -- -Z unstable-options --format json`
    """
    if not report_path.exists():
        return None

    try:
        passed, failed, skipped = [], [], []
        for line in report_path.read_text().strip().split("\n"):
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "test" and event.get("event") == "ok":
                passed.append(event.get("name", ""))
            elif event.get("type") == "test" and event.get("event") == "failed":
                failed.append(event.get("name", ""))
            elif event.get("type") == "test" and event.get("event") == "ignored":
                skipped.append(event.get("name", ""))
        return TestSummary(
            passed_count=len(passed),
            failed_count=len(failed),
            skipped_count=len(skipped),
            passed_tests=sorted(passed),
            failed_tests=sorted(failed),
            skipped_tests=sorted(skipped),
        )
    except Exception as e:
        print(f"Error parsing Cargo test report: {e}", file=sys.stderr)
        return None
