"""Append-only log and cache for agent runs.

This module provides a database-backed logging and caching layer for the bootstrap agent.
The design philosophy is "log everything, cache selectively":

Architecture
------------
Two tables work together:

1. `cli_run` - Records every CLI invocation (both cache hits and misses)
   - Captures: UUID, timestamp, working directory, CLI arguments, whether cache hit, result
   - Purpose: Analytics on CLI usage patterns, debugging, audit trail

2. `agent_run` - Records every actual agent execution (cache misses only)
   - Captures: UUID (links to cli_run), timestamp, cache key components,
     agent output (events + devcontainer), return code, Claude state tarball
   - Purpose: Replay cache, analytics on agent behavior, debugging

Database Support
----------------
Supports both SQLite and PostgreSQL via SQLAlchemy + pandas:
- SQLite: Pass a file path (e.g., "./runs.db" or "~/.imbue_keystone/log.sqlite")
- PostgreSQL: Pass a connect string (e.g., "postgresql://user:pass@host/db")

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
- --log_db: Database path or connect string (default: ~/.imbue_keystone/log.sqlite)
- --require_cache_hit: Fail immediately if cache miss (useful for CI/testing)
- --no_cache_replay: Skip cache lookup but still log the run (force fresh execution)
- --cache_version: String appended to cache key to invalidate old entries

Example Usage
-------------
    # SQLite (default)
    bootstrap --project_root ./myproject --log_db ./runs.db

    # PostgreSQL
    bootstrap --project_root ./myproject --log_db postgresql://user:pass@localhost/mydb

    # Force fresh run, ignore cache
    bootstrap --project_root ./myproject --no_cache_replay

    # Require cache hit (CI mode)
    bootstrap --project_root ./myproject --require_cache_hit

    # Invalidate cache for this config
    bootstrap --project_root ./myproject --cache_version v2
"""

import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from bootstrap_devcontainer.git_utils import get_git_tree_hash
from bootstrap_devcontainer.schema import AgentConfig
from bootstrap_devcontainer.version import VersionInfo, get_version_info


class StreamEvent(BaseModel):
    """A single event from the agent's output stream."""

    stream: Literal["stdout", "stderr"]
    line: str


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
    claude_dir_tarball: bytes | None = None  # Tarball of ~/.claude from Modal
    version_info: VersionInfo | None = None  # Version of the code that ran


class CLIRunRecord(BaseModel):
    """Data stored for each CLI invocation."""

    id: str
    timestamp: datetime
    cwd: str
    args: list[str]
    cache_hit: bool
    bootstrap_result_json: str | None = None


def compute_cache_key(
    prompt: str,
    repo_path: Path,
    agent_config: AgentConfig,
    cache_version: str,
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


def ensure_column_exists(engine: Engine, table: str, column: str, column_type: str) -> None:
    """Add a column to a table if it doesn't exist.

    Does nothing if the table doesn't exist (table will be created with the column).

    Args:
        engine: SQLAlchemy engine
        table: Table name
        column: Column name to add
        column_type: SQL type for the column (e.g., 'TEXT', 'INTEGER')
    """
    with engine.connect() as conn:
        # Check if column exists (works for SQLite and PostgreSQL)
        if engine.dialect.name == "sqlite":
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            columns = {row[1] for row in result}
            if not columns:
                # Table doesn't exist yet
                return
        else:
            # PostgreSQL
            result = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = :table"
                ),
                {"table": table},
            )
            columns = {row[0] for row in result}
            if not columns:
                # Table doesn't exist yet
                return

        if column not in columns:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))
            conn.commit()


def _create_engine(db_url: str) -> Engine:
    """Create SQLAlchemy engine from URL or path.

    Args:
        db_url: Either a SQLAlchemy URL (postgresql://...) or a file path for SQLite.
    """
    if db_url.startswith(("postgresql://", "postgres://", "sqlite://")):
        return create_engine(db_url)
    else:
        # Treat as SQLite file path
        path = Path(db_url).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return create_engine(f"sqlite:///{path}")


class AgentLog:
    """Append-only log and cache for agent runs.

    Uses pandas + SQLAlchemy for database operations, supporting both
    SQLite and PostgreSQL.

    Thread-safety: This class is NOT thread-safe. Use one instance per thread.
    """

    def __init__(self, db_url: str) -> None:
        """Open or create the log database.

        Args:
            db_url: Database URL or file path.
                - File path: Creates/opens SQLite database
                - postgresql://...: Connects to PostgreSQL
        """
        self._engine = _create_engine(db_url)

    def close(self) -> None:
        """Close the database connection."""
        self._engine.dispose()

    def generate_run_id(self) -> str:
        """Generate a unique ID for a CLI run."""
        return str(uuid.uuid4())

    def log_cli_run(self, record: CLIRunRecord) -> None:
        """Log a CLI invocation."""
        df = pd.DataFrame(
            [
                {
                    "id": record.id,
                    "timestamp": record.timestamp.isoformat(),
                    "cwd": record.cwd,
                    "args_json": json.dumps(record.args),
                    "cache_hit": record.cache_hit,
                    "bootstrap_result_json": record.bootstrap_result_json,
                }
            ]
        )
        df.to_sql("cli_run", self._engine, if_exists="append", index=False)

    def log_agent_run(self, record: AgentRunRecord) -> None:
        """Log an agent execution."""
        # Ensure version_info column exists (schema migration)
        ensure_column_exists(self._engine, "agent_run", "version_info_json", "TEXT")

        # Use current version if not provided
        version_info = record.version_info or get_version_info()

        df = pd.DataFrame(
            [
                {
                    "cli_run_id": record.cli_run_id,
                    "timestamp": record.timestamp.isoformat(),
                    "git_tree_hash": record.cache_key.git_tree_hash,
                    "prompt_hash": record.cache_key.prompt_hash,
                    "agent_config_json": record.cache_key.agent_config_json,
                    "cache_version": record.cache_key.cache_version,
                    "cache_key_hash": record.cache_key.compute_hash(),
                    "events_json": json.dumps([e.model_dump() for e in record.events]),
                    "devcontainer_tarball": record.devcontainer_tarball,
                    "return_code": record.return_code,
                    "claude_dir_tarball": record.claude_dir_tarball,
                    "version_info_json": version_info.model_dump_json(),
                }
            ]
        )
        df.to_sql("agent_run", self._engine, if_exists="append", index=False)

    def lookup_cache(self, cache_key: CacheKey) -> AgentRunRecord | None:
        """Find most recent successful agent run matching the cache key.

        Returns None if no matching successful run exists.
        Only runs with return_code == 0 are considered for replay.
        """
        cache_hash = cache_key.compute_hash()
        query = text("""
            SELECT cli_run_id, timestamp,
                   git_tree_hash, prompt_hash, agent_config_json, cache_version,
                   events_json, devcontainer_tarball, return_code, claude_dir_tarball
            FROM agent_run
            WHERE cache_key_hash = :cache_hash AND return_code = 0
            ORDER BY timestamp DESC
            LIMIT 1
        """)

        try:
            with self._engine.connect() as conn:
                result = conn.execute(query, {"cache_hash": cache_hash})
                row = result.fetchone()
        except Exception:
            # Table may not exist yet (first run)
            return None

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
            devcontainer_tarball=row[7],
            return_code=row[8],
            claude_dir_tarball=row[9],
        )


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
