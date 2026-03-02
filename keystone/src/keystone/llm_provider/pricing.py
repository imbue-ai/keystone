"""Token-based cost estimation for all supported LLM providers.

Each entry maps a model-name prefix to (input, cached_input, output) rates
in USD per 1M tokens.

Sources (February 2026):
  Anthropic: https://www.anthropic.com/pricing
  OpenAI:    https://platform.openai.com/docs/pricing
"""

# (input_per_m, cached_input_per_m, output_per_m)
_PRICING_PER_M: dict[str, tuple[float, float, float]] = {
    # Claude models
    "claude-opus-4": (15.00, 1.50, 75.00),
    "claude-haiku-4": (0.80, 0.08, 4.00),
    # OpenAI / Codex models
    "gpt-5.2": (1.75, 0.18, 14.00),
    "gpt-5.1": (1.25, 0.125, 10.00),
    "gpt-5": (1.25, 0.125, 10.00),
}

_DEFAULT_PRICING_PER_M: tuple[float, float, float] = (1.25, 0.125, 10.00)


def estimate_cost_usd(
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    model: str | None = None,
) -> float:
    """Estimate dollar cost from token counts and an optional model name.

    Cached tokens are a subset of input tokens, so we bill the non-cached
    portion at the full input rate and the cached portion at the discounted
    cached-input rate.  Cache-creation tokens are billed at 1.25x input rate
    (Anthropic) or input rate (OpenAI) — we use the higher of the two for
    conservatism when the provider is unknown.
    """
    pricing = _DEFAULT_PRICING_PER_M
    if model:
        model_lower = model.lower()
        for prefix, rates in _PRICING_PER_M.items():
            if model_lower.startswith(prefix):
                pricing = rates
                break

    input_rate, cached_rate, output_rate = pricing
    cache_creation_rate = input_rate * 1.25
    non_cached = max(input_tokens - cached_tokens, 0)
    return (
        non_cached * input_rate / 1_000_000
        + cached_tokens * cached_rate / 1_000_000
        + output_tokens * output_rate / 1_000_000
        + cache_creation_tokens * cache_creation_rate / 1_000_000
    )
