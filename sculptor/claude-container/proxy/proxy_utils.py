"""Common utilities shared between proxy implementations.

This module contains shared code for the claude_code_proxy and codex_proxy.
"""

import json
import os
from typing import Any

# Path to the proxy cache file
LOG_FILE_PATH = "/tmp/proxy_logs.txt"
PROXY_CACHE_PATH = os.environ.get("SNAPSHOT_PATH", "/imbue_addons/.proxy_cache.db")


def existing_snapshots_provided() -> bool:
    """If the proxy was initialized with existing collection of snapshots.

    We must not call remote servers if this is true.
    """
    return "SNAPSHOT_PATH" in os.environ


class CacheMissError(Exception):
    """Raised when a cache miss occurs in snapshot mode."""

    pass


class JsonCache:
    def __init__(self, path: str) -> None:
        self.path = path
        open(self.path, "a").close()

    def get(self, key: str) -> Any | None:
        result = None
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f.readlines():
                try:
                    record = json.loads(line)
                    if record.get("key") == key:
                        result = record.get("value")
                except Exception:
                    pass
        return result

    def set(self, key: str, value: Any) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "value": value}) + "\n")


def safe_to_dict(obj: object) -> Any:
    """Safely convert object to dictionary for serialization.

    Handles various object types including Pydantic models, dataclasses,
    and plain objects.

    Args:
        obj: The object to convert.

    Returns:
        A JSON-serializable representation of the object.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool, list, dict)):
        return obj
    if hasattr(obj, "dict"):
        # Pydantic v1 style
        return obj.dict()  # pyre-ignore[16]
    if hasattr(obj, "model_dump"):
        # Pydantic v2 style
        return obj.model_dump()  # pyre-ignore[16]
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def remove_none_values(obj: object) -> Any:
    """Recursively remove None values from dicts and lists.

    Args:
        obj: The object to clean.

    Returns:
        The object with None values removed.
    """
    if isinstance(obj, dict):
        return {k: remove_none_values(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [remove_none_values(item) for item in obj if item is not None]
    else:
        return obj
