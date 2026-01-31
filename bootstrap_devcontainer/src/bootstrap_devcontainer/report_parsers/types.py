"""Shared types for test report parsers."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bootstrap_devcontainer.schema import TestSummary


class TestReports:
    """Container for per-language test summaries."""

    def __init__(self) -> None:
        self.pytest_summary: TestSummary | None = None
        self.go_test_summary: TestSummary | None = None
        self.node_test_summary: TestSummary | None = None
        self.cargo_test_summary: TestSummary | None = None
