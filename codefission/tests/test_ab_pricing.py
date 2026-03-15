"""Tests for the pricing module."""

import pytest

from agentbridge.pricing import (
    PRICING_TABLE,
    ModelPricing,
    TokenUsage,
    estimate_cost,
    estimate_cost_from_raw,
)


class TestModelPricing:
    def test_pricing_table_has_claude_models(self):
        assert "claude-opus-4-6" in PRICING_TABLE
        assert "claude-sonnet-4-6" in PRICING_TABLE
        assert "claude-haiku-4-5-20251001" in PRICING_TABLE

    def test_pricing_table_has_codex_models(self):
        assert "o4-mini" in PRICING_TABLE
        assert "codex-mini" in PRICING_TABLE
        assert "gpt-5.3-codex" in PRICING_TABLE

    def test_pricing_is_frozen(self):
        p = PRICING_TABLE["claude-opus-4-6"]
        with pytest.raises(AttributeError):
            p.input_per_mtok = 999  # type: ignore[misc]


class TestEstimateCost:
    def test_known_model(self):
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=500_000)
        cost = estimate_cost("claude-opus-4-6", usage)
        assert cost is not None
        # 1M * 15/1M + 0.5M * 75/1M = 15 + 37.5 = 52.5
        assert cost == pytest.approx(52.5)

    def test_with_cached_tokens(self):
        usage = TokenUsage(
            input_tokens=500_000,
            output_tokens=100_000,
            cached_input_tokens=200_000,
        )
        cost = estimate_cost("claude-sonnet-4-6", usage)
        assert cost is not None
        # 0.5M * 3/1M + 0.1M * 15/1M + 0.2M * 0.3/1M
        # = 1.5 + 1.5 + 0.06 = 3.06
        assert cost == pytest.approx(3.06)

    def test_unknown_model_returns_none(self):
        usage = TokenUsage(input_tokens=100)
        assert estimate_cost("unknown-model", usage) is None

    def test_zero_usage(self):
        usage = TokenUsage()
        cost = estimate_cost("o4-mini", usage)
        assert cost == 0.0

    def test_codex_model_cost(self):
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = estimate_cost("gpt-5.3-codex", usage)
        assert cost is not None
        # 1M * 2/1M + 1M * 8/1M = 2 + 8 = 10
        assert cost == pytest.approx(10.0)


class TestEstimateCostFromRaw:
    def test_codex_turn_completed(self):
        raw = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100_000,
                "output_tokens": 50_000,
                "cached_input_tokens": 10_000,
            },
        }
        cost = estimate_cost_from_raw("o4-mini", raw)
        assert cost is not None
        # 100k * 1.10/1M + 50k * 4.40/1M + 10k * 0.275/1M
        # = 0.11 + 0.22 + 0.00275 = 0.33275
        assert cost == pytest.approx(0.33275)

    def test_missing_usage_returns_none(self):
        raw = {"type": "turn.completed"}
        assert estimate_cost_from_raw("o4-mini", raw) is None

    def test_empty_usage_returns_none(self):
        raw = {"type": "turn.completed", "usage": {}}
        assert estimate_cost_from_raw("o4-mini", raw) is None

    def test_unknown_model_returns_none(self):
        raw = {
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        assert estimate_cost_from_raw("nonexistent", raw) is None
