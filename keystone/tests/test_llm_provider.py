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
from keystone.llm_provider.opencode import OpencodeProvider
from keystone.schema import AgentConfig, LLMModel


def _make_config(**overrides: object) -> AgentConfig:
    """Create a minimal AgentConfig for testing, with sensible defaults."""
    defaults: dict[str, object] = {
        "max_budget_usd": 1.0,
        "agent_time_limit_seconds": 300,
        "agent_in_modal": False,
        "provider": "claude",
        "guardrail": False,
        "use_agents_md": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]

# ── Claude provider ───────────────────────────────────────────────────


class TestClaudeProvider:
    def setup_method(self) -> None:
        self.config = _make_config(
            provider="claude",
            model=LLMModel.OPUS,
            claude_reasoning_level="medium",
        )
        self.provider = ClaudeProvider(config=self.config)

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
        # model and reasoning are always present
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == LLMModel.OPUS.value
        assert "--reasoning" in cmd
        reasoning_idx = cmd.index("--reasoning")
        assert cmd[reasoning_idx + 1] == "medium"

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

    def test_env_vars_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert self.provider.env_vars() == {}


# ── Codex provider ────────────────────────────────────────────────────


class TestCodexProvider:
    def setup_method(self) -> None:
        self.config = _make_config(
            provider="codex",
            model=LLMModel.CODEX,
            codex_reasoning_level="high",
        )
        self.provider = CodexProvider(config=self.config)

    def test_name_and_default_cmd(self) -> None:
        assert self.provider.name == "codex"
        assert self.provider.default_cmd == "codex"

    def test_build_command(self) -> None:
        cmd = self.provider.build_command("Fix the bug", 5.0, "codex")
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "--json" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "Fix the bug" in cmd
        # model and reasoning are always present
        assert f"--model={LLMModel.CODEX.value}" in cmd
        model_idx = cmd.index(f"--model={LLMModel.CODEX.value}")
        exec_idx = cmd.index("exec")
        assert model_idx < exec_idx
        assert "--config" in cmd
        config_idx = cmd.index("--config")
        assert cmd[config_idx + 1] == "model_reasoning_effort=high"
        assert config_idx < exec_idx

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
        config = _make_config(provider="claude", model=LLMModel.OPUS, claude_reasoning_level="high")
        p = get_provider(config)
        assert isinstance(p, ClaudeProvider)

    def test_get_codex(self) -> None:
        config = _make_config(provider="codex", model=LLMModel.CODEX, codex_reasoning_level="high")
        p = get_provider(config)
        assert isinstance(p, CodexProvider)

    def test_get_opencode(self) -> None:
        config = _make_config(provider="opencode", model=LLMModel.OPENCODE_OPUS)
        p = get_provider(config)
        assert isinstance(p, OpencodeProvider)

    def test_get_opencode_with_model(self) -> None:
        config = _make_config(provider="opencode", model=LLMModel.OPENCODE_OPUS)
        p = get_provider(config)
        assert isinstance(p, OpencodeProvider)
        assert p.config.model == LLMModel.OPENCODE_OPUS

    def test_get_unknown(self) -> None:
        config = _make_config(provider="nonexistent")
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_provider(config)


# ── OpenCode provider ────────────────────────────────────────────────


class TestOpencodeProvider:
    def setup_method(self) -> None:
        self.config = _make_config(provider="opencode", model=LLMModel.OPENCODE_OPUS)
        self.provider = OpencodeProvider(config=self.config)

    def test_name_and_default_cmd(self) -> None:
        assert self.provider.name == "opencode"
        assert self.provider.default_cmd == "opencode"

    def test_build_command(self) -> None:
        cmd = self.provider.build_command("Fix the bug", 5.0, "opencode")
        assert cmd[0] == "opencode"
        assert "run" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        assert "Fix the bug" in cmd
        # No budget flag — opencode doesn't support it
        assert "--max-budget-usd" not in cmd

    def test_build_command_with_model(self) -> None:
        config = _make_config(provider="opencode", model=LLMModel.OPENCODE_OPUS)
        provider = OpencodeProvider(config=config)
        cmd = provider.build_command("Fix the bug", 5.0, "opencode")
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == LLMModel.OPENCODE_OPUS.value
        # --model should come after --format json but before prompt
        format_idx = cmd.index("--format")
        assert format_idx < model_idx
        assert cmd[-1] == "Fix the bug"  # prompt is last

    def test_build_command_requires_model(self) -> None:
        config = _make_config(provider="opencode", model=None)
        provider = OpencodeProvider(config=config)
        with pytest.raises(AssertionError, match="model is required"):
            provider.build_command("Do stuff", 1.0, "opencode")

    def test_build_command_custom_agent_cmd(self) -> None:
        cmd = self.provider.build_command("Fix it", 1.0, "/usr/local/bin/opencode")
        assert cmd[0] == "/usr/local/bin/opencode"
        assert "run" in cmd

    # ── parse: message.part.updated ───────────────────────────────────

    def test_parse_text_part(self) -> None:
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {"type": "text", "text": "I'll help you fix this"},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentTextEvent)
        assert events[0].text == "I'll help you fix this"

    def test_parse_text_part_empty(self) -> None:
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {"type": "text", "text": "   "},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert events == []

    def test_parse_thinking_part(self) -> None:
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {"type": "thinking", "text": "Let me think..."},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert events == []

    def test_parse_tool_in_progress(self) -> None:
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "name": "bash",
                    "input": {"command": "ls -la"},
                    "status": "in_progress",
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentToolCallEvent)
        assert events[0].name == "bash"
        assert events[0].input == {"command": "ls -la"}

    def test_parse_tool_running(self) -> None:
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "name": "bash",
                    "input": {"command": "pytest"},
                    "status": "running",
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentToolCallEvent)

    def test_parse_tool_no_status(self) -> None:
        """Tool with no status should be treated as a tool call."""
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "name": "read",
                    "input": {"path": "/foo.py"},
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentToolCallEvent)
        assert events[0].name == "read"

    def test_parse_tool_completed(self) -> None:
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "name": "bash",
                    "input": {"command": "ls"},
                    "output": "file1.py\nfile2.py",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentToolResultEvent)
        assert events[0].tool_name == "bash"
        assert events[0].output == "file1.py\nfile2.py"
        assert events[0].exit_code == 0

    def test_parse_error_part(self) -> None:
        line = json.dumps(
            {
                "type": "message.part.updated",
                "part": {"type": "error", "message": "Tool execution failed"},
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentErrorEvent)
        assert events[0].message == "Tool execution failed"

    # ── parse: message.completed ──────────────────────────────────────

    def test_parse_message_completed_with_usage(self) -> None:
        line = json.dumps(
            {
                "type": "message.completed",
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 150,
                    "cache_read_input_tokens": 200,
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        cost = events[0]
        assert isinstance(cost, AgentCostEvent)
        assert cost.cost_usd is None  # OpenCode doesn't report dollar cost
        assert cost.input_tokens == 500
        assert cost.output_tokens == 150
        assert cost.cached_tokens == 200

    def test_parse_message_completed_no_usage(self) -> None:
        line = json.dumps({"type": "message.completed"})
        events = self.provider.parse_stdout_line(line)
        assert events == []

    # ── parse: session.completed ──────────────────────────────────────

    def test_parse_session_completed_with_usage(self) -> None:
        line = json.dumps(
            {
                "type": "session.completed",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 400,
                },
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        cost = events[0]
        assert isinstance(cost, AgentCostEvent)
        assert cost.input_tokens == 1000
        assert cost.output_tokens == 300
        assert cost.cached_tokens == 400

    def test_parse_session_completed_no_usage(self) -> None:
        line = json.dumps({"type": "session.completed"})
        events = self.provider.parse_stdout_line(line)
        assert events == []

    # ── parse: error ──────────────────────────────────────────────────

    def test_parse_error_event(self) -> None:
        line = json.dumps(
            {
                "type": "error",
                "message": "Model not found: anthropic/bad-model",
            }
        )
        events = self.provider.parse_stdout_line(line)
        assert len(events) == 1
        assert isinstance(events[0], AgentErrorEvent)
        assert "Model not found" in events[0].message

    # ── parse: edge cases ─────────────────────────────────────────────

    def test_parse_non_json(self) -> None:
        events = self.provider.parse_stdout_line("not json at all")
        assert events == []

    def test_parse_unknown_type(self) -> None:
        line = json.dumps({"type": "heartbeat", "ts": 12345})
        events = self.provider.parse_stdout_line(line)
        assert events == []

    # ── env_vars ──────────────────────────────────────────────────────

    def test_env_vars_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no API keys set, returns empty dict."""
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert self.provider.env_vars() == {}

    def test_env_vars_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        env = self.provider.env_vars()
        assert env == {"ANTHROPIC_API_KEY": "sk-ant-test"}

    def test_env_vars_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
        env = self.provider.env_vars()
        assert env == {"OPENAI_API_KEY": "sk-oai-test"}

    def test_env_vars_multiple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
        monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
        env = self.provider.env_vars()
        assert env == {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "OPENAI_API_KEY": "sk-oai-test",
            "GEMINI_API_KEY": "gem-test",
        }


# ── LLMModel enum ────────────────────────────────────────────────────


class TestLLMModel:
    def test_claude_models(self) -> None:
        assert LLMModel.HAIKU == "claude-haiku-4-5-20251001"
        assert LLMModel.OPUS == "claude-opus-4-6"

    def test_codex_models(self) -> None:
        assert LLMModel.CODEX_MINI == "gpt-5.1-codex-mini"
        assert LLMModel.CODEX == "gpt-5.2-codex"

    def test_opencode_models(self) -> None:
        assert LLMModel.OPENCODE_HAIKU == "anthropic/claude-haiku-4-5"
        assert LLMModel.OPENCODE_OPUS == "anthropic/claude-opus-4-6"
        assert LLMModel.OPENCODE_CODEX_MINI == "openai/gpt-5.1-codex-mini"
        assert LLMModel.OPENCODE_CODEX == "openai/gpt-5.2-codex"

    def test_opencode_models_have_provider_prefix(self) -> None:
        """All OpenCode models must use provider/model format."""
        for member in LLMModel:
            if member.name.startswith("OPENCODE_"):
                assert "/" in member.value, f"{member.name} missing provider/ prefix"
