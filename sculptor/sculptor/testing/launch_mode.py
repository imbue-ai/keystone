from enum import StrEnum

import pytest

_FLAG_NAME = "--sculptor-launch-mode"

_FLAG_HELP = """The mode in which to launch the Sculptor frontend and backend.
dev-electron: backend with uv, frontend with dev Electron. Requires "just install-frontend". The default and mimics local development.
app-electron: backend and frontend together with packaged Electron app. Requires "just app". Matches the end user experience.
browser: backend with uv, with Playwright-managed browser. Requires "just build-frontend". Kept around for future use.
"""


class LaunchMode(StrEnum):
    """These are the different kinds of sculptors we can run."""

    DEV_ELECTRON = "dev-electron"
    APP_ELECTRON = "app-electron"
    BROWSER = "browser"

    def is_electron(self) -> bool:
        return self in (LaunchMode.DEV_ELECTRON, LaunchMode.APP_ELECTRON)


def add_launch_mode_option(parser: pytest.Parser) -> None:
    parser.addoption(
        _FLAG_NAME,
        action="store",
        default=LaunchMode.DEV_ELECTRON,
        choices=LaunchMode.__members__.values(),
        help=_FLAG_HELP,
    )


def get_launch_mode(config: pytest.Config) -> LaunchMode:
    return LaunchMode(config.getoption(_FLAG_NAME))
