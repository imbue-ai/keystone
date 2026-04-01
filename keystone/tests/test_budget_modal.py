"""Integration test: keystone_budget.sh reports cost change after a real Claude call in Modal."""

from __future__ import annotations

import logging
import os
import re
import shlex

import modal
import pytest

from keystone.agent_runner import BUDGET_SCRIPT_PATH
from keystone.modal.image import create_modal_image
from keystone.modal.modal_runner import run_modal_command

logger = logging.getLogger(__name__)

BUDGET_RE = re.compile(r"Remaining budget: ([0-9.]+) USD")
TIME_RE = re.compile(r"Remaining time: (\d+) seconds")


def _run_in_sandbox(sb: modal.Sandbox, cmd: str, *, name: str) -> tuple[int, str]:
    """Run a bash command in the sandbox, sourcing the env file first.

    Returns (exit_code, captured_output).
    """
    proc = run_modal_command(
        sb,
        "su",
        "agent",
        "-c",
        f"source /env.sh && {cmd}",
        capture=True,
        name=name,
    )
    output = "\n".join(e.line for e in proc.stream())
    # stream() calls wait() internally, so just read the exit code
    return proc.proc.returncode or 0, output


@pytest.mark.modal
@pytest.mark.agentic
def test_budget_script_reports_cost_after_claude() -> None:
    """Run keystone_budget.sh before and after 'claude -p' and verify cost increased."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    assert api_key, "ANTHROPIC_API_KEY must be set"

    logger.info("Creating Modal sandbox...")
    app = modal.App.lookup("keystone-test", create_if_missing=True)
    image = create_modal_image()
    sb = modal.Sandbox.create(app=app, image=image, timeout=300)
    logger.info("Sandbox created.")

    try:
        # Create /project and upload budget script
        logger.info("Uploading budget script...")
        run_modal_command(sb, "mkdir", "-p", "/project", name="setup").wait()
        with sb.open("/project/keystone_budget.sh", "wb") as f:
            f.write(BUDGET_SCRIPT_PATH.read_bytes())
        run_modal_command(sb, "chmod", "+x", "/project/keystone_budget.sh", name="setup").wait()
        logger.info("Budget script uploaded.")

        # Get sandbox clock and compute a fixed deadline so it doesn't reset each call
        proc = run_modal_command(sb, "date", "+%s", capture=True, name="clock")
        sandbox_now = int("".join(e.line for e in proc.stream()).strip())
        deadline = sandbox_now + 120

        # Write a shared env file so each command picks up the same vars
        env_content = f"""export ANTHROPIC_API_KEY={shlex.quote(api_key)}
export AGENT_BUDGET_CAP_USD=10.00
export CCUSAGE_COMMAND=ccusage
export AGENT_TIME_DEADLINE={deadline}
"""
        with sb.open("/env.sh", "w") as f:
            f.write(env_content)

        # 1. Check budget BEFORE — ccusage has no session yet, so budget
        #    reports "unknown" while time should be positive.
        logger.info("Running budget check (before)...")
        rc, before_output = _run_in_sandbox(
            sb, "bash /project/keystone_budget.sh", name="budget-before"
        )
        logger.info(f"Before output:\n{before_output}")
        assert rc == 0, f"budget script failed (before):\n{before_output}"

        before_time = TIME_RE.search(before_output)
        assert before_time and int(before_time.group(1)) > 0, (
            f"No positive time before:\n{before_output}"
        )
        # No ccusage session exists yet, so the script can't report a dollar amount
        assert "ccusage failed" in before_output, (
            f"Expected 'ccusage failed' before any claude session:\n{before_output}"
        )

        # 2. Run Claude to consume some budget (creates a ccusage session).
        #    Don't capture output — we just need it to finish.
        logger.info("Running claude -p 'say hello'...")
        claude_proc = run_modal_command(
            sb,
            "su",
            "agent",
            "-c",
            "source /env.sh && claude -p 'say hello' --max-turns 1 --dangerously-skip-permissions",
            name="claude",
            pty=True,
        )
        rc = claude_proc.wait()
        logger.info("Claude exited with rc=%d", rc)
        assert rc == 0, f"claude call failed with exit code {rc}"

        # 3. Check budget AFTER — now ccusage has session data
        logger.info("Running budget check (after)...")
        rc, after_output = _run_in_sandbox(
            sb, "bash /project/keystone_budget.sh", name="budget-after"
        )
        logger.info(f"After output:\n{after_output}")
        assert rc == 0, f"budget script failed (after):\n{after_output}"

        after_match = BUDGET_RE.search(after_output)
        assert after_match, f"No budget line in after output:\n{after_output}"
        after_budget = float(after_match.group(1))
        # ccusage should now work (no "ccusage failed") — budget is reported as a number
        assert "ccusage failed" not in after_output, (
            f"ccusage still failing after claude session:\n{after_output}"
        )
        # Claude consumed some budget, so remaining should be less than the cap.
        # With %.4f precision, even sub-cent costs are visible.
        assert after_budget < 10.0, (
            f"Expected budget < $10.00 after claude call, got ${after_budget}"
        )
        assert after_budget > 0.0, f"Budget should still be positive, got ${after_budget}"

        after_time = TIME_RE.search(after_output)
        assert after_time and int(after_time.group(1)) > 0, (
            f"No positive time after:\n{after_output}"
        )
        assert int(after_time.group(1)) < int(before_time.group(1)), (
            f"Expected time to decrease after claude call: before={before_time.group(1)}s, after={after_time.group(1)}s"
        )

        # 4. Re-run with budget cap of $0.00 — should report OVER BUDGET and exit 1
        logger.info("Running over-budget check (cap=$0.00)...")
        zero_env = f"""export ANTHROPIC_API_KEY={shlex.quote(api_key)}
export AGENT_BUDGET_CAP_USD=0.00
export CCUSAGE_COMMAND=ccusage
export AGENT_TIME_DEADLINE={deadline}
"""
        with sb.open("/env.sh", "w") as f:
            f.write(zero_env)

        rc, over_output = _run_in_sandbox(
            sb, "bash /project/keystone_budget.sh", name="budget-over"
        )
        logger.info(f"Over-budget output (rc={rc}):\n{over_output}")
        assert rc == 1, f"Expected exit code 1 for over-budget, got {rc}:\n{over_output}"
        assert "OVER BUDGET" in over_output, f"Expected 'OVER BUDGET' in output:\n{over_output}"

    finally:
        sb.terminate()
