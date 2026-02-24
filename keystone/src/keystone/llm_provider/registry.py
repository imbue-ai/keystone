"""Provider registry for name-based lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from keystone.llm_provider.claude import ClaudeProvider
from keystone.llm_provider.codex import CodexProvider

if TYPE_CHECKING:
    from keystone.llm_provider.base import AgentProvider

PROVIDER_REGISTRY: dict[str, type[AgentProvider]] = {
    "claude": ClaudeProvider,
    "codex": CodexProvider,
}


def get_provider(name: str, model: str | None = None) -> AgentProvider:
    """Instantiate a provider by name.

    Raises ``ValueError`` if the name is not registered.
    """
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(PROVIDER_REGISTRY.keys()))
        raise ValueError(f"Unknown LLM provider {name!r}. Available: {available}")
    return cls(model=model)
