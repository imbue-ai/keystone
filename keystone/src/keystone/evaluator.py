"""LLM evaluator that catches verification failures and attempts to fix them.

When the creator agent's output fails verification (build failure, missing
Dockerfile, test failures, timeouts, etc.), this module calls a cheap LLM
(Haiku) with the error context and generated files, asking it to produce
corrected versions.  The fixed files are written back to disk so the caller
can re-run verification.

Design: one-shot fix — no back-and-forth conversation.  This keeps cost low
(single Haiku call, only on failure) while still rescuing common mistakes.
"""

import json
import logging
import os
from pathlib import Path

import anthropic

from keystone.schema import EvaluatorResult

logger = logging.getLogger(__name__)

EVALUATOR_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Prompt for the fixer: given the error + files, produce corrected files.
# ---------------------------------------------------------------------------
FIXER_SYSTEM_PROMPT = """\
You are a devcontainer repair agent.  An AI agent was asked to create a \
working .devcontainer/ setup (devcontainer.json, Dockerfile, run_all_tests.sh) \
for a software project, but verification FAILED.

You will be given:
1. The error that occurred (build failure, missing file, test failure, etc.)
2. The files the agent produced (some may be missing or broken)
3. Information about the project (language, test framework, etc.) from the \
   agent's status messages

Your job is to produce FIXED versions of the three files so the build and \
tests pass.  Output a JSON object with exactly these keys:

{
  "diagnosis": "one-line explanation of what went wrong",
  "fixes_applied": ["list of changes you made"],
  "devcontainer_json": "<full corrected content or null to keep existing>",
  "dockerfile": "<full corrected Dockerfile content>",
  "run_all_tests_sh": "<full corrected run_all_tests.sh content>"
}

Rules:
- If a file is null/missing, create it from scratch.
- The Dockerfile MUST:
  - Start with a FROM instruction
  - Create /test_artifacts: RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts
  - End with: COPY .devcontainer/run_all_tests.sh /run_all_tests.sh\\nRUN chmod +x /run_all_tests.sh
  - Use WORKDIR /project_src and copy source files explicitly (not COPY . .)
- run_all_tests.sh MUST:
  - Start with #!/bin/bash
  - Produce JUnit XML in /test_artifacts/junit/*.xml
  - Write /test_artifacts/final_result.json
- devcontainer_json should normally be kept as-is (return null to skip).
- Focus on fixing the specific error.  Don't rewrite everything.
- For build failures: check package names, base images, COPY paths, syntax.
- For test failures: check test commands, working directory, env vars.
- For missing files: create them.
- For timeouts: add timeout commands, reduce scope.
"""


def evaluate_and_fix(
    verification_error: str,
    generated_files: dict[str, str | None],
    status_messages: list[str],
    agent_summary: str | None,
    devcontainer_dir: Path,
) -> EvaluatorResult:
    """Attempt to fix verification failures using a cheap LLM call.

    On success, writes corrected files to *devcontainer_dir* so the caller
    can re-run verification with the patched output.

    Args:
        verification_error: The error string from verification.
        generated_files: Dict with keys devcontainer_json, dockerfile, run_all_tests_sh.
        status_messages: Agent's status messages (project context).
        agent_summary: Agent's final summary.
        devcontainer_dir: Path to .devcontainer/ on disk (files will be overwritten).

    Returns:
        EvaluatorResult describing what happened.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping evaluator fix attempt")
        return EvaluatorResult(
            passed=False,
            reasoning="Skipped: ANTHROPIC_API_KEY not available",
        )

    # ---- Build the user message with full error context ----
    parts: list[str] = []

    parts.append(f"## Verification Error\n```\n{verification_error[:4000]}\n```\n")

    parts.append("## Files Produced by Agent\n")
    for name, content in generated_files.items():
        if content:
            display = content[:4000] + "\n...(truncated)" if len(content) > 4000 else content
            parts.append(f"### {name}\n```\n{display}\n```\n")
        else:
            parts.append(f"### {name}\nNOT CREATED (missing)\n")

    if status_messages:
        parts.append("## Agent Status Messages (project context)\n")
        for msg in status_messages[-15:]:
            parts.append(f"- {msg}")

    if agent_summary:
        parts.append(f"\n## Agent Summary\n{agent_summary}")

    user_message = "\n".join(parts)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=EVALUATOR_MODEL,
            max_tokens=4096,
            system=FIXER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        first_block = response.content[0]
        if not hasattr(first_block, "text"):
            return EvaluatorResult(
                passed=False,
                reasoning="Evaluator returned non-text content block",
                model=EVALUATOR_MODEL,
            )
        response_text = first_block.text.strip()  # type: ignore[union-attr]

        # Extract JSON (handle markdown code blocks)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        fix_data = json.loads(response_text)

        cost_usd = 0.0
        if response.usage:
            cost_usd = (
                response.usage.input_tokens * 0.80 / 1_000_000
                + response.usage.output_tokens * 4.0 / 1_000_000
            )

        # ---- Write fixed files to disk ----
        devcontainer_dir.mkdir(parents=True, exist_ok=True)
        files_written: list[str] = []

        if fix_data.get("dockerfile"):
            (devcontainer_dir / "Dockerfile").write_text(fix_data["dockerfile"])
            files_written.append("Dockerfile")

        if fix_data.get("run_all_tests_sh"):
            script = devcontainer_dir / "run_all_tests.sh"
            script.write_text(fix_data["run_all_tests_sh"])
            script.chmod(0o755)
            files_written.append("run_all_tests.sh")

        if fix_data.get("devcontainer_json"):
            (devcontainer_dir / "devcontainer.json").write_text(fix_data["devcontainer_json"])
            files_written.append("devcontainer.json")

        diagnosis = fix_data.get("diagnosis", "No diagnosis provided")
        fixes = fix_data.get("fixes_applied", [])

        logger.info(
            "Evaluator fix attempt: diagnosis=%s, files_written=%s, fixes=%s",
            diagnosis,
            files_written,
            fixes,
        )

        return EvaluatorResult(
            passed=bool(files_written),
            reasoning=diagnosis,
            issues=fixes,
            model=EVALUATOR_MODEL,
            cost_usd=cost_usd,
        )

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse evaluator fix response as JSON: {e}")
        return EvaluatorResult(
            passed=False,
            reasoning=f"Evaluator response was not valid JSON: {e}",
            model=EVALUATOR_MODEL,
        )
    except Exception as e:
        logger.error(f"Evaluator fix call failed: {e}")
        return EvaluatorResult(
            passed=False,
            reasoning=f"Evaluator fix call failed: {e}",
            model=EVALUATOR_MODEL,
        )


# ---------------------------------------------------------------------------
# Passive evaluation (kept for backwards compatibility / reporting)
# ---------------------------------------------------------------------------
EVALUATOR_SYSTEM_PROMPT = """\
You are a strict quality evaluator for an AI agent that creates devcontainer setups.
Your job is to determine whether the agent completed its task or gave up / produced incomplete work.

The agent's task was to create three files inside .devcontainer/:
1. devcontainer.json — copied from a pre-generated file
2. Dockerfile — a working Docker image definition
3. run_all_tests.sh — a test runner script that produces JUnit XML

You will be given:
- The generated files (if any)
- The agent's status messages and summary
- The verification result (build/test outcome)

Evaluate whether the agent:
- Created all three required files
- Made a genuine attempt at a working Dockerfile (not just a stub)
- Made a genuine attempt at a working test runner (not just a stub)
- Did not give up early with excuses

Respond with a JSON object:
{
  "passed": true/false,
  "reasoning": "Brief explanation of your assessment",
  "issues": ["list", "of", "specific", "issues"]  // empty if passed
}
"""


def evaluate_agent_work(
    generated_files: dict[str, str | None],
    agent_summary: str | None,
    status_messages: list[str],
    verification_success: bool,
    verification_error: str | None,
) -> EvaluatorResult:
    """Run the LLM evaluator on the agent's output (passive check, no fixes).

    Returns:
        EvaluatorResult with pass/fail and reasoning.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping LLM evaluation")
        return EvaluatorResult(
            passed=True,
            reasoning="Skipped: ANTHROPIC_API_KEY not available",
        )

    user_parts: list[str] = []

    user_parts.append("## Generated Files\n")
    for name, content in generated_files.items():
        if content:
            display = content[:3000] + "\n...(truncated)" if len(content) > 3000 else content
            user_parts.append(f"### {name}\n```\n{display}\n```\n")
        else:
            user_parts.append(f"### {name}\nNOT CREATED\n")

    if status_messages:
        user_parts.append("## Agent Status Messages\n")
        for msg in status_messages[-10:]:
            user_parts.append(f"- {msg}")

    if agent_summary:
        user_parts.append(f"\n## Agent Summary\n{agent_summary}")

    user_parts.append("\n## Verification Result")
    user_parts.append(f"Success: {verification_success}")
    if verification_error:
        err_display = (
            verification_error[:2000] + "\n...(truncated)"
            if len(verification_error) > 2000
            else verification_error
        )
        user_parts.append(f"Error: {err_display}")

    user_message = "\n".join(user_parts)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=EVALUATOR_MODEL,
            max_tokens=512,
            system=EVALUATOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        first_block = response.content[0]
        if not hasattr(first_block, "text"):
            return EvaluatorResult(
                passed=False,
                reasoning="Evaluator returned non-text content block",
                model=EVALUATOR_MODEL,
            )
        response_text = first_block.text.strip()  # type: ignore[union-attr]

        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        result_data = json.loads(response_text)

        cost_usd = 0.0
        if response.usage:
            cost_usd = (
                response.usage.input_tokens * 0.80 / 1_000_000
                + response.usage.output_tokens * 4.0 / 1_000_000
            )

        return EvaluatorResult(
            passed=result_data.get("passed", False),
            reasoning=result_data.get("reasoning", "No reasoning provided"),
            issues=result_data.get("issues", []),
            model=EVALUATOR_MODEL,
            cost_usd=cost_usd,
        )

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse evaluator response as JSON: {e}")
        return EvaluatorResult(
            passed=False,
            reasoning=f"Evaluator response was not valid JSON: {e}",
            model=EVALUATOR_MODEL,
        )
    except Exception as e:
        logger.error(f"Evaluator LLM call failed: {e}")
        return EvaluatorResult(
            passed=True,
            reasoning=f"Evaluator call failed (non-blocking): {e}",
            model=EVALUATOR_MODEL,
        )
