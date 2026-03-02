"""OpenCode CLI provider implementation.

Reference:
    https://opencode.ai/docs/cli/
    https://github.com/opencode-ai/opencode
"""

from __future__ import annotations

import json
import os
import shlex

from keystone.llm_provider.base import (
    AgentCostEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentProvider,
    AgentTextEvent,
    AgentToolCallEvent,
    AgentToolResultEvent,
)


class OpencodeProvider(AgentProvider):
    """Provider for the ``opencode`` CLI (OpenCode AI).

    OpenCode is an open-source terminal coding agent that supports 75+ models
    across many providers (Anthropic, OpenAI, Google, etc.).  In non-interactive
    ``run`` mode, all permissions are auto-approved and output can be streamed
    as JSON lines with ``--format json``.

    OpenCode does not have a built-in ``--max-budget-usd`` flag — budget is
    managed externally via the timeout wrapper that keystone already provides.
    """

    @property
    def name(self) -> str:
        return "opencode"

    @property
    def default_cmd(self) -> str:
        return "opencode"

    def build_command(
        self,
        prompt: str,
        max_budget_usd: float,  # noqa: ARG002  # no budget flag in opencode
        agent_cmd: str,
    ) -> list[str]:
        cmd = [
            *shlex.split(agent_cmd),
            "run",
            "--format",
            "json",
        ]
        if self.model:
            cmd.extend(("--model", self.model))
        cmd.append(prompt)
        return cmd

    def parse_stdout_line(self, line: str) -> list[AgentEvent]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []

        event_type = data.get("type")
        events: list[AgentEvent] = []

        # ── message events ────────────────────────────────────────────
        if event_type == "message.part.updated":
            events.extend(self._parse_message_part(data.get("part", {})))

        elif event_type == "message.completed":
            # End of a full message; extract cost/usage if present
            usage = data.get("usage", {})
            if usage:
                events.append(
                    AgentCostEvent(
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        cached_tokens=usage.get("cache_read_input_tokens", 0),
                    )
                )

        # ── session lifecycle ─────────────────────────────────────────
        elif event_type == "session.completed":
            usage = data.get("usage", {})
            if usage:
                events.append(
                    AgentCostEvent(
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        cached_tokens=usage.get("cache_read_input_tokens", 0),
                    )
                )

        # ── errors ────────────────────────────────────────────────────
        elif event_type == "error":
            events.append(AgentErrorEvent(message=data.get("message", "Unknown error")))

        return events

    # ── helpers ────────────────────────────────────────────────────────

    def _parse_message_part(self, part: dict) -> list[AgentEvent]:
        """Parse a message part into typed events."""
        part_type = part.get("type")
        events: list[AgentEvent] = []

        if part_type == "text":
            text = part.get("text", "").strip()
            if text:
                events.append(AgentTextEvent(text=text))

        elif part_type == "thinking":
            # Internal reasoning; skip
            pass

        elif part_type == "tool":
            tool_name = part.get("name", "")
            tool_input = part.get("input", {})
            status = part.get("status")

            if status in (None, "in_progress", "running"):
                events.append(AgentToolCallEvent(name=tool_name, input=tool_input))
            else:
                # Tool completed — emit result
                events.append(
                    AgentToolResultEvent(
                        tool_name=tool_name,
                        output=part.get("output", ""),
                        exit_code=part.get("exit_code"),
                    )
                )

        elif part_type == "error":
            events.append(AgentErrorEvent(message=part.get("message", "Unknown error")))

        return events

    def env_vars(self) -> dict[str, str]:
        """Pass through API keys for whichever backend OpenCode is configured to use.

        OpenCode reads the standard provider env vars directly, so we forward
        all keys that are present in the environment.
        """
        env: dict[str, str] = {}
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "OPENROUTER_API_KEY",
        ):
            val = os.environ.get(var, "")
            if val:
                env[var] = val
        return env
