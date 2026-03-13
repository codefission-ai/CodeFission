"""Model pricing table and cost estimation.

Claude provides total_cost_usd in its result event. Codex only returns token
counts, so we compute cost from a pricing table.

The PRICING_TABLE is a mutable dict — update it when prices change or to add
new models.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Cost per million tokens in USD."""
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float = 0.0


@dataclass
class TokenUsage:
    """Token counts from a single turn."""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


# ── Pricing table (USD per million tokens) ────────────────────────────
# Sources: https://anthropic.com/pricing, https://openai.com/api/pricing
# Last updated: 2025-03

PRICING_TABLE: dict[str, ModelPricing] = {
    # Claude models
    "claude-opus-4-6": ModelPricing(
        input_per_mtok=15.0,
        output_per_mtok=75.0,
        cached_input_per_mtok=1.5,
    ),
    "claude-sonnet-4-6": ModelPricing(
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cached_input_per_mtok=0.3,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        input_per_mtok=0.8,
        output_per_mtok=4.0,
        cached_input_per_mtok=0.08,
    ),
    # OpenAI / Codex models
    "o4-mini": ModelPricing(
        input_per_mtok=1.10,
        output_per_mtok=4.40,
        cached_input_per_mtok=0.275,
    ),
    "codex-mini": ModelPricing(
        input_per_mtok=1.50,
        output_per_mtok=6.00,
        cached_input_per_mtok=0.375,
    ),
    "gpt-5.3-codex": ModelPricing(
        input_per_mtok=2.00,
        output_per_mtok=8.00,
        cached_input_per_mtok=0.50,
    ),
}


def estimate_cost(model: str, usage: TokenUsage) -> float | None:
    """Estimate cost in USD for a given model and token usage.

    Returns None if the model is not in the pricing table.
    """
    pricing = PRICING_TABLE.get(model)
    if pricing is None:
        return None

    cost = (
        (usage.input_tokens * pricing.input_per_mtok / 1_000_000)
        + (usage.output_tokens * pricing.output_per_mtok / 1_000_000)
        + (usage.cached_input_tokens * pricing.cached_input_per_mtok / 1_000_000)
    )
    return cost


def estimate_cost_from_raw(model: str, raw_event: dict) -> float | None:
    """Extract token counts from a raw event dict and estimate cost.

    Works with Codex's turn.completed format:
        {"usage": {"input_tokens": N, "output_tokens": N, "cached_input_tokens": N}}
    """
    usage_data = raw_event.get("usage", {})
    if not usage_data:
        return None

    usage = TokenUsage(
        input_tokens=usage_data.get("input_tokens", 0),
        output_tokens=usage_data.get("output_tokens", 0),
        cached_input_tokens=usage_data.get("cached_input_tokens", 0),
    )
    return estimate_cost(model, usage)
