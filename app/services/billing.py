"""Checkout: create a PayMongo hosted checkout for one Pro period."""
from dataclasses import dataclass

from app.core.config import get_pricing, get_settings
from app.core.db import transaction
from app.core.errors import AlreadyPro
from app.core.models import Tenant
from app.integrations.paymongo import PayMongoClient
from app.repositories import checkout_sessions


@dataclass(frozen=True)
class CheckoutResult:
    checkout_url: str
    session_id: str
    amount_centavos: int
    period_days: int


def format_php(centavos: int) -> str:
    """Integer centavos to "1650.00" without floats."""
    return f"{centavos // 100}.{centavos % 100:02d}"


class BillingService:
    def __init__(self, client: PayMongoClient):
        self._client = client

    def create_checkout(self, tenant: Tenant) -> CheckoutResult:
        if tenant.plan_code == "pro":
            raise AlreadyPro(
                "This tenant is already on the Pro plan. Buy another period after the current one expires."
            )

        checkout = get_pricing().checkout
        base_url = get_settings().app_base_url
        # The provider call runs outside any database transaction. If storing the row fails
        # afterwards, the webhook can still find the tenant through the session metadata.
        session = self._client.create_checkout_session(
            amount_centavos=checkout.pro_amount_centavos,
            currency=checkout.currency,
            name=f"Pro plan - {checkout.pro_period_days} days",
            description="Usage Metering & Billing Engine - Pro plan",
            success_url=f"{base_url}/billing/success",
            cancel_url=f"{base_url}/billing/cancel",
            metadata={"tenant_id": str(tenant.id)},
        )
        with transaction() as conn:
            checkout_sessions.insert(
                conn,
                session_id=session.id,
                tenant_id=tenant.id,
                plan_code="pro",
                amount_centavos=checkout.pro_amount_centavos,
            )
        return CheckoutResult(
            checkout_url=session.checkout_url,
            session_id=session.id,
            amount_centavos=checkout.pro_amount_centavos,
            period_days=checkout.pro_period_days,
        )
