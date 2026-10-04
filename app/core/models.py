"""Plain row types shared between repositories and services."""
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    api_call_limit: int
    token_limit: int
    base_fee_micros: int


@dataclass(frozen=True)
class Tenant:
    id: UUID
    name: str
    plan_code: str
    billing_status: str
    stripe_customer_id: str | None


@dataclass(frozen=True)
class UsageTotals:
    api_calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int

    @property
    def quota_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens
