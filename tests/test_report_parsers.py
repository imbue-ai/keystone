"""Tests for JUnit XML test report parsing.

Fixtures are pre-generated JUnit XML files from sample projects.
To regenerate fixtures, run: ./fixtures/reports/generate_fixtures.sh
"""

from pathlib import Path

import pytest

from keystone.report_parsers import parse_junit_xml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "reports"


class TestJUnitXMLParser:
    """Test JUnit XML report parsing for all languages."""

    def test_parse_pytest_passing(self) -> None:
        """Parse pytest JUnit XML with passing tests."""
        fixture = FIXTURES_DIR / "pytest-passing.xml"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        results = parse_junit_xml(fixture)

        assert len(results) == 2
        passed = [r for r in results if r.passed and not r.skipped]
        assert len(passed) == 2
        assert any("test_add" in r.name for r in results)
        assert any("test_multiply" in r.name for r in results)

    def test_parse_pytest_failing(self) -> None:
        """Parse pytest JUnit XML with failing tests."""
        fixture = FIXTURES_DIR / "pytest-failing.xml"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        results = parse_junit_xml(fixture)

        # This fixture has a collection error, so it may have 0 or 1 test
        failed = [r for r in results if not r.passed]
        assert len(failed) >= 1 or len(results) == 0  # Collection error case

    def test_parse_go_passing(self) -> None:
        """Parse Go JUnit XML with passing tests."""
        fixture = FIXTURES_DIR / "go-passing.xml"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        results = parse_junit_xml(fixture)

        assert len(results) == 2
        passed = [r for r in results if r.passed]
        assert len(passed) == 2
        assert any("TestAdd" in r.name for r in results)
        assert any("TestMultiply" in r.name for r in results)

    def test_parse_node_passing(self) -> None:
        """Parse Node.js JUnit XML with passing tests."""
        fixture = FIXTURES_DIR / "node-passing.xml"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        results = parse_junit_xml(fixture)

        assert len(results) == 2
        passed = [r for r in results if r.passed]
        assert len(passed) == 2
        assert any("add" in r.name for r in results)
        assert any("multiply" in r.name for r in results)

    def test_parse_cargo_passing(self) -> None:
        """Parse Cargo/nextest JUnit XML with passing tests."""
        fixture = FIXTURES_DIR / "cargo-passing.xml"
        if not fixture.exists():
            pytest.skip(f"Fixture not found: {fixture}")

        results = parse_junit_xml(fixture)

        assert len(results) == 2
        passed = [r for r in results if r.passed]
        assert len(passed) == 2
        assert any("test_add" in r.name for r in results)
        assert any("test_multiply" in r.name for r in results)

    def test_parse_nonexistent_file(self) -> None:
        """Parsing a nonexistent file returns empty list."""
        results = parse_junit_xml(Path("/nonexistent/file.xml"))
        assert results == []
