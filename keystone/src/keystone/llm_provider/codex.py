"""OpenAI Codex CLI provider implementation.

Reference:
    https://github.com/openai/codex/blob/main/docs/exec.md
    https://github.com/openai/codex/blob/main/sdk/typescript/src/events.ts
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


class CodexProvider(AgentProvider):
    """Provider for the ``codex`` CLI (OpenAI Codex)."""

    @property
    def name(self) -> str:
        return "codex"

    @property
    def default_cmd(self) -> str:
        return "codex"

    def build_command(
        self,
        prompt: str,
        max_budget_usd: float,  # noqa: ARG002  # required by interface
        agent_cmd: str,
    ) -> list[str]:
        cmd = [
            *shlex.split(agent_cmd),
            *((f"--model={self.model}",) if self.model else ()),
            "exec",
            "--sandbox",
            "danger-full-access",
            "--skip-git-repo-check",
            "--json",
            prompt,
        ]
        return cmd

    def parse_stdout_line(self, line: str) -> list[AgentEvent]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []

        event_type = data.get("type")
        events: list[AgentEvent] = []

        if event_type == "turn.completed":
            usage = data.get("usage", {})
            events.append(
                AgentCostEvent(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cached_tokens=usage.get("cached_input_tokens", 0),
                )
            )

        elif event_type == "turn.failed":
            error = data.get("error", {})
            events.append(AgentErrorEvent(message=error.get("message", "Unknown error")))

        elif event_type in ("item.started", "item.completed"):
            events.extend(self._parse_item(data.get("item", {})))

        elif event_type == "thread.started":
            # Session lifecycle event; no action needed
            pass

        elif event_type == "error":
            events.append(AgentErrorEvent(message=data.get("message", "Unknown error")))

        return events

    def _parse_item(self, item: dict) -> list[AgentEvent]:
        """Parse a Codex thread item into agent events."""
        item_type = item.get("type")
        events: list[AgentEvent] = []

        if item_type == "agent_message":
            text = item.get("text", "").strip()
            if text:
                events.append(AgentTextEvent(text=text))

        elif item_type == "command_execution":
            status = item.get("status")
            if status == "in_progress":
                events.append(
                    AgentToolCallEvent(
                        name="bash",
                        input={"command": item.get("command", "")},
                    )
                )
            else:
                events.append(
                    AgentToolResultEvent(
                        tool_name="bash",
                        output=item.get("aggregated_output", ""),
                        exit_code=item.get("exit_code"),
                    )
                )

        elif item_type == "file_change":
            changes = item.get("changes", [])
            events.append(
                AgentToolCallEvent(
                    name="file_change",
                    input={"changes": changes},
                )
            )

        elif item_type == "reasoning":
            # Reasoning / thinking; skip for now
            pass

        elif item_type == "error":
            events.append(AgentErrorEvent(message=item.get("message", "Unknown error")))

        return events

    def env_vars(self) -> dict[str, str]:
        key = os.environ.get("OPENAI_API_KEY", "")
        # CODEX_API_KEY is read directly by ``codex exec`` for authentication,
        # avoiding the need for a separate ``codex login`` step.
        return {"OPENAI_API_KEY": key, "CODEX_API_KEY": key} if key else {}
