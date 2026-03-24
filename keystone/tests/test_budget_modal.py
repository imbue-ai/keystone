"""Integration test: keystone_budget.sh reports cost change after a real Claude call in Modal."""

from __future__ import annotations

import os
import re
import shlex

import modal
import pytest

from keystone.agent_runner import BUDGET_SCRIPT_PATH
from keystone.modal.image import create_modal_image
from keystone.modal.modal_runner import run_modal_command

BUDGET_RE = re.compile(r"Remaining budget: \$([0-9.]+)")
TIME_RE = re.compile(r"Remaining time: (\d+) seconds")


def _run_in_sandbox(sb: modal.Sandbox, cmd: str, *, name: str) -> tuple[int, str]:
    """Run a bash command in the sandbox, sourcing the env file first.

    Returns (exit_code, captured_output).
    """
    proc = run_modal_command(
        sb,
        "bash",
        "-c",
        f"source /env.sh && {cmd}",
        capture=True,
        name=name,
    )
    output = "\n".join(e.line for e in proc.stream())
    exit_code = proc.wait()
    return exit_code, output


@pytest.mark.modal
@pytest.mark.agentic
def test_budget_script_reports_cost_after_claude() -> None:
    """Run keystone_budget.sh before and after 'claude -p' and verify cost increased."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    assert api_key, "ANTHROPIC_API_KEY must be set"

    app = modal.App.lookup("keystone-test", create_if_missing=True)
    image = create_modal_image()
    sb = modal.Sandbox.create(app=app, image=image, timeout=300)

    try:
        # Create /project and upload budget script
        run_modal_command(sb, "mkdir", "-p", "/project", name="setup").wait()
        with sb.open("/project/keystone_budget.sh", "wb") as f:
            f.write(BUDGET_SCRIPT_PATH.read_bytes())
        run_modal_command(sb, "chmod", "+x", "/project/keystone_budget.sh", name="setup").wait()

        # Write a shared env file so each command picks up the same vars
        env_content = f"""export ANTHROPIC_API_KEY={shlex.quote(api_key)}
export AGENT_BUDGET_CAP_USD=10.00
export CCUSAGE_COMMAND=ccusage
export AGENT_TIME_DEADLINE=$(( $(date +%s) + 120 ))
"""
        with sb.open("/env.sh", "w") as f:
            f.write(env_content)

        # 1. Check budget BEFORE — should be full ($10)
        rc, before_output = _run_in_sandbox(sb, "bash /project/keystone_budget.sh", name="budget-before")
        assert rc == 0, f"budget script failed (before):\n{before_output}"

        before_match = BUDGET_RE.search(before_output)
        assert before_match, f"No budget line in before output:\n{before_output}"
        before_budget = float(before_match.group(1))
        assert before_budget == pytest.approx(10.0, abs=0.01), f"Expected ~$10.00 before, got ${before_budget}"

        before_time = TIME_RE.search(before_output)
        assert before_time and int(before_time.group(1)) > 0, f"No positive time before:\n{before_output}"

        # 2. Run Claude to consume some budget
        rc, claude_output = _run_in_sandbox(
            sb, "claude -p 'say hello' --max-turns 1", name="claude"
        )
        assert rc == 0, f"claude call failed:\n{claude_output}"

        # 3. Check budget AFTER — should be less than $10
        rc, after_output = _run_in_sandbox(sb, "bash /project/keystone_budget.sh", name="budget-after")
        assert rc == 0, f"budget script failed (after):\n{after_output}"

        after_match = BUDGET_RE.search(after_output)
        assert after_match, f"No budget line in after output:\n{after_output}"
        after_budget = float(after_match.group(1))
        assert after_budget < 10.0, f"Expected budget < $10.00 after claude call, got ${after_budget}"

        after_time = TIME_RE.search(after_output)
        assert after_time and int(after_time.group(1)) > 0, f"No positive time after:\n{after_output}"

    finally:
        sb.terminate()
