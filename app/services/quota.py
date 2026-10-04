"""Quota rules: allowed only if used + requested <= limit for both meters."""
from datetime import datetime

from app.core.config import get_pricing
from app.core.errors import PaymentRequired, QuotaExceeded, UpgradeRequired
from app.core.models import Plan
from app.core.period import Period, iso_z


class QuotaService:
    def check(
        self,
        *,
        plan: Plan,
        billing_status: str,
        used_calls: int,
        used_tokens: int,
        requested_calls: int,
        requested_tokens: int,
        period: Period,
        now: datetime,
    ) -> None:
        """Raise a domain error if the request must be rejected. Order: billing, calls, tokens."""
        if billing_status == "past_due":
            raise PaymentRequired(
                "Your billing status is past due. Update your payment method to keep using the API.",
                {"billing_status": billing_status},
            )

        meters = (
            ("api_calls", "API call", "API calls", used_calls, requested_calls,
             plan.api_call_limit, "api_call_limit"),
            ("tokens", "token", "tokens", used_tokens, requested_tokens,
             plan.token_limit, "token_limit"),
        )
        for meter, label, unit, used, requested, limit, limit_field in meters:
            if used + requested > limit:
                self._reject(
                    plan=plan, meter=meter, label=label, unit=unit, used=used,
                    requested=requested, limit=limit, limit_field=limit_field,
                    period=period, now=now,
                )

    def _reject(
        self, *, plan: Plan, meter: str, label: str, unit: str, used: int,
        requested: int, limit: int, limit_field: str, period: Period, now: datetime,
    ) -> None:
        period_end = iso_z(period.end)
        details = {
            "meter": meter,
            "used": used,
            "requested": requested,
            "limit": limit,
            "period_end": period_end,
        }
        base = (
            f"{plan.name} plan {label} quota reached: {used:,} of {limit:,} {unit} "
            f"used this month and this request needs {requested:,}."
        )
        if plan.code == "free":
            pro_limit = getattr(get_pricing().plans["pro"], limit_field)
            raise UpgradeRequired(
                f"{base} Upgrade to Pro for {pro_limit:,} {unit} per month.",
                details,
                upgrade_url="/billing/checkout",
            )
        raise QuotaExceeded(
            f"{base} The quota resets at {period_end}.",
            details,
            retry_after=period.seconds_until_reset(now),
        )
