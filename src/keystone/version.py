"""Version information utility.

Resolution order:
1. ``version_stamp.json`` baked in next to this module (CI / Docker builds).
2. Live ``git`` commands (local development).
3. PEP 610 ``direct_url.json`` metadata written by pip/uv when installing
   from a VCS URL (``uvx --from 'git+…'``).  Gives us the exact commit hash
   and requested branch/tag even when the CWD is not a git repo.
4. Unknown placeholder (last resort).
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import subprocess
from functools import cache
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_DIST_NAME = "keystone"


class VersionInfo(BaseModel):
    """Version information for the current codebase."""

    branch: str | None
    commit_count: int
    commit_timestamp: str | None  # ISO format, None when unavailable
    git_hash: str | None
    is_dirty: bool


_UNKNOWN_VERSION = VersionInfo(
    branch=None,
    commit_count=0,
    commit_timestamp=None,
    git_hash=None,
    is_dirty=False,
)


@cache
def get_version_info() -> VersionInfo:
    """Get version information from stamp file, git, or package metadata.

    First looks for a version_stamp.json file next to this module.
    If not found, constructs version info from git.
    If git is unavailable (e.g. running via uvx outside a repo),
    falls back to PEP 610 direct_url.json metadata, then to a placeholder.
    """
    stamp_path = Path(__file__).parent / "version_stamp.json"

    if stamp_path.exists():
        data = json.loads(stamp_path.read_text())
        return VersionInfo(**data)

    try:
        return _version_info_from_git()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        logger.debug("Git not available; trying PEP 610 metadata")

    try:
        return _version_info_from_direct_url()
    except Exception:
        logger.debug("PEP 610 metadata not available; using unknown version info")
        return _UNKNOWN_VERSION


def _version_info_from_direct_url() -> VersionInfo:
    """Extract version info from PEP 610 direct_url.json package metadata.

    When installed via ``uvx --from 'git+https://…@branch'``, the installer
    records the VCS URL and resolved commit hash in ``direct_url.json``.

    Raises ``ValueError`` if the metadata is absent or not a VCS install.
    """
    dist = importlib.metadata.distribution(_DIST_NAME)
    raw = dist.read_text("direct_url.json")
    if raw is None:
        raise ValueError("No direct_url.json in distribution metadata")

    data = json.loads(raw)
    vcs_info = data.get("vcs_info")
    if not vcs_info or vcs_info.get("vcs") != "git":
        raise ValueError("direct_url.json is not a git VCS install")

    commit_id: str = vcs_info["commit_id"]
    branch = vcs_info.get("requested_revision", "unknown")

    return VersionInfo(
        git_hash=commit_id,
        is_dirty=False,
        branch=branch,
        commit_count=0,
        commit_timestamp=None,
    )


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

    commit_timestamp = subprocess.check_output(
        ["git", "log", "-1", "--format=%cI"], text=True
    ).strip()

    return VersionInfo(
        git_hash=git_hash,
        is_dirty=is_dirty,
        branch=branch,
        commit_count=commit_count,
        commit_timestamp=commit_timestamp,
    )
