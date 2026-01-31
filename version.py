"""Version information utility."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from functools import cache
from pathlib import Path

from pydantic import BaseModel


class VersionInfo(BaseModel):
    """Version information for the current codebase."""

    git_hash: str
    is_dirty: bool
    branch: str
    commit_count: int
    commit_timestamp: str  # ISO format


@cache
def get_version_info() -> VersionInfo:
    """Get version information from stamp file or git.

    First looks for a version_stamp.json file next to this module.
    If not found, constructs version info from git.
    """
    stamp_path = Path(__file__).parent / "version_stamp.json"

    if stamp_path.exists():
        data = json.loads(stamp_path.read_text())
        return VersionInfo(**data)

    # Get info from git
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
