"""Shared constants for bootstrap_devcontainer."""

from pathlib import Path

DEFAULT_CACHE_PATH = Path.home() / ".cache" / "bootstrap_devcontainer.sqlite"

STATUS_MARKER = "BOOTSTRAP_DEVCONTAINER_STATUS:"
SUMMARY_MARKER = "BOOTSTRAP_DEVCONTAINER_SUMMARY:"

# ANSI color codes
ANSI_BLUE = "\033[34m"
ANSI_RESET = "\033[0m"
