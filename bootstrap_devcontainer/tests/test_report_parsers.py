"""Tests for test report parsers using fixture files.

Fixtures are pre-generated report files from sample projects.
To regenerate fixtures, run: ./fixtures/reports/generate_fixtures.sh
"""

import shutil
from pathlib import Path

import pytest

from bootstrap_devcontainer.report_parsers import parse_test_reports

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "reports"


class TestPytestReportParser:
    """Test pytest JSON report parsing."""

    def test_parse_pytest_passing(self, tmp_path: Path) -> None:
        """Parse pytest report with passing tests."""
        fixture = FIXTURES_DIR / "pytest-passing.json"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        # Copy fixture to expected location
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        shutil.copy(fixture, artifacts_dir / "pytest-json-report.json")

        reports = parse_test_reports(artifacts_dir)

        assert reports.pytest_summary is not None
        assert reports.pytest_summary.passed_count == 2
        assert reports.pytest_summary.failed_count == 0
        # Test names include full path from where pytest was run
        assert any("test_add" in t for t in reports.pytest_summary.passed_tests)
        assert any("test_multiply" in t for t in reports.pytest_summary.passed_tests)

    def test_parse_pytest_failing(self, tmp_path: Path) -> None:
        """Parse pytest report with failing tests."""
        fixture = FIXTURES_DIR / "pytest-failing.json"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        shutil.copy(fixture, artifacts_dir / "pytest-json-report.json")

        reports = parse_test_reports(artifacts_dir)

        assert reports.pytest_summary is not None
        assert reports.pytest_summary.passed_count == 2
        assert reports.pytest_summary.failed_count == 1
        assert any("test_impossible" in t for t in reports.pytest_summary.failed_tests)


class TestGoReportParser:
    """Test Go test JSON report parsing."""

    def test_parse_go_passing(self, tmp_path: Path) -> None:
        """Parse Go test report with passing tests."""
        fixture = FIXTURES_DIR / "go-test-passing.json"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        shutil.copy(fixture, artifacts_dir / "go-test-report.json")

        reports = parse_test_reports(artifacts_dir)

        assert reports.go_test_summary is not None
        assert reports.go_test_summary.passed_count == 2
        assert reports.go_test_summary.failed_count == 0
        assert "TestAdd" in reports.go_test_summary.passed_tests
        assert "TestMultiply" in reports.go_test_summary.passed_tests


class TestNodeReportParser:
    """Test Node.js test report parsing (TAP, Jest, Mocha formats)."""

    def test_parse_node_tap(self, tmp_path: Path) -> None:
        """Parse Node TAP format report."""
        fixture = FIXTURES_DIR / "node-tap.tap"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        shutil.copy(fixture, artifacts_dir / "node-test-report.tap")

        reports = parse_test_reports(artifacts_dir)

        assert reports.node_test_summary is not None
        assert reports.node_test_summary.passed_count == 2
        assert reports.node_test_summary.failed_count == 0
        assert "add" in reports.node_test_summary.passed_tests
        assert "multiply" in reports.node_test_summary.passed_tests

    def test_parse_node_jest(self, tmp_path: Path) -> None:
        """Parse Jest JSON format report."""
        fixture = FIXTURES_DIR / "node-jest.json"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        # Check if the fixture has actual test results (Jest may fail on Node test syntax)
        import json

        fixture_data = json.loads(fixture.read_text())
        if fixture_data.get("numTotalTests", 0) == 0:
            pytest.skip("Jest fixture has no tests (Node test syntax not compatible with Jest)")

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        shutil.copy(fixture, artifacts_dir / "node-test-report.json")

        reports = parse_test_reports(artifacts_dir)

        assert reports.node_test_summary is not None
        assert reports.node_test_summary.passed_count == 2
        assert reports.node_test_summary.failed_count == 0

    def test_parse_node_mocha(self, tmp_path: Path) -> None:
        """Parse Mocha JSON format report."""
        fixture = FIXTURES_DIR / "node-mocha.json"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        shutil.copy(fixture, artifacts_dir / "node-test-report.json")

        reports = parse_test_reports(artifacts_dir)

        assert reports.node_test_summary is not None
        assert reports.node_test_summary.passed_count == 2
        assert reports.node_test_summary.failed_count == 0


class TestCargoReportParser:
    """Test Cargo/Rust test JSON report parsing."""

    def test_parse_cargo_passing(self, tmp_path: Path) -> None:
        """Parse Cargo test report with passing tests."""
        fixture = FIXTURES_DIR / "cargo-test-passing.json"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        # Check if fixture contains valid JSON (cargo may fail without nightly)
        content = fixture.read_text()
        if not content.strip().startswith("{"):
            pytest.skip("Cargo fixture is not valid JSON (requires nightly compiler)")

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        shutil.copy(fixture, artifacts_dir / "cargo-test-report.json")

        reports = parse_test_reports(artifacts_dir)

        assert reports.cargo_test_summary is not None
        assert reports.cargo_test_summary.passed_count == 2
        assert reports.cargo_test_summary.failed_count == 0
        assert "tests::test_add" in reports.cargo_test_summary.passed_tests
        assert "tests::test_multiply" in reports.cargo_test_summary.passed_tests
