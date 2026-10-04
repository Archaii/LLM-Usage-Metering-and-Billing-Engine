"""Cost calculation in integer micro-USD (spec section 7)."""
from app.api.schemas import CostBreakdown, TokenUsage
from app.core.config import Rates, get_pricing
from app.core.money import format_usd, micros_from_raw


class PricingService:
    def __init__(self, rates: Rates | None = None):
        self._rates = rates or get_pricing().rates

    def token_micros(
        self, input_tokens: int, cached_input_tokens: int, output_tokens: int, reasoning_tokens: int
    ) -> int:
        """Token cost: each category priced at its own rate, summed raw, divided once (spec 7.2)."""
        r = self._rates
        raw = (
            (input_tokens - cached_input_tokens) * r.input_micros_per_million
            + cached_input_tokens * r.cached_input_micros_per_million
            + (output_tokens + reasoning_tokens) * r.output_micros_per_million
        )
        return micros_from_raw(raw)

    def api_call_micros(self, api_calls: int) -> int:
        return api_calls * self._rates.api_call_micros

    def cost_for_counts(
        self,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        api_calls: int,
    ) -> CostBreakdown:
        """Price summed counts once. Used for one event and for the monthly rollup alike.

        The three category lines are each rounded on their own for display, so they can differ
        from the rounded token total by one micro. `total_micros` is the authoritative figure.
        """
        r = self._rates
        api_micros = self.api_call_micros(api_calls)
        total = api_micros + self.token_micros(
            input_tokens, cached_input_tokens, output_tokens, reasoning_tokens
        )
        return CostBreakdown(
            api_call_micros=api_micros,
            fresh_input_micros=micros_from_raw(
                (input_tokens - cached_input_tokens) * r.input_micros_per_million
            ),
            cached_input_micros=micros_from_raw(cached_input_tokens * r.cached_input_micros_per_million),
            output_micros=micros_from_raw(
                (output_tokens + reasoning_tokens) * r.output_micros_per_million
            ),
            total_micros=total,
            total_usd=format_usd(total),
        )

    def cost(self, tokens: TokenUsage, api_calls: int = 1) -> CostBreakdown:
        return self.cost_for_counts(
            tokens.input_tokens,
            tokens.cached_input_tokens,
            tokens.output_tokens,
            tokens.reasoning_tokens,
            api_calls,
        )
