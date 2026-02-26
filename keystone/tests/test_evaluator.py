"""Tests for the LLM evaluator module."""

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keystone.evaluator import (
    _is_openai_model,
    _read_project_context,
    evaluate_agent_work,
    evaluate_and_fix,
    run_guardrail,
)
from keystone.schema import EvaluatorResult

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _make_generated_files(
    has_devcontainer: bool = True,
    has_dockerfile: bool = True,
    has_run_all_tests: bool = True,
) -> dict[str, str | None]:
    """Create test generated files dict."""
    return {
        "devcontainer_json": '{"build": {"dockerfile": "Dockerfile"}}'
        if has_devcontainer
        else None,
        "dockerfile": (
            "FROM python:3.12\nWORKDIR /project_src\n"
            "RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\n"
            "COPY .devcontainer/run_all_tests.sh /run_all_tests.sh\n"
        )
        if has_dockerfile
        else None,
        "run_all_tests_sh": (
            "#!/bin/bash\nset -euo pipefail\n"
            "pytest --junitxml=/test_artifacts/junit/pytest.xml\n"
            "echo '{\"success\": true}' > /test_artifacts/final_result.json\n"
        )
        if has_run_all_tests
        else None,
    }


def test_evaluator_skips_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator should skip gracefully when ANTHROPIC_API_KEY is not set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = evaluate_agent_work(
        generated_files=_make_generated_files(),
        agent_summary="Created devcontainer with pytest support",
        status_messages=["Exploring repo", "Creating Dockerfile"],
        verification_success=True,
        verification_error=None,
    )

    assert result.passed is True
    assert "Skipped" in result.reasoning


def test_evaluator_passes_complete_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator should pass when all files are present and agent completed work."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text=json.dumps(
                {
                    "passed": True,
                    "reasoning": "Agent created all required files with proper structure.",
                    "issues": [],
                }
            )
        )
    ]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Created devcontainer with pytest support",
            status_messages=["Exploring repo", "Creating Dockerfile", "Completed setup"],
            verification_success=True,
            verification_error=None,
        )

    assert result.passed is True
    assert result.model == DEFAULT_MODEL
    assert result.cost_usd > 0

    # Verify the API was called with the right model
    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == DEFAULT_MODEL


def test_evaluator_fails_missing_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator should fail when required files are missing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text=json.dumps(
                {
                    "passed": False,
                    "reasoning": "Agent did not create Dockerfile or run_all_tests.sh.",
                    "issues": ["Missing Dockerfile", "Missing run_all_tests.sh"],
                }
            )
        )
    ]
    mock_response.usage = MagicMock(input_tokens=80, output_tokens=40)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(has_dockerfile=False, has_run_all_tests=False),
            agent_summary=None,
            status_messages=["Exploring repo", "Giving up - too complex"],
            verification_success=False,
            verification_error="Build failed: no Dockerfile",
        )

    assert result.passed is False
    assert len(result.issues) == 2


def test_evaluator_handles_json_in_code_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator should parse JSON even when wrapped in markdown code blocks."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text='```json\n{"passed": true, "reasoning": "All good", "issues": []}\n```')
    ]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
        )

    assert result.passed is True


def test_evaluator_handles_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator should handle API errors gracefully (non-blocking)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API rate limit")

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
        )

    # Should pass (non-blocking) with error explanation
    assert result.passed is True
    assert "failed" in result.reasoning.lower()


def test_evaluator_handles_invalid_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator should handle non-JSON responses from the LLM."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="I think everything looks good but I'm not sure.")]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
        )

    # Should fail since we can't parse the response
    assert result.passed is False
    assert "not valid JSON" in result.reasoning


def test_evaluator_truncates_long_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator should truncate very long file contents to save tokens."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text=json.dumps({"passed": True, "reasoning": "Looks good", "issues": []}))
    ]
    mock_response.usage = MagicMock(input_tokens=500, output_tokens=30)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    long_dockerfile = "FROM python:3.12\n" + "RUN echo 'line'\n" * 500

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        evaluate_agent_work(
            generated_files={
                "devcontainer_json": '{"build": {}}',
                "dockerfile": long_dockerfile,
                "run_all_tests_sh": "#!/bin/bash\nexit 0\n",
            },
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
        )

    # Check the message was constructed with truncated content
    call_kwargs = mock_client.messages.create.call_args
    user_content = call_kwargs.kwargs["messages"][0]["content"]
    assert "truncated" in user_content


def test_evaluator_result_model() -> None:
    """Test EvaluatorResult model construction."""
    result = EvaluatorResult(
        passed=True,
        reasoning="All files present and correct",
        issues=[],
        model="claude-haiku-4-5-20251001",
        cost_usd=0.001,
    )
    assert result.passed is True
    assert result.model == "claude-haiku-4-5-20251001"

    # Test with issues
    result_fail = EvaluatorResult(
        passed=False,
        reasoning="Missing files",
        issues=["No Dockerfile", "No test script"],
    )
    assert result_fail.passed is False
    assert len(result_fail.issues) == 2


# ---------------------------------------------------------------------------
# Tests for evaluate_and_fix (the fixer)
# ---------------------------------------------------------------------------


def test_fixer_skips_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fixer should skip gracefully when ANTHROPIC_API_KEY is not set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = evaluate_and_fix(
        verification_error="Build failed: no Dockerfile",
        generated_files=_make_generated_files(),
        status_messages=["Exploring repo"],
        agent_summary=None,
        devcontainer_dir=tmp_path / ".devcontainer",
    )

    assert result.passed is False
    assert "Skipped" in result.reasoning


def test_fixer_writes_fixed_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fixer should write corrected files to disk on success."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fix_response = {
        "diagnosis": "Dockerfile was missing FROM instruction",
        "fixes_applied": ["Added FROM python:3.12", "Fixed COPY path"],
        "devcontainer_json": None,
        "dockerfile": "FROM python:3.12\nWORKDIR /project_src\nRUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\nCOPY .devcontainer/run_all_tests.sh /run_all_tests.sh\nRUN chmod +x /run_all_tests.sh\n",
        "run_all_tests_sh": "#!/bin/bash\nset -euo pipefail\npytest --junitxml=/test_artifacts/junit/pytest.xml\necho '{\"success\": true}' > /test_artifacts/final_result.json\n",
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(fix_response))]
    mock_response.usage = MagicMock(input_tokens=500, output_tokens=300)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    devcontainer_dir = tmp_path / ".devcontainer"

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_and_fix(
            verification_error="Build failed: no FROM instruction",
            generated_files=_make_generated_files(has_dockerfile=False),
            status_messages=["Exploring repo", "Creating Dockerfile"],
            agent_summary="Attempted to create devcontainer",
            devcontainer_dir=devcontainer_dir,
        )

    assert result.passed is True
    assert result.model == DEFAULT_MODEL
    assert result.cost_usd > 0
    assert "FROM" in result.reasoning.lower() or "dockerfile" in result.reasoning.lower()

    # Verify files were actually written to disk
    assert (devcontainer_dir / "Dockerfile").exists()
    assert (devcontainer_dir / "run_all_tests.sh").exists()
    assert "FROM python:3.12" in (devcontainer_dir / "Dockerfile").read_text()
    # devcontainer.json should NOT be written (null in response)
    assert not (devcontainer_dir / "devcontainer.json").exists()

    # Verify run_all_tests.sh is executable
    mode = (devcontainer_dir / "run_all_tests.sh").stat().st_mode
    assert mode & stat.S_IXUSR


def test_fixer_handles_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fixer should handle API errors gracefully."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API rate limit")

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_and_fix(
            verification_error="Build failed",
            generated_files=_make_generated_files(),
            status_messages=[],
            agent_summary=None,
            devcontainer_dir=tmp_path / ".devcontainer",
        )

    assert result.passed is False
    assert "failed" in result.reasoning.lower()


def test_fixer_handles_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fixer should handle non-JSON responses from the LLM."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Sorry, I can't fix this.")]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_and_fix(
            verification_error="Build failed",
            generated_files=_make_generated_files(),
            status_messages=[],
            agent_summary=None,
            devcontainer_dir=tmp_path / ".devcontainer",
        )

    assert result.passed is False
    assert "not valid JSON" in result.reasoning


def test_fixer_passed_false_when_no_files_written(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fixer should report passed=False when LLM returns empty/null files."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fix_response = {
        "diagnosis": "Cannot determine the issue",
        "fixes_applied": [],
        "devcontainer_json": None,
        "dockerfile": None,
        "run_all_tests_sh": None,
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(fix_response))]
    mock_response.usage = MagicMock(input_tokens=200, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_and_fix(
            verification_error="Tests timed out",
            generated_files=_make_generated_files(),
            status_messages=[],
            agent_summary=None,
            devcontainer_dir=tmp_path / ".devcontainer",
        )

    assert result.passed is False


# ---------------------------------------------------------------------------
# Tests for project context + guardrail integration
# ---------------------------------------------------------------------------


def test_read_project_context_finds_requirements(tmp_path: Path) -> None:
    """_read_project_context should include requirements.txt content."""
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\npytest==8.0.0\n")
    context = _read_project_context(tmp_path)
    assert "requirements.txt" in context
    assert "flask" in context


def test_read_project_context_fallback_lists_files(tmp_path: Path) -> None:
    """When no known config files exist, fallback to listing project root."""
    (tmp_path / "main.go").write_text("package main\n")
    context = _read_project_context(tmp_path)
    assert "main.go" in context


def test_run_guardrail_missing_devcontainer(tmp_path: Path) -> None:
    """guardrail.sh should report FAIL when .devcontainer is missing."""
    output = run_guardrail(tmp_path)
    assert "FAIL" in output or "MISSING" in output or "guardrail" in output.lower()


def test_fixer_includes_project_context_in_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fixer should include project files and guardrail output in LLM prompt."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    (tmp_path / "requirements.txt").write_text("flask==3.0.0\npytest\n")

    fix_response = {
        "diagnosis": "Fixed for flask project",
        "fixes_applied": ["Used flask base image"],
        "devcontainer_json": None,
        "dockerfile": "FROM python:3.12\nWORKDIR /project_src\nRUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\nCOPY .devcontainer/run_all_tests.sh /run_all_tests.sh\nRUN chmod +x /run_all_tests.sh\n",
        "run_all_tests_sh": "#!/bin/bash\npytest --junitxml=/test_artifacts/junit/pytest.xml\necho '{\"success\": true}' > /test_artifacts/final_result.json\n",
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(fix_response))]
    mock_response.usage = MagicMock(input_tokens=800, output_tokens=400)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        evaluate_and_fix(
            verification_error="Build failed",
            generated_files=_make_generated_files(has_dockerfile=False),
            status_messages=[],
            agent_summary=None,
            devcontainer_dir=tmp_path / ".devcontainer",
            project_root=tmp_path,
        )

    call_kwargs = mock_client.messages.create.call_args
    user_content = call_kwargs.kwargs["messages"][0]["content"]
    assert "requirements.txt" in user_content
    assert "flask" in user_content
    assert "Guardrail" in user_content


# ---------------------------------------------------------------------------
# Tests for model passthrough
# ---------------------------------------------------------------------------


def test_evaluator_passes_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """evaluate_agent_work should use the model parameter when calling the LLM."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text=json.dumps({"passed": True, "reasoning": "All good", "issues": []}))
    ]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    custom_model = "claude-opus-4-6"
    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
            model=custom_model,
        )

    assert result.model == custom_model
    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == custom_model


def test_fixer_passes_custom_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """evaluate_and_fix should use the model parameter when calling the LLM."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fix_response = {
        "diagnosis": "Fixed",
        "fixes_applied": ["fix"],
        "devcontainer_json": None,
        "dockerfile": "FROM python:3.12\nWORKDIR /app\n",
        "run_all_tests_sh": None,
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(fix_response))]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    custom_model = "claude-opus-4-6"
    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        result = evaluate_and_fix(
            verification_error="Build failed",
            generated_files=_make_generated_files(),
            status_messages=[],
            agent_summary=None,
            devcontainer_dir=tmp_path / ".devcontainer",
            model=custom_model,
        )

    assert result.model == custom_model
    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == custom_model


# ---------------------------------------------------------------------------
# Tests for OpenAI model routing
# ---------------------------------------------------------------------------


def test_is_openai_model() -> None:
    """_is_openai_model should identify gpt-* models correctly."""
    assert _is_openai_model("gpt-5.2-codex") is True
    assert _is_openai_model("gpt-5.1-codex-mini") is True
    assert _is_openai_model("openai/gpt-5.2-codex") is True
    assert _is_openai_model("claude-opus-4-6") is False
    assert _is_openai_model("claude-haiku-4-5-20251001") is False
    assert _is_openai_model("anthropic/claude-opus-4-6") is False


def test_evaluator_routes_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """evaluate_agent_work should use OpenAI SDK for gpt-* models."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(
        {"passed": True, "reasoning": "All good", "issues": []}
    )

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("keystone.evaluator.openai.OpenAI", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
            model="gpt-5.2-codex",
        )

    assert result.passed is True
    assert result.model == "gpt-5.2-codex"
    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["model"] == "gpt-5.2-codex"


def test_evaluator_strips_opencode_prefix_for_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenCode-prefixed gpt models should be stripped before calling OpenAI."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(
        {"passed": True, "reasoning": "All good", "issues": []}
    )

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("keystone.evaluator.openai.OpenAI", return_value=mock_client):
        result = evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
            model="openai/gpt-5.2-codex",
        )

    assert result.passed is True
    # Should strip "openai/" prefix when calling the API
    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["model"] == "gpt-5.2-codex"


def test_fixer_skips_without_openai_key_for_gpt_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fixer should skip when OPENAI_API_KEY is missing for a gpt model."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = evaluate_and_fix(
        verification_error="Build failed",
        generated_files=_make_generated_files(),
        status_messages=[],
        agent_summary=None,
        devcontainer_dir=tmp_path / ".devcontainer",
        model="gpt-5.2-codex",
    )

    assert result.passed is False
    assert "OPENAI_API_KEY" in result.reasoning


# ---------------------------------------------------------------------------
# Tests for guardrail in passive evaluator
# ---------------------------------------------------------------------------


def test_passive_evaluator_includes_guardrail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """evaluate_agent_work should include guardrail output when project_root is given."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text=json.dumps({"passed": True, "reasoning": "All good", "issues": []}))
    ]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("keystone.evaluator.anthropic.Anthropic", return_value=mock_client):
        evaluate_agent_work(
            generated_files=_make_generated_files(),
            agent_summary="Done",
            status_messages=[],
            verification_success=True,
            verification_error=None,
            project_root=tmp_path,
        )

    call_kwargs = mock_client.messages.create.call_args
    user_content = call_kwargs.kwargs["messages"][0]["content"]
    assert "Guardrail" in user_content
