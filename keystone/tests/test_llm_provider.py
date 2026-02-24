"""Tests for the LLM provider abstraction."""

import json

import pytest

from keystone.llm_provider import (
    AgentCostEvent,
    AgentErrorEvent,
    AgentTextEvent,
    AgentToolCallEvent,
    AgentToolResultEvent,
    get_provider,
)
from keystone.llm_provider.claude import ClaudeProvider
from keystone.llm_provider.codex import CodexProvider

# ── Claude provider ───────────────────────────────────────────────────


class TestClaudeProvider:
    def setup_method(self) -> None:
        self.provider = ClaudeProvider()

    def test_name_and_default_cmd(self) -> None:
        assert self.provider.name == "claude"
        assert self.provider.default_cmd == "claude"

    def test_build_command(self) -> None:
        cmd = self.provider.build_command("Fix the bug", 5.0, "claude")
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd
        assert "stream-json" in cmd
        assert "5.0" in cmd
        assert "Fix the bug" in cmd

    def test_build_command_with_model(self) -> None:
        self.provider.model = "claude-opus-4-6"
        cmd = self.provider.build_command("Fix the bug", 5.0, "claude")
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "claude-opus-4-6"

    def test_parse_assistant_text(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello world"}]},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentTextEvent)
        assert events[0].text == "Hello world"

    def test_parse_assistant_text_and_tool(self) -> None:
        """One line can produce multiple events."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I'll run a command"},
                        {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}},
                    ],
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 2
        assert isinstance(events[0], AgentTextEvent)
        assert isinstance(events[1], AgentToolCallEvent)
        assert events[1].name == "bash"

    def test_parse_result(self) -> None:
        line = json.dumps(
            {
                "type": "result",
                "total_cost_usd": 0.42,
                "model": "claude-sonnet-4-20250514",
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 50,
                    "output_tokens": 200,
                    "cache_creation_input_tokens": 10,
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        cost = events[0]
        assert isinstance(cost, AgentCostEvent)
        assert cost.cost_usd == 0.42
        assert cost.model == "claude-sonnet-4-20250514"
        assert cost.input_tokens == 100
        assert cost.cached_tokens == 50
        assert cost.output_tokens == 200
        assert cost.cache_creation_tokens == 10

    def test_parse_non_json(self) -> None:
        events = self.provider.parse_stdout_line("not json at all")
        assert events == []

    def test_parse_unknown_type(self) -> None:
        line = json.dumps({"type": "system", "data": "something"})
        events = self.provider.parse_stdout_line(line)
        assert events == []

    def test_env_vars_empty(self) -> None:
        assert self.provider.env_vars() == {}


# ── Codex provider ────────────────────────────────────────────────────


class TestCodexProvider:
    def setup_method(self) -> None:
        self.provider = CodexProvider()

    def test_name_and_default_cmd(self) -> None:
        assert self.provider.name == "codex"
        assert self.provider.default_cmd == "codex"

    def test_build_command(self) -> None:
        cmd = self.provider.build_command("Fix the bug", 5.0, "codex")
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "--json" in cmd
        assert "danger-full-access" in cmd
        assert "Fix the bug" in cmd

    def test_build_command_with_model(self) -> None:
        self.provider.model = "gpt-5.2-codex"
        cmd = self.provider.build_command("Fix the bug", 5.0, "codex")
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "gpt-5.2-codex"
        # --model should come after exec but before --sandbox
        exec_idx = cmd.index("exec")
        sandbox_idx = cmd.index("--sandbox")
        assert exec_idx < model_idx < sandbox_idx

    def test_parse_turn_completed(self) -> None:
        line = json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "output_tokens": 200, "cached_input_tokens": 50},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        cost = events[0]
        assert isinstance(cost, AgentCostEvent)
        assert cost.cost_usd is None  # Codex doesn't report dollar cost
        assert cost.input_tokens == 100
        assert cost.output_tokens == 200
        assert cost.cached_tokens == 50

    def test_parse_turn_failed(self) -> None:
        line = json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": "Rate limit exceeded"},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentErrorEvent)
        assert events[0].message == "Rate limit exceeded"

    def test_parse_item_agent_message(self) -> None:
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "id": "1", "text": "I'll fix this"},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentTextEvent)
        assert events[0].text == "I'll fix this"

    def test_parse_item_command_in_progress(self) -> None:
        line = json.dumps(
            {
                "type": "item.started",
                "item": {
                    "type": "command_execution",
                    "id": "1",
                    "command": "ls -la",
                    "aggregated_output": "",
                    "status": "in_progress",
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentToolCallEvent)
        assert events[0].name == "bash"
        assert events[0].input == {"command": "ls -la"}

    def test_parse_item_command_completed(self) -> None:
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "id": "1",
                    "command": "ls",
                    "aggregated_output": "file1.py\nfile2.py",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentToolResultEvent)
        assert events[0].tool_name == "bash"
        assert events[0].exit_code == 0

    def test_parse_item_file_change(self) -> None:
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "id": "1",
                    "changes": [{"path": "foo.py", "kind": "update"}],
                    "status": "completed",
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentToolCallEvent)
        assert events[0].name == "file_change"

    def test_parse_thread_started(self) -> None:
        line = json.dumps({"type": "thread.started", "thread_id": "abc123"})
        events = self.provider.parse_stdout_line(line)
        assert events == []

    def test_parse_error_event(self) -> None:
        line = json.dumps({"type": "error", "message": "Something went wrong"})
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentErrorEvent)

    def test_parse_non_json(self) -> None:
        events = self.provider.parse_stdout_line("not json")
        assert events == []


# ── Registry ──────────────────────────────────────────────────────────


class TestProviderRegistry:
    def test_get_claude(self) -> None:
        p = get_provider("claude")
        assert isinstance(p, ClaudeProvider)

    def test_get_codex(self) -> None:
        p = get_provider("codex")
        assert isinstance(p, CodexProvider)

    def test_get_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_provider("nonexistent")
