"""Plain row types shared between repositories and services."""
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    tenant_id: UUID
    plan_code: str
    amount_centavos: int
    status: str


@dataclass(frozen=True)
class PaymentEvent:
    event_id: str
    type: str
    event_created: int
    payload: dict
    status: str
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
