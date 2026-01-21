"""Caching layer for agent calls to speed up testing."""

import base64
import hashlib
import io
import tarfile
import threading
from pathlib import Path
from typing import Literal

import diskcache
from pydantic import BaseModel, field_serializer, field_validator


class StreamEvent(BaseModel):
    """A single event from the agent's output stream."""
    stream: Literal["stdout", "stderr"]
    line: str


class CacheValue(BaseModel):
    """Cached result from an agent run."""
    events: list[StreamEvent]
    devcontainer_tarball: bytes  # gzipped tar of .devcontainer directory
    return_code: int

    @field_serializer("devcontainer_tarball")
    def serialize_tarball(self, v: bytes) -> str:
        return base64.b64encode(v).decode("ascii")

    @field_validator("devcontainer_tarball", mode="before")
    @classmethod
    def deserialize_tarball(cls, v: str | bytes) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


def compute_directory_hash(directory: Path) -> str:
    """Compute MD5 hash of all files in a directory (sorted by path)."""
    h = hashlib.md5()
    
    all_files = sorted(directory.rglob("*"))
    for file_path in all_files:
        if file_path.is_file():
            rel_path = file_path.relative_to(directory)
            h.update(str(rel_path).encode("utf-8"))
            h.update(file_path.read_bytes())
    
    return h.hexdigest()


def compute_cache_key(prompt: str, directory: Path) -> str:
    """Compute cache key from prompt and directory contents."""
    dir_hash = compute_directory_hash(directory)
    h = hashlib.md5()
    h.update(prompt.encode("utf-8"))
    h.update(dir_hash.encode("utf-8"))
    return h.hexdigest()


def create_devcontainer_tarball(project_root: Path) -> bytes:
    """Create a gzipped tarball of the .devcontainer directory."""
    devcontainer_dir = project_root / ".devcontainer"
    if not devcontainer_dir.exists():
        return b""
    
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(devcontainer_dir, arcname=".devcontainer")
    return buf.getvalue()


def extract_devcontainer_tarball(tarball: bytes, project_root: Path) -> None:
    """Extract a .devcontainer tarball to the project root."""
    if not tarball:
        return
    
    buf = io.BytesIO(tarball)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        tar.extractall(project_root)


class EventCollector:
    """Thread-safe collector for stream events."""
    
    def __init__(self) -> None:
        self._events: list[StreamEvent] = []
        self._lock = threading.Lock()
    
    def add(self, stream: Literal["stdout", "stderr"], line: str) -> None:
        with self._lock:
            self._events.append(StreamEvent(stream=stream, line=line))
    
    def get_events(self) -> list[StreamEvent]:
        with self._lock:
            return list(self._events)


class AgentCache:
    """Cache for agent run results."""
    
    def __init__(self, cache_path: Path) -> None:
        self._cache = diskcache.Cache(str(cache_path))
    
    def get(self, key: str) -> CacheValue | None:
        data = self._cache.get(key)
        if data is None:
            return None
        return CacheValue.model_validate_json(data)
    
    def set(self, key: str, value: CacheValue) -> None:
        self._cache.set(key, value.model_dump_json())
    
    def close(self) -> None:
        self._cache.close()
