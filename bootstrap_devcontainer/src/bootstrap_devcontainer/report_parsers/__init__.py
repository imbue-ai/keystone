"""Test report parsers for various languages and test frameworks."""

from pathlib import Path

from bootstrap_devcontainer.report_parsers.cargo import parse_cargo_report
from bootstrap_devcontainer.report_parsers.go import parse_go_report
from bootstrap_devcontainer.report_parsers.node import parse_node_report
from bootstrap_devcontainer.report_parsers.pytest import parse_pytest_report
from bootstrap_devcontainer.report_parsers.types import TestReports

__all__ = [
    "TestReports",
    "parse_cargo_report",
    "parse_go_report",
    "parse_node_report",
    "parse_pytest_report",
    "parse_test_reports",
]


def parse_test_reports(test_artifacts_dir: Path) -> TestReports:
    """Parse test reports from various formats (pytest, go, node, cargo)."""

    test_artifacts_dir = Path(test_artifacts_dir)
    reports = TestReports()

    reports.pytest_summary = parse_pytest_report(test_artifacts_dir / "pytest-json-report.json")
    reports.go_test_summary = parse_go_report(test_artifacts_dir / "go-test-report.json")
    reports.cargo_test_summary = parse_cargo_report(test_artifacts_dir / "cargo-test-report.json")

    # Node has multiple possible file formats
    node_json = test_artifacts_dir / "node-test-report.json"
    node_tap = test_artifacts_dir / "node-test-report.tap"
    if node_json.exists():
        reports.node_test_summary = parse_node_report(node_json)
    elif node_tap.exists():
        reports.node_test_summary = parse_node_report(node_tap)

    return reports
