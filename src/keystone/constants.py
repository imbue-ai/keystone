"""Shared constants for keystone."""

from pathlib import Path

DEFAULT_LOG_PATH = Path.home() / ".keystone" / "log.sqlite"
DEFAULT_TESTING_LOG_PATH = Path.home() / ".keystone" / "testing_log.sqlite"

STATUS_MARKER = "BOOTSTRAP_DEVCONTAINER_STATUS:"
SUMMARY_MARKER = "BOOTSTRAP_DEVCONTAINER_SUMMARY:"

# ANSI color codes
ANSI_BLUE = "\033[34m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_MAGENTA = "\033[35m"
ANSI_CYAN = "\033[36m"
ANSI_WHITE = "\033[37m"
ANSI_RESET = "\033[0m"
