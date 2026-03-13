"""Snapshot tests for prompt generation.

Run with --snapshot-update to regenerate goldens after intentional changes.
"""

import pytest
from syrupy.assertion import SnapshotAssertion

from keystone.prompts import build_prompt
from keystone.schema import AgentConfig


def _make_config(
    *,
    guardrail: bool = True,
    agent_in_modal: bool = True,
    use_agents_md: bool = False,
) -> AgentConfig:
    """Helper to build an AgentConfig with sensible defaults for testing."""
    return AgentConfig(
        max_budget_usd=1.0,
        agent_time_limit_seconds=3600,
        agent_in_modal=agent_in_modal,
        provider="claude",
        guardrail=guardrail,
        use_agents_md=use_agents_md,
    )


# -- Inline prompt (use_agents_md=False) -----------------------------------


@pytest.mark.parametrize(
    "guardrail,agent_in_modal",
    [
        (True, True),
        # (True, False),
        (False, True),
        # (False, False),
    ],
    ids=[
        "guardrail-modal",
        # "guardrail-local",
        "no_guardrail-modal",
        # "no_guardrail-local",
    ],
)
def test_inline_prompt(
    guardrail: bool,
    agent_in_modal: bool,
    snapshot: SnapshotAssertion,
) -> None:
    config = _make_config(guardrail=guardrail, agent_in_modal=agent_in_modal)
    result = build_prompt(config)
    assert result.cli_prompt == snapshot


# -- AGENTS.md prompt (use_agents_md=True) ---------------------------------


@pytest.mark.parametrize(
    "guardrail,agent_in_modal",
    [
        (True, True),
        # (True, False),
        (False, True),
        # (False, False),
    ],
    ids=[
        "guardrail-modal",
        # "guardrail-local",
        "no_guardrail-modal",
        # "no_guardrail-local",
    ],
)
def test_agents_md_prompt(
    guardrail: bool,
    agent_in_modal: bool,
    snapshot: SnapshotAssertion,
) -> None:
    config = _make_config(guardrail=guardrail, agent_in_modal=agent_in_modal, use_agents_md=True)
    result = build_prompt(config)
    assert {"agents_md": result.agents_md, "short_prompt": result.cli_prompt} == snapshot
