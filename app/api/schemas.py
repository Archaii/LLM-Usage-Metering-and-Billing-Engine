"""Pydantic models for every request and response body (spec section 8.2)."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TokenUsage(BaseModel):
    input_tokens: int = Field(ge=0, le=1_000_000)
    cached_input_tokens: int = Field(ge=0, le=1_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000)
    reasoning_tokens: int = Field(ge=0, le=1_000_000)

    @model_validator(mode="after")
    def cached_within_input(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        return self

    @property
    def quota_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4_000)
    tokens: TokenUsage  # simulated counts; no model is called


class CostBreakdown(BaseModel):
    api_call_micros: int
    fresh_input_micros: int
    cached_input_micros: int
    output_micros: int  # output + reasoning
    total_micros: int
    total_usd: str  # "0.012850", formatted from integers


class QuotaSnapshot(BaseModel):
    api_calls_used: int
    api_call_limit: int
    tokens_used: int
    token_limit: int


class GenerateResponse(BaseModel):
    event_id: str
    tenant_id: str
    output: str  # simulated text
    tokens: TokenUsage
    cost: CostBreakdown
    quota: QuotaSnapshot  # after this request
    created_at: str  # ISO 8601 UTC


class MeterUsage(BaseModel):
    used: int
    limit: int
    remaining: int
    cost_micros: int


class TokenMeterUsage(MeterUsage):
    breakdown: dict[str, int]  # input, cached_input, fresh_input, output, reasoning


class UsageSummary(BaseModel):
    tenant_id: str
    plan: Literal["free", "pro"]
    billing_status: Literal["ok", "past_due"]
    period_start: str  # first instant of the month, UTC
    period_end: str  # first instant of next month, UTC
    api_calls: MeterUsage
    tokens: TokenMeterUsage
    base_fee_micros: int
    total_micros: int
    total_usd: str


class ErrorBody(BaseModel):
    error: str  # machine code, e.g. "quota_exceeded"
    message: str  # human sentence explaining why
    details: dict | None = None  # e.g. {"meter": "tokens", "used": 99_000, "requested": 1_500, "limit": 100_000}
    upgrade_url: str | None = None


class PlanInfo(BaseModel):
    code: str
    name: str
    api_call_limit: int
    token_limit: int
    base_fee_micros: int


class RatesInfo(BaseModel):
    api_call_micros: int
    input_micros_per_million: int
    cached_input_micros_per_million: int
    output_micros_per_million: int
    reasoning_micros_per_million: int


class PlansResponse(BaseModel):
    currency: str
    micros_per_usd: int
    plans: list[PlanInfo]
    rates: RatesInfo
