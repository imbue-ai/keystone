"""LLM provider abstraction for swappable agent backends.

To add a new provider, subclass AgentProvider and register it in PROVIDER_REGISTRY.
"""

from keystone.llm_provider.base import (
    AgentCostEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentProvider,
    AgentTextEvent,
    AgentToolCallEvent,
    AgentToolResultEvent,
)
from keystone.llm_provider.registry import PROVIDER_REGISTRY, get_provider

__all__ = [
    "PROVIDER_REGISTRY",
    "AgentCostEvent",
    "AgentErrorEvent",
    "AgentEvent",
    "AgentProvider",
    "AgentTextEvent",
    "AgentToolCallEvent",
    "AgentToolResultEvent",
    "get_provider",
]
