"""Version information utility."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from functools import cache
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class VersionInfo(BaseModel):
    """Version information for the current codebase."""

    branch: str
    commit_count: int
    commit_timestamp: str  # ISO format
    git_hash: str
    is_dirty: bool


_UNKNOWN_VERSION = VersionInfo(
    branch="unknown",
    commit_count=0,
    commit_timestamp="1970-01-01T00:00:00",
    git_hash="unknown",
    is_dirty=False,
)


@cache
def get_version_info() -> VersionInfo:
    """Get version information from stamp file or git.

    First looks for a version_stamp.json file next to this module.
    If not found, constructs version info from git.
    If git is unavailable (e.g. running via uvx outside a repo),
    returns a placeholder.
    """
    stamp_path = Path(__file__).parent / "version_stamp.json"

    if stamp_path.exists():
        data = json.loads(stamp_path.read_text())
        return VersionInfo(**data)

    try:
        return _version_info_from_git()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        logger.debug("Git not available; using unknown version info")
        return _UNKNOWN_VERSION


def _version_info_from_git() -> VersionInfo:
    """Construct version info by shelling out to git."""
    git_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    dirty_output = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
    is_dirty = bool(dirty_output)

    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()

    commit_count = int(
        subprocess.check_output(["git", "rev-list", "--count", "HEAD"], text=True).strip()
    )

    timestamp_unix = subprocess.check_output(
        ["git", "log", "-1", "--format=%ct"], text=True
    ).strip()
    commit_timestamp = datetime.fromtimestamp(int(timestamp_unix)).isoformat()

    return VersionInfo(
        git_hash=git_hash,
        is_dirty=is_dirty,
        branch=branch,
        commit_count=commit_count,
        commit_timestamp=commit_timestamp,
    )
