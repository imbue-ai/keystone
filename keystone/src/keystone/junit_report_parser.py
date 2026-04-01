"""Parse JUnit XML test reports.

JUnit XML is the standard CI/CD test report format. Generate with:
- pytest: `pytest --junitxml=report.xml`
- Node.js: `node --test --test-reporter=junit > report.xml`
- Go: `go test -v ./... 2>&1 | go-junit-report > report.xml`
- Cargo: `cargo nextest run` (writes to target/nextest/default/junit.xml)
"""

import contextlib
from pathlib import Path

from junitparser import JUnitXml, TestCase

from keystone.schema import TestResult, VerificationResult

__all__ = ["TestResult", "enrich_verification_with_junit", "parse_junit_xml"]


def enrich_verification_with_junit(
    result: VerificationResult,
    test_artifacts_dir: Path,
) -> VerificationResult:
    """Parse JUnit XML from test_artifacts_dir/junit/*.xml and return an enriched copy.

    The returned ``VerificationResult`` keeps the original success/error_message/timing
    but has ``tests_passed``, ``tests_failed``, ``tests_skipped``, and ``test_results``
    populated from the parsed JUnit reports.  If no XML files are found the result is
    returned unchanged.
    """
    test_results: list[TestResult] = []
    junit_dir = test_artifacts_dir / "junit"
    if junit_dir.is_dir():
        for xml_file in sorted(junit_dir.glob("*.xml")):
            if not xml_file.is_file():
                continue
            with contextlib.suppress(Exception):
                test_results.extend(parse_junit_xml(xml_file))

    if not test_results:
        return result

    n_passed = sum(1 for t in test_results if t.passed and not t.skipped)
    n_failed = sum(1 for t in test_results if not t.passed and not t.skipped)
    n_skipped = sum(1 for t in test_results if t.skipped)

    return result.model_copy(
        update={
            "tests_passed": n_passed,
            "tests_failed": n_failed,
            "tests_skipped": n_skipped,
            "test_results": test_results,
        }
    )


def parse_junit_xml(report_path: Path) -> list[TestResult]:
    """Parse JUnit XML report and return list of test results."""
    if not report_path.exists():
        return []

    xml = JUnitXml.fromfile(str(report_path))
    # Track best result per unique test name: if a test passes at least once,
    # count it as passed (agents sometimes run the suite multiple times).
    seen: dict[str, TestResult] = {}

    def process_case(case: TestCase) -> None:
        classname = case.classname or ""
        name = case.name or ""
        full_name = f"{classname}::{name}" if classname else name

        result = TestResult(
            name=full_name,
            passed=case.is_passed,
            skipped=case.is_skipped,
        )

        prev = seen.get(full_name)
        if prev is None:
            seen[full_name] = result
        elif result.passed and not prev.passed:
            # Upgrade: a later run passed
            seen[full_name] = result

    # Handle testcases directly under root (Node.js style)
    for case in xml.iterchildren(TestCase):
        process_case(case)

    # Handle testcases under testsuites (standard style)
    for suite in xml:
        for case in suite:
            process_case(case)

    return list(seen.values())
