import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "manual: test requires external services (registry, docker daemon)",
    )
