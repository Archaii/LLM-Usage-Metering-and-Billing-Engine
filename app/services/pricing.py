"""Cost calculation.

Phase 2 placeholder: every cost is 0. Phase 4 replaces this with the real
micro-USD rules from spec section 7 (cached input, reasoning-as-output).
"""
from app.api.schemas import CostBreakdown, TokenUsage


class PricingService:
    def cost(self, tokens: TokenUsage, api_calls: int = 1) -> CostBreakdown:
        return CostBreakdown(
            api_call_micros=0,
            fresh_input_micros=0,
            cached_input_micros=0,
            output_micros=0,
            total_micros=0,
            total_usd="0.000000",
        )
