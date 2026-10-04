"""Metering: exactly-once usage events."""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg.errors

from app.api.schemas import (
    GenerateRequest,
    GenerateResponse,
    MeterUsage,
    QuotaSnapshot,
    TokenMeterUsage,
    UsageSummary,
)
from app.core.db import transaction
from app.core.errors import IdempotencyKeyReused, Unauthorized
from app.core.money import format_usd
from app.core.period import current_period, iso_z
from app.repositories import plans, tenants, usage
from app.services.pricing import PricingService
from app.services.quota import QuotaService


def canonical_json(request: GenerateRequest) -> str:
    """Sorted keys, no spaces: the same logical body always serialises the same way."""
    return json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def request_hash(request: GenerateRequest) -> str:
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MeterResult:
    status_code: int  # 201 new event, 200 replay
    body: dict
    replayed: bool


class MeterService:
    def __init__(self, quota: QuotaService | None = None, pricing: PricingService | None = None):
        self._quota = quota or QuotaService()
        self._pricing = pricing or PricingService()

    def record(
        self,
        tenant_id: UUID,
        request: GenerateRequest,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MeterResult:
        """One transaction: lock tenant, replay or reject or insert exactly one event."""
        req_hash = request_hash(request)
        now = now or datetime.now(timezone.utc)

        with transaction() as conn:
            tenant = tenants.lock_for_update(conn, tenant_id)
            if tenant is None:
                raise Unauthorized("Unknown tenant.")

            replay = self._replay(conn, tenant_id, idempotency_key, req_hash)
            if replay is not None:
                return replay

            plan = plans.get(conn, tenant.plan_code)
            period = current_period(now)
            used = usage.totals_for_period(conn, tenant_id, period.start, period.end)
            requested_tokens = request.tokens.quota_tokens

            self._quota.check(
                plan=plan,
                billing_status=tenant.billing_status,
                used_calls=used.api_calls,
                used_tokens=used.quota_tokens,
                requested_calls=1,
                requested_tokens=requested_tokens,
                period=period,
                now=now,
            )

            cost = self._pricing.cost(request.tokens)
            event_id = uuid4()
            response = GenerateResponse(
                event_id=str(event_id),
                tenant_id=str(tenant_id),
                output=f"[simulated] response to: {request.prompt}",
                tokens=request.tokens,
                cost=cost,
                quota=QuotaSnapshot(
                    api_calls_used=used.api_calls + 1,
                    api_call_limit=plan.api_call_limit,
                    tokens_used=used.quota_tokens + requested_tokens,
                    token_limit=plan.token_limit,
                ),
                created_at=iso_z(now),
            )
            body = response.model_dump(mode="json")

            try:
                # Savepoint: a unique violation must not poison the outer transaction.
                with conn.transaction():
                    usage.insert_event(
                        conn,
                        tenant_id=tenant_id,
                        event_id=event_id,
                        idempotency_key=idempotency_key,
                        request_hash=req_hash,
                        api_calls=1,
                        input_tokens=request.tokens.input_tokens,
                        cached_input_tokens=request.tokens.cached_input_tokens,
                        output_tokens=request.tokens.output_tokens,
                        reasoning_tokens=request.tokens.reasoning_tokens,
                        cost_micros=cost.total_micros,
                        response_status=201,
                        response_body=body,
                        created_at=now,
                    )
            except psycopg.errors.UniqueViolation:
                # Should be impossible under the tenant row lock; fall back to the replay path.
                replay = self._replay(conn, tenant_id, idempotency_key, req_hash)
                if replay is None:
                    raise
                return replay

            return MeterResult(status_code=201, body=body, replayed=False)

    @staticmethod
    def _replay(conn, tenant_id: UUID, idempotency_key: str, req_hash: str) -> MeterResult | None:
        stored = usage.find_by_key(conn, tenant_id, idempotency_key)
        if stored is None:
            return None
        if stored.request_hash != req_hash:
            raise IdempotencyKeyReused(
                "This Idempotency-Key was already used with a different request body. "
                "Use a new key for a new request, or resend the original body.",
                {"idempotency_key": idempotency_key},
            )
        return MeterResult(status_code=200, body=stored.response_body, replayed=True)

    def usage_summary(self, tenant_id: UUID, now: datetime | None = None) -> UsageSummary:
        """Current-month rollup: sum counts per category, price them once, add the base fee."""
        now = now or datetime.now(timezone.utc)
        period = current_period(now)
        with transaction() as conn:
            tenant = tenants.get_by_id(conn, tenant_id)
            if tenant is None:
                raise Unauthorized("Unknown tenant.")
            plan = plans.get(conn, tenant.plan_code)
            totals = usage.totals_for_period(conn, tenant_id, period.start, period.end)

        tokens_used = totals.quota_tokens
        api_micros = self._pricing.api_call_micros(totals.api_calls)
        token_micros = self._pricing.token_micros(
            totals.input_tokens,
            totals.cached_input_tokens,
            totals.output_tokens,
            totals.reasoning_tokens,
        )
        total_micros = plan.base_fee_micros + api_micros + token_micros
        return UsageSummary(
            tenant_id=str(tenant_id),
            plan=plan.code,
            billing_status=tenant.billing_status,
            period_start=iso_z(period.start),
            period_end=iso_z(period.end),
            api_calls=MeterUsage(
                used=totals.api_calls,
                limit=plan.api_call_limit,
                remaining=max(0, plan.api_call_limit - totals.api_calls),
                cost_micros=api_micros,
            ),
            tokens=TokenMeterUsage(
                used=tokens_used,
                limit=plan.token_limit,
                remaining=max(0, plan.token_limit - tokens_used),
                cost_micros=token_micros,
                breakdown={
                    "input": totals.input_tokens,
                    "cached_input": totals.cached_input_tokens,
                    "fresh_input": totals.input_tokens - totals.cached_input_tokens,
                    "output": totals.output_tokens,
                    "reasoning": totals.reasoning_tokens,
                },
            ),
            base_fee_micros=plan.base_fee_micros,
            total_micros=total_micros,
            total_usd=format_usd(total_micros),
        )
