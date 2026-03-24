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
        # Ensure /project directory exists and is accessible
        run_modal_command(sb, "mkdir", "-p", "/project", name="setup").wait()
        run_modal_command(sb, "chown", "-R", "agent:agent", "/project", name="setup").wait()

        # Upload budget script
        with sb.open("/project/keystone_budget.sh", "wb") as f:
            f.write(BUDGET_SCRIPT_PATH.read_bytes())
        run_modal_command(
            sb, "chmod", "+x", "/project/keystone_budget.sh", name="setup"
        ).wait()

        # Write wrapper script
        wrapper = f"""#!/bin/bash
set -e
export ANTHROPIC_API_KEY={shlex.quote(api_key)}
export AGENT_BUDGET_CAP_USD=10.00
export CCUSAGE_COMMAND=ccusage
export AGENT_TIME_DEADLINE=$(( $(date +%s) + 120 ))

echo "=== BEFORE ==="
bash /project/keystone_budget.sh

claude -p "say hello" --max-turns 1

echo "=== AFTER ==="
bash /project/keystone_budget.sh
"""
        with sb.open("/run_test.sh", "w") as f:
            f.write(wrapper)
        run_modal_command(sb, "chmod", "+x", "/run_test.sh", name="setup").wait()

        # Execute as agent user — stream() first to capture output, then wait() for exit code
        proc = run_modal_command(
            sb,
            "su",
            "agent",
            "-c",
            "/run_test.sh",
            capture=True,
            name="budget-test",
        )
        output = "\n".join(e.line for e in proc.stream())
        exit_code = proc.wait()

        assert exit_code == 0, (
            f"Wrapper script failed with exit code {exit_code}\n{output}"
        )

        # Split on markers
        assert "=== BEFORE ===" in output, (
            f"Missing BEFORE marker in output:\n{output}"
        )
        assert "=== AFTER ===" in output, (
            f"Missing AFTER marker in output:\n{output}"
        )

        before_section = output.split("=== BEFORE ===")[1].split("=== AFTER ===")[0]
        after_section = output.split("=== AFTER ===")[1]

        budget_re = re.compile(r"Remaining budget: \$([0-9.]+)")

        before_match = budget_re.search(before_section)
        assert before_match, f"No budget line in BEFORE section:\n{before_section}"
        before_budget = float(before_match.group(1))

        after_match = budget_re.search(after_section)
        assert after_match, f"No budget line in AFTER section:\n{after_section}"
        after_budget = float(after_match.group(1))

        # Before should be full budget (no cost yet)
        assert before_budget == pytest.approx(
            10.0, abs=0.01
        ), f"Expected ~$10.00 before, got ${before_budget}"

        # After should be less (claude consumed some budget)
        assert (
            after_budget < 10.0
        ), f"Expected budget < $10.00 after claude call, got ${after_budget}"

        # Both sections should show positive remaining time
        time_re = re.compile(r"Remaining time: (\d+) seconds")
        before_time = time_re.search(before_section)
        after_time = time_re.search(after_section)
        assert before_time and int(before_time.group(1)) > 0, (
            f"No positive time in BEFORE:\n{before_section}"
        )
        assert after_time and int(after_time.group(1)) > 0, (
            f"No positive time in AFTER:\n{after_section}"
        )

    finally:
        sb.terminate()
