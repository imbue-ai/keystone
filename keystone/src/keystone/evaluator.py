"""LLM evaluator that catches verification failures and attempts to fix them.

When the creator agent's output fails verification (build failure, missing
Dockerfile, test failures, timeouts, etc.), this module calls a cheap LLM
with the error context and generated files, asking it to produce
corrected versions.  The fixed files are written back to disk so the caller
can re-run verification.

Design: one-shot fix — no back-and-forth conversation.  This keeps cost low
(single LLM call, only on failure) while still rescuing common mistakes.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

import anthropic
import openai

from keystone.llm_provider.pricing import estimate_cost_usd
from keystone.schema import EvaluatorResult

logger = logging.getLogger(__name__)

# Project files that reveal the language/framework/dependencies.
_PROJECT_CONTEXT_FILES = [
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "go.mod",
    "Gemfile",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Makefile",
    "tox.ini",
    "pytest.ini",
    "conftest.py",
]

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
3. Key project files (requirements.txt, package.json, etc.) so you know the \
   exact language, dependencies, and test framework
4. Guardrail check results showing exactly which structural checks failed
5. Information about the project from the agent's status messages

Your job is to produce FIXED versions of the three files so the build and \
tests pass.  Output a JSON object with exactly these keys:

{
  "diagnosis": "one-line explanation of what went wrong",
  "fixes_applied": ["list of changes you made"],
  "devcontainer_json": "<full corrected content or null to keep existing>",
  "dockerfile": "<full corrected Dockerfile content>",
  "run_all_tests_sh": "<full corrected run_all_tests.sh content>"
}

GUARDRAIL REQUIREMENTS (these checks WILL be run on your output — all must pass):

Dockerfile MUST:
  - Start with a FROM instruction (e.g. FROM python:3.12)
  - Contain: RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts
  - Contain: COPY .devcontainer/run_all_tests.sh /run_all_tests.sh
  - Set WORKDIR (e.g. WORKDIR /project_src)
  - NOT use "COPY . ." — use explicit COPY for source files only

run_all_tests.sh MUST:
  - Start with #!/bin/bash
  - Produce JUnit XML in /test_artifacts/junit/*.xml
  - Write /test_artifacts/final_result.json with {"success": true/false}
  - Reference /test_artifacts for all output

Additional rules:
- Use the project files provided to pick the right base image, install the \
  right packages, and run the correct test command.
- If requirements.txt exists, install it. If package.json, run npm install. etc.
- For Python projects: use pytest --junitxml=/test_artifacts/junit/pytest.xml
- For Node projects: use jest or mocha with JUnit reporter
- For build failures: check package names, base images, COPY paths.
- For test failures (exit code 4 = no tests collected): check the test \
  discovery path, WORKDIR, and that test files are COPY'd into the image.
- devcontainer_json should normally be kept as-is (return null to skip).
"""


# ---------------------------------------------------------------------------
# Provider routing helpers
# ---------------------------------------------------------------------------


def _is_openai_model(model: str) -> bool:
    """Return True for models that use the OpenAI API."""
    bare = model.split("/", 1)[-1] if "/" in model else model
    return bare.startswith("gpt-")


def _is_codex_model(model: str) -> bool:
    """Return True for Codex models that require the Responses API."""
    bare = model.split("/", 1)[-1] if "/" in model else model
    return "codex" in bare.lower()


def _call_llm(model: str, system: str, user_message: str, max_tokens: int) -> tuple[str, float]:
    """Call the appropriate LLM API and return (response_text, cost_usd).

    Routes to OpenAI SDK for gpt-* models and Anthropic SDK otherwise.
    Strips ``provider/`` prefixes (e.g. ``openai/gpt-5.2-codex`` -> ``gpt-5.2-codex``)
    before calling the API.
    """
    bare_model = model.split("/", 1)[-1] if "/" in model else model

    if _is_openai_model(bare_model):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        client = openai.OpenAI(api_key=api_key)

        if _is_codex_model(bare_model):
            # Codex models only support the Responses API (/v1/responses).
            response = client.responses.create(
                model=bare_model,
                instructions=system,
                input=user_message,
                max_output_tokens=max_tokens,
            )
            # Extract text from output items.
            text = ""
            for item in response.output:
                if item.type == "message":
                    for block in item.content:
                        if block.type == "output_text":
                            text += block.text
            input_tokens = response.usage.input_tokens if response.usage else 0
            output_tokens = response.usage.output_tokens if response.usage else 0
        else:
            # Standard OpenAI models use Chat Completions.
            chat_response = client.chat.completions.create(
                model=bare_model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
            )
            text = chat_response.choices[0].message.content or ""
            input_tokens = chat_response.usage.prompt_tokens if chat_response.usage else 0
            output_tokens = chat_response.usage.completion_tokens if chat_response.usage else 0

        cost_usd = estimate_cost_usd(
            input_tokens=input_tokens,
            cached_tokens=0,
            output_tokens=output_tokens,
            model=bare_model,
        )
        return text.strip(), cost_usd
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=bare_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        first_block = response.content[0]
        if not hasattr(first_block, "text"):
            raise RuntimeError("Anthropic returned non-text content block")
        text = first_block.text.strip()  # type: ignore[union-attr]
        input_tokens = response.usage.input_tokens if response.usage else 0
        output_tokens = response.usage.output_tokens if response.usage else 0
        cost_usd = estimate_cost_usd(
            input_tokens=input_tokens,
            cached_tokens=0,
            output_tokens=output_tokens,
            model=bare_model,
        )
        return text, cost_usd


def run_guardrail(project_root: Path) -> str:
    """Run guardrail.sh from project_root and return its output (non-Docker checks only).

    We skip the Docker build step (section 4) to keep this fast and side-effect-free.
    Returns the combined stdout/stderr output.
    """
    guardrail_path = Path(__file__).parent / "guardrail.sh"
    if not guardrail_path.exists():
        return "(guardrail.sh not found)"
    try:
        # Inject SKIP_DOCKER_BUILD=1 so the script can optionally skip section 4.
        # Even without that env var the script will just fail the docker build check
        # which is fine — we want all structural feedback.
        result = subprocess.run(
            ["bash", str(guardrail_path)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "SKIP_DOCKER_BUILD": "1"},
        )
        output = result.stdout + result.stderr
        return output[:3000]
    except Exception as e:
        return f"(guardrail failed to run: {e})"


def _read_project_context(project_root: Path) -> str:
    """Read key project files to give the fixer context about the project."""
    parts: list[str] = []
    for filename in _PROJECT_CONTEXT_FILES:
        fpath = project_root / filename
        if fpath.exists():
            try:
                content = fpath.read_text()[:2000]
                parts.append(f"### {filename}\n```\n{content}\n```")
            except Exception:
                pass
    if not parts:
        # Fallback: list top-level files so LLM can infer the project type
        try:
            entries = sorted(p.name for p in project_root.iterdir() if not p.name.startswith("."))
            parts.append(f"### Project root files\n{', '.join(entries[:40])}")
        except Exception:
            pass
    return "\n".join(parts)


def evaluate_and_fix(
    verification_error: str,
    generated_files: dict[str, str | None],
    status_messages: list[str],
    agent_summary: str | None,
    devcontainer_dir: Path,
    project_root: Path | None = None,
    model: str = "claude-haiku-4-5-20251001",
    guardrail: bool = True,
) -> EvaluatorResult:
    """Attempt to fix verification failures using an LLM call.

    On success, writes corrected files to *devcontainer_dir* so the caller
    can re-run verification with the patched output.

    Args:
        verification_error: The error string from verification.
        generated_files: Dict with keys devcontainer_json, dockerfile, run_all_tests_sh.
        status_messages: Agent's status messages (project context).
        agent_summary: Agent's final summary.
        devcontainer_dir: Path to .devcontainer/ on disk (files will be overwritten).
        project_root: Root of the project (used to read requirements, run guardrail).
        model: LLM model to use for the evaluator (matches agent model by default).
        guardrail: Whether to run guardrail checks and include output in LLM context.

    Returns:
        EvaluatorResult describing what happened.
    """
    # Check for the appropriate API key based on model
    if _is_openai_model(model):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, skipping evaluator fix attempt")
            return EvaluatorResult(
                passed=False,
                reasoning="Skipped: OPENAI_API_KEY not available",
            )
    else:
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

    # ---- Project files: give the fixer real context about the project ----
    if project_root is not None:
        project_context = _read_project_context(project_root)
        if project_context:
            parts.append(
                f"## Project Files (language/dependencies/test framework)\n{project_context}\n"
            )

        # ---- Guardrail output: structural check results ----
        if guardrail:
            guardrail_output = run_guardrail(project_root)
            parts.append(f"## Guardrail Check Results\n```\n{guardrail_output}\n```\n")

    if status_messages:
        parts.append("## Agent Status Messages (project context)\n")
        for msg in status_messages[-15:]:
            parts.append(f"- {msg}")

    if agent_summary:
        parts.append(f"\n## Agent Summary\n{agent_summary}")

    user_message = "\n".join(parts)

    try:
        response_text, cost_usd = _call_llm(model, FIXER_SYSTEM_PROMPT, user_message, 4096)

        # Extract JSON (handle markdown code blocks)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        fix_data = json.loads(response_text)

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
            model=model,
            cost_usd=cost_usd,
        )

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse evaluator fix response as JSON: {e}")
        return EvaluatorResult(
            passed=False,
            reasoning=f"Evaluator response was not valid JSON: {e}",
            model=model,
        )
    except Exception as e:
        logger.error(f"Evaluator fix call failed: {e}")
        return EvaluatorResult(
            passed=False,
            reasoning=f"Evaluator fix call failed: {e}",
            model=model,
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
    project_root: Path | None = None,
    model: str = "claude-haiku-4-5-20251001",
    guardrail: bool = True,
) -> EvaluatorResult:
    """Run the LLM evaluator on the agent's output (passive check, no fixes).

    Args:
        generated_files: Dict with keys devcontainer_json, dockerfile, run_all_tests_sh.
        agent_summary: Agent's final summary.
        status_messages: Agent's status messages.
        verification_success: Whether verification passed.
        verification_error: Error string from verification, if any.
        project_root: Root of the project (used to run guardrail for context).
        model: LLM model to use for the evaluator (matches agent model by default).
        guardrail: Whether to run guardrail checks and include output in LLM context.

    Returns:
        EvaluatorResult with pass/fail and reasoning.
    """
    # Check for the appropriate API key based on model
    if _is_openai_model(model):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, skipping LLM evaluation")
            return EvaluatorResult(
                passed=True,
                reasoning="Skipped: OPENAI_API_KEY not available",
            )
    else:
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

    # ---- Guardrail output: give the evaluator structural context ----
    if project_root is not None and guardrail:
        guardrail_output = run_guardrail(project_root)
        user_parts.append(f"## Guardrail Check Results\n```\n{guardrail_output}\n```\n")

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
        response_text, cost_usd = _call_llm(model, EVALUATOR_SYSTEM_PROMPT, user_message, 512)

        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        result_data = json.loads(response_text)

        return EvaluatorResult(
            passed=result_data.get("passed", False),
            reasoning=result_data.get("reasoning", "No reasoning provided"),
            issues=result_data.get("issues", []),
            model=model,
            cost_usd=cost_usd,
        )

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse evaluator response as JSON: {e}")
        return EvaluatorResult(
            passed=False,
            reasoning=f"Evaluator response was not valid JSON: {e}",
            model=model,
        )
    except Exception as e:
        logger.error(f"Evaluator LLM call failed: {e}")
        return EvaluatorResult(
            passed=True,
            reasoning=f"Evaluator call failed (non-blocking): {e}",
            model=model,
        )
