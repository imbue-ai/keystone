"""Timeout hierarchy for the Keystone pipeline.

There are two nested timeouts, each derived from the previous:

1. **Agent timeout** (``agent_time_limit_seconds``): How long the coding agent
   is allowed to work.  Enforced inside the Modal sandbox via the Linux
   ``timeout`` command wrapper.

2. **Modal sandbox timeout** (``sandbox_timeout_seconds``): The hard lifetime
   of the Modal sandbox.  Must be longer than the agent timeout because the
   sandbox also runs Docker image builds and test execution *after* the agent
   finishes.  Set to **2x agent timeout**.

By deriving the sandbox timeout from the agent timeout, we guarantee they
never go out of sync.
"""

_MINIMUM_SANDBOX_TIMEOUT_SECONDS = 30
"""Modal enforces a minimum sandbox timeout of 10 s, but we need extra
headroom so that ccusage can collect cost data after the agent is killed.
30 s is a safe floor."""


def sandbox_timeout_seconds(agent_time_limit_seconds: int) -> int:
    """Modal sandbox lifetime -- 2x the agent working timeout, min 30 s.

    The sandbox must stay alive for the full agent run *plus* Docker image
    build and test execution, which can each take up to 30 min.
    The 30 s floor ensures Modal accepts the timeout (minimum 10 s) and
    gives enough time for ccusage to report token costs after the agent
    is terminated.
    """
    return max(agent_time_limit_seconds * 2, _MINIMUM_SANDBOX_TIMEOUT_SECONDS)
