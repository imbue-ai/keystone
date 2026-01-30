"""Append-only log and cache for agent runs.

This module provides a SQLite-based logging and caching layer for the bootstrap agent.
The design philosophy is "log everything, cache selectively":

Architecture
------------
Two tables work together:

1. `cli_run` - Records every CLI invocation (both cache hits and misses)
   - Captures: UUID, timestamp, working directory, CLI arguments, whether cache hit, result
   - Purpose: Analytics on CLI usage patterns, debugging, audit trail

2. `agent_run` - Records every actual agent execution (cache misses only)
   - Captures: UUID (links to cli_run), timestamp, cache key components,
     agent output (events + devcontainer), return code, Claude JSONL log
   - Purpose: Replay cache, analytics on agent behavior, debugging

Cache Behavior
--------------
The cache key is computed from:
- Git tree hash (content-addressable snapshot of repo)
- Prompt hash (MD5 of the agent prompt)
- Agent config (JSON-serialized AgentConfig)
- Cache version (user-supplied string to force invalidation)

Cache lookup finds the most recent `agent_run` entry where:
- All cache key components match
- return_code == 0 (only successful runs are replayed)

This means failed runs are logged for analytics but never replayed.

CLI Flags
---------
- --log_db: Path to SQLite database (default: ~/.bootstrap_devcontainer/log.sqlite)
- --require_cache_hit: Fail immediately if cache miss (useful for CI/testing)
- --no_cache_replay: Skip cache lookup but still log the run (force fresh execution)
- --cache_version: String appended to cache key to invalidate old entries

Example Usage
-------------
    # Normal run with logging
    bootstrap --project_root ./myproject --log_db ./runs.db

    # Force fresh run, ignore cache
    bootstrap --project_root ./myproject --no_cache_replay

    # Require cache hit (CI mode)
    bootstrap --project_root ./myproject --require_cache_hit

    # Invalidate cache for this config
    bootstrap --project_root ./myproject --cache_version v2
"""

import base64
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_serializer, field_validator

from bootstrap_devcontainer.git_utils import get_git_tree_hash


class StreamEvent(BaseModel):
    """A single event from the agent's output stream."""

    stream: Literal["stdout", "stderr"]
    line: str


class AgentConfig(BaseModel):
    """Configuration for how the agent is run.

    This is part of the cache key - changing any field invalidates the cache.
    """

    agent_cmd: str
    max_budget_usd: float
    agent_time_limit_secs: int
    agent_in_modal: bool

    def to_cache_key_json(self) -> str:
        """Stable JSON representation for cache key computation."""
        # Sort keys for deterministic output
        return self.model_dump_json(indent=None)


class CacheKey(BaseModel):
    """Components of the cache key, stored for debugging and analytics."""

    git_tree_hash: str
    prompt_hash: str
    agent_config_json: str
    cache_version: str

    def compute_hash(self) -> str:
        """Compute the combined cache key hash."""
        h = hashlib.sha256()
        h.update(self.git_tree_hash.encode("utf-8"))
        h.update(self.prompt_hash.encode("utf-8"))
        h.update(self.agent_config_json.encode("utf-8"))
        h.update(self.cache_version.encode("utf-8"))
        return h.hexdigest()


class AgentRunRecord(BaseModel):
    """Data stored for each agent run."""

    cli_run_id: str
    timestamp: datetime
    cache_key: CacheKey
    events: list[StreamEvent]
    devcontainer_tarball: bytes
    return_code: int
    claude_jsonl: str | None = None  # Only available from Modal runs

    @field_serializer("devcontainer_tarball")
    def serialize_tarball(self, v: bytes) -> str:
        return base64.b64encode(v).decode("ascii")

    @field_validator("devcontainer_tarball", mode="before")
    @classmethod
    def deserialize_tarball(cls, v: str | bytes) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


class CLIRunRecord(BaseModel):
    """Data stored for each CLI invocation."""

    id: str
    timestamp: datetime
    cwd: str
    args: list[str]
    cache_hit: bool
    # BootstrapResult is stored as JSON string since it may evolve
    bootstrap_result_json: str | None = None


# SQL schema
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cli_run (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    cwd TEXT NOT NULL,
    args_json TEXT NOT NULL,
    cache_hit INTEGER NOT NULL,
    bootstrap_result_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cli_run_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    -- Cache key components (stored separately for analytics)
    git_tree_hash TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    agent_config_json TEXT NOT NULL,
    cache_version TEXT NOT NULL,
    cache_key_hash TEXT NOT NULL,
    -- Cached data
    events_json TEXT NOT NULL,
    devcontainer_tarball_b64 TEXT NOT NULL,
    return_code INTEGER NOT NULL,
    claude_jsonl TEXT,
    FOREIGN KEY (cli_run_id) REFERENCES cli_run(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_run_cache_key ON agent_run(cache_key_hash);
CREATE INDEX IF NOT EXISTS idx_agent_run_timestamp ON agent_run(timestamp DESC);
"""


def compute_cache_key(
    prompt: str,
    repo_path: Path,
    agent_config: AgentConfig,
    cache_version: str = "",
) -> CacheKey:
    """Compute cache key components from inputs."""
    git_tree_hash = get_git_tree_hash(repo_path)
    prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    return CacheKey(
        git_tree_hash=git_tree_hash,
        prompt_hash=prompt_hash,
        agent_config_json=agent_config.to_cache_key_json(),
        cache_version=cache_version,
    )


class AgentLog:
    """Append-only log and cache for agent runs.

    Thread-safety: This class is NOT thread-safe. Use one instance per thread.
    """

    def __init__(self, db_path: Path) -> None:
        """Open or create the log database.

        Args:
            db_path: Path to SQLite database file. Parent directories created if needed.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def generate_run_id(self) -> str:
        """Generate a unique ID for a CLI run."""
        return str(uuid.uuid4())

    def log_cli_run(self, record: CLIRunRecord) -> None:
        """Log a CLI invocation."""
        self._conn.execute(
            """
            INSERT INTO cli_run (id, timestamp, cwd, args_json, cache_hit, bootstrap_result_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.timestamp.isoformat(),
                record.cwd,
                json.dumps(record.args),
                1 if record.cache_hit else 0,
                record.bootstrap_result_json,
            ),
        )
        self._conn.commit()

    def update_cli_run_result(self, run_id: str, bootstrap_result_json: str) -> None:
        """Update a CLI run with its final result."""
        self._conn.execute(
            "UPDATE cli_run SET bootstrap_result_json = ? WHERE id = ?",
            (bootstrap_result_json, run_id),
        )
        self._conn.commit()

    def log_agent_run(self, record: AgentRunRecord) -> None:
        """Log an agent execution."""
        self._conn.execute(
            """
            INSERT INTO agent_run (
                cli_run_id, timestamp,
                git_tree_hash, prompt_hash, agent_config_json, cache_version, cache_key_hash,
                events_json, devcontainer_tarball_b64, return_code, claude_jsonl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.cli_run_id,
                record.timestamp.isoformat(),
                record.cache_key.git_tree_hash,
                record.cache_key.prompt_hash,
                record.cache_key.agent_config_json,
                record.cache_key.cache_version,
                record.cache_key.compute_hash(),
                json.dumps([e.model_dump() for e in record.events]),
                base64.b64encode(record.devcontainer_tarball).decode("ascii"),
                record.return_code,
                record.claude_jsonl,
            ),
        )
        self._conn.commit()

    def lookup_cache(self, cache_key: CacheKey) -> AgentRunRecord | None:
        """Find most recent successful agent run matching the cache key.

        Returns None if no matching successful run exists.
        Only runs with return_code == 0 are considered for replay.
        """
        cursor = self._conn.execute(
            """
            SELECT cli_run_id, timestamp,
                   git_tree_hash, prompt_hash, agent_config_json, cache_version,
                   events_json, devcontainer_tarball_b64, return_code, claude_jsonl
            FROM agent_run
            WHERE cache_key_hash = ? AND return_code = 0
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (cache_key.compute_hash(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        events_data = json.loads(row[6])
        return AgentRunRecord(
            cli_run_id=row[0],
            timestamp=datetime.fromisoformat(row[1]),
            cache_key=CacheKey(
                git_tree_hash=row[2],
                prompt_hash=row[3],
                agent_config_json=row[4],
                cache_version=row[5],
            ),
            events=[StreamEvent(**e) for e in events_data],
            devcontainer_tarball=base64.b64decode(row[7]),
            return_code=row[8],
            claude_jsonl=row[9],
        )


# Legacy compatibility - re-export for existing code
# TODO: Remove after migration
def create_devcontainer_tarball(project_root: Path) -> bytes:
    """Create a gzipped tarball of the .devcontainer directory."""
    import io
    import tarfile

    devcontainer_dir = project_root / ".devcontainer"
    if not devcontainer_dir.exists():
        return b""

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(devcontainer_dir, arcname=".devcontainer")
    return buf.getvalue()


def extract_devcontainer_tarball(tarball: bytes, project_root: Path) -> None:
    """Extract a .devcontainer tarball to the project root."""
    import io
    import tarfile

    if not tarball:
        return

    buf = io.BytesIO(tarball)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        tar.extractall(project_root)
