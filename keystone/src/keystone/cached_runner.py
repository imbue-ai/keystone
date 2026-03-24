"""Cached agent runner wrapper.

Wraps any AgentRunner with transparent cache lookup/storage, so the caller
doesn't need to branch on cache hit vs. miss. Inspired by imbue_core's
CachedAgentClient pattern.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from keystone.agent_log import (
    AgentLog,
    AgentRunRecord,
    CacheKey,
    extract_devcontainer_tarball,
)
from keystone.agent_runner import TIMEOUT_EXIT_CODE, AgentRunner

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from keystone.llm_provider import AgentProvider
    from keystone.schema import AgentConfig, InferenceCost, StreamEvent, VerificationResult

logger = logging.getLogger(__name__)

# ANSI color codes (duplicated from constants to avoid circular import)
_ANSI_GREEN = "\033[32m"
_ANSI_MAGENTA = "\033[35m"
_ANSI_RESET = "\033[0m"


class CachedAgentRunner(AgentRunner):
    """Wraps an AgentRunner with transparent caching.

    On ``run()``:
    - If cache hit: replays stored events, restores tarball, no agent invocation.
    - If cache miss: delegates to the inner runner, records everything, saves to DB.

    The caller sees the same ``Iterator[StreamEvent]`` either way.
    """

    def __init__(
        self,
        inner: AgentRunner,
        agent_log: AgentLog,
        cache_key: CacheKey,
        cli_run_id: str,
        project_root: Path,
        *,
        no_cache_replay: bool = False,
        require_cache_hit: bool = False,
    ) -> None:
        self._inner = inner
        self._agent_log = agent_log
        self._cache_key = cache_key
        self._cli_run_id = cli_run_id
        self._project_root = project_root
        self._no_cache_replay = no_cache_replay
        self._require_cache_hit = require_cache_hit

        # State set during/after run()
        self._exit_code: int = 1
        self._devcontainer_tarball: bytes = b""
        self._cache_hit: bool = False
        self._timed_out: bool = False

    @property
    def exit_code(self) -> int:
        return self._exit_code

    @property
    def cache_hit(self) -> bool:
        """Whether the last run() was served from cache."""
        return self._cache_hit

    @property
    def timed_out(self) -> bool:
        """Whether the agent timed out."""
        return self._timed_out

    @property
    def cost_limit_exceeded(self) -> bool:
        """Whether the agent was terminated for exceeding the cost limit."""
        return getattr(self._inner, "cost_limit_exceeded", False)

    def run(
        self,
        prompt: str,
        project_archive: bytes,
        agent_config: AgentConfig,
        provider: AgentProvider,
        agents_md: str | None = None,
    ) -> Iterator[StreamEvent]:
        # Try cache lookup
        cached_run: AgentRunRecord | None = None
        if not self._no_cache_replay:
            cached_run = self._agent_log.lookup_cache(self._cache_key)

        if self._require_cache_hit and cached_run is None:
            raise CacheMissError("--require_cache_hit specified but cache miss")

        if cached_run is not None:
            # Cache hit path
            yield from self._replay_cached(cached_run)
        else:
            # Cache miss path
            yield from self._run_and_record(
                prompt, project_archive, agent_config, provider, agents_md=agents_md
            )

    def _replay_cached(self, cached_run: AgentRunRecord) -> Iterator[StreamEvent]:
        """Replay a cached run's events."""
        self._cache_hit = True
        self._exit_code = cached_run.return_code
        self._devcontainer_tarball = cached_run.devcontainer_tarball

        log_db_info = "cache"
        print(
            f"{_ANSI_GREEN}CACHE HIT: Replaying cached agent output from {log_db_info}{_ANSI_RESET}",
            file=sys.stderr,
        )
        print(
            f"  Cached return_code: {cached_run.return_code}, "
            f"events: {len(cached_run.events)}, "
            f"tarball size: {len(cached_run.devcontainer_tarball)} bytes",
            file=sys.stderr,
        )

        extract_devcontainer_tarball(cached_run.devcontainer_tarball, self._project_root)
        yield from cached_run.events

    def _run_and_record(
        self,
        prompt: str,
        project_archive: bytes,
        agent_config: AgentConfig,
        provider: AgentProvider,
        agents_md: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Run the agent for real and record the result."""
        self._cache_hit = False

        if self._no_cache_replay:
            print(
                f"{_ANSI_MAGENTA}CACHE BYPASS (--no_cache_replay): Running agent{_ANSI_RESET}",
                file=sys.stderr,
            )
        else:
            print(
                f"{_ANSI_MAGENTA}CACHE MISS: Running agent{_ANSI_RESET}",
                file=sys.stderr,
            )

        collected_events: list[StreamEvent] = []
        try:
            for event in self._inner.run(
                prompt, project_archive, agent_config, provider, agents_md=agents_md
            ):
                collected_events.append(event)
                yield event

            self._exit_code = self._inner.exit_code
            if self._exit_code == TIMEOUT_EXIT_CODE:
                self._timed_out = True

            # Extract and log
            try:
                self._devcontainer_tarball = self._inner.get_devcontainer_tarball()
                extract_devcontainer_tarball(self._devcontainer_tarball, self._project_root)

                agent_dir_tarball = self._inner.get_agent_dir_tarball()

                agent_run_record = AgentRunRecord(
                    cli_run_id=self._cli_run_id,
                    timestamp=datetime.now(UTC),
                    cache_key=self._cache_key,
                    events=collected_events,
                    devcontainer_tarball=self._devcontainer_tarball,
                    return_code=self._exit_code,
                    agent_dir_tarball=agent_dir_tarball,
                )
                self._agent_log.log_agent_run(agent_run_record)

                if self._exit_code != 0:
                    print(
                        f"Agent failed (exit_code={self._exit_code}), "
                        "logged but not cached for replay",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(f"Warning: could not extract/log .devcontainer: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error running agent: {e}", file=sys.stderr)
            self._exit_code = 1

    def get_devcontainer_tarball(self) -> bytes:
        return self._devcontainer_tarball

    def verify(
        self,
        project_archive: bytes,
        devcontainer_tarball: bytes,
        test_artifacts_dir: Path,
        image_build_timeout_seconds: int,
        test_timeout_seconds: int,
    ) -> VerificationResult:
        """Delegate verification to inner runner."""
        return self._inner.verify(
            project_archive,
            devcontainer_tarball,
            test_artifacts_dir,
            image_build_timeout_seconds,
            test_timeout_seconds,
        )

    def cleanup(self) -> None:
        """Delegate cleanup to inner runner."""
        self._inner.cleanup()

    def get_agent_dir_tarball(self) -> bytes | None:
        """Delegate to inner runner (only available on cache miss)."""
        if self._cache_hit:
            return None
        return self._inner.get_agent_dir_tarball()

    def get_inference_cost(self, provider_name: str) -> InferenceCost | None:
        """Delegate to inner runner (only available on cache miss)."""
        if self._cache_hit:
            return None
        return self._inner.get_inference_cost(provider_name)


class CacheMissError(Exception):
    """Raised when --require_cache_hit is set but no cache entry exists."""
