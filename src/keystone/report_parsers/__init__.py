"""Parse JUnit XML test reports.

JUnit XML is the standard CI/CD test report format. Generate with:
- pytest: `pytest --junitxml=report.xml`
- Node.js: `node --test --test-reporter=junit > report.xml`
- Go: `go test -v ./... 2>&1 | go-junit-report > report.xml`
- Cargo: `cargo nextest run` (writes to target/nextest/default/junit.xml)
"""

from pathlib import Path

from junitparser import JUnitXml, TestCase

from keystone.schema import TestResult

__all__ = ["TestResult", "parse_junit_xml"]


def parse_junit_xml(report_path: Path) -> list[TestResult]:
    """Parse JUnit XML report and return list of test results."""
    if not report_path.exists():
        return []

    xml = JUnitXml.fromfile(str(report_path))
    results = []

    def process_case(case: TestCase) -> None:
        classname = case.classname or ""
        name = case.name or ""
        full_name = f"{classname}::{name}" if classname else name

        results.append(
            TestResult(
                name=full_name,
                passed=case.is_passed,
                skipped=case.is_skipped,
            )
        )

    # Handle testcases directly under root (Node.js style)
    for case in xml.iterchildren(TestCase):
        process_case(case)

    # Handle testcases under testsuites (standard style)
    for suite in xml:
        for case in suite:
            process_case(case)

    return results
