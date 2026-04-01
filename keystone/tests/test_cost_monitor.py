"""Tests for the ccusage-based cost monitoring in ModalAgentRunner."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from keystone.modal.modal_runner import ModalAgentRunner
from keystone.schema import InferenceCost, TokenSpending


@pytest.fixture
def runner() -> ModalAgentRunner:
    """Create a ModalAgentRunner without actually connecting to Modal."""
    return ModalAgentRunner(
        agent_time_limit_seconds=60,
        docker_registry_mirror=None,
    )


class TestCostMonitor:
    """Tests for ModalAgentRunner._cost_monitor."""

    def test_terminates_agent_when_over_budget(self, runner: ModalAgentRunner) -> None:
        """Monitor should call agent.terminate() when cost exceeds budget."""
        agent = MagicMock()
        over_budget_cost = InferenceCost(
            cost_usd=5.0,
            token_spending=TokenSpending(input=1000, cached=0, output=500, cache_creation=0),
        )

        with patch.object(runner, "run_ccusage", return_value=over_budget_cost):
            runner._agent_done.clear()
            runner._cost_limit_exceeded = False

            monitor = threading.Thread(
                target=runner._cost_monitor,
                args=(1.0, "claude", agent, 1),  # poll every 1s, budget $1
                daemon=True,
            )
            monitor.start()
            monitor.join(timeout=5)

        assert runner._cost_limit_exceeded is True
        agent.terminate.assert_called_once()

    def test_does_not_terminate_when_under_budget(self, runner: ModalAgentRunner) -> None:
        """Monitor should not terminate agent when cost is below budget."""
        agent = MagicMock()
        under_budget_cost = InferenceCost(
            cost_usd=0.50,
            token_spending=TokenSpending(input=500, cached=0, output=200, cache_creation=0),
        )

        with patch.object(runner, "run_ccusage", return_value=under_budget_cost):
            runner._agent_done.clear()
            runner._cost_limit_exceeded = False

            monitor = threading.Thread(
                target=runner._cost_monitor,
                args=(1.0, "claude", agent, 1),
                daemon=True,
            )
            monitor.start()
            # Let it poll once, then signal done
            time.sleep(1.5)
            runner._agent_done.set()
            monitor.join(timeout=5)

        assert runner._cost_limit_exceeded is False
        agent.terminate.assert_not_called()

    def test_terminates_on_ccusage_failure(self, runner: ModalAgentRunner) -> None:
        """Monitor should conservatively terminate the agent if ccusage fails."""
        agent = MagicMock()

        def failing_ccusage(
            provider_name: str,  # noqa: ARG001
            timeout_secs: int | None = None,  # noqa: ARG001
        ) -> InferenceCost:
            raise RuntimeError("ccusage crashed")

        with patch.object(runner, "run_ccusage", side_effect=failing_ccusage):
            runner._agent_done.clear()
            runner._cost_limit_exceeded = False

            monitor = threading.Thread(
                target=runner._cost_monitor,
                args=(1.0, "claude", agent, 1),
                daemon=True,
            )
            monitor.start()
            monitor.join(timeout=5)

        # Should have terminated on first failure (conservative behavior)
        assert runner._cost_limit_exceeded is True
        agent.terminate.assert_called_once()

    def test_stops_when_agent_done_signaled(self, runner: ModalAgentRunner) -> None:
        """Monitor should exit promptly when _agent_done is set."""
        agent = MagicMock()

        with patch.object(runner, "run_ccusage", return_value=InferenceCost(cost_usd=0.0)):
            runner._agent_done.clear()
            runner._cost_limit_exceeded = False

            monitor = threading.Thread(
                target=runner._cost_monitor,
                args=(1.0, "claude", agent, 60),  # long poll interval
                daemon=True,
            )
            monitor.start()
            # Signal immediately
            runner._agent_done.set()
            monitor.join(timeout=3)

        assert not monitor.is_alive()
        assert runner._cost_limit_exceeded is False
        agent.terminate.assert_not_called()
