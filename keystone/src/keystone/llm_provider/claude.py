"""Claude Code provider implementation."""

from __future__ import annotations

import json
import shlex

from keystone.llm_provider.base import (
    AgentCostEvent,
    AgentEvent,
    AgentProvider,
    AgentTextEvent,
    AgentToolCallEvent,
)


class ClaudeProvider(AgentProvider):
    """Provider for the ``claude`` CLI (Claude Code)."""

    @property
    def name(self) -> str:
        return "claude"

    @property
    def default_cmd(self) -> str:
        return "claude"

    def build_command(
        self,
        prompt: str,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> list[str]:
        return [
            *shlex.split(agent_cmd),
            "--dangerously-skip-permissions",
            *("--output-format", "stream-json"),
            "--verbose",
            *("--max-budget-usd", str(max_budget_usd)),
            *("-p", prompt),
        ]

    def parse_stdout_line(self, line: str) -> list[AgentEvent]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []

        events: list[AgentEvent] = []
        msg_type = data.get("type")

        if msg_type == "assistant":
            for item in data.get("message", {}).get("content", []):
                if item.get("type") == "text":
                    txt = item.get("text", "").strip()
                    if txt:
                        events.append(AgentTextEvent(text=txt))
                elif item.get("type") == "tool_use":
                    events.append(
                        AgentToolCallEvent(
                            name=item.get("name", ""),
                            input=item.get("input", {}),
                        )
                    )

        elif msg_type == "result":
            usage = data.get("usage", {})
            events.append(
                AgentCostEvent(
                    cost_usd=data.get("total_cost_usd", 0.0),
                    model=data.get("model", ""),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cached_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                )
            )

        return events
