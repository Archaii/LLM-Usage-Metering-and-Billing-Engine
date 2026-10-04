from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.api.deps import CurrentTenant
from app.api.schemas import CheckoutResponse, ErrorBody
from app.core.config import get_settings
from app.integrations.paymongo import PayMongoClient
from app.services.billing import BillingService, format_php

router = APIRouter()


@lru_cache
def _client() -> PayMongoClient:
    return PayMongoClient(get_settings().paymongo_secret_key)


def get_billing_service() -> BillingService:
    """Dependency. Tests replace it with a service that uses a fake PayMongo client."""
    return BillingService(_client())


@router.post(
    "/billing/checkout",
    response_model=CheckoutResponse,
    responses={
        401: {"model": ErrorBody}, 409: {"model": ErrorBody}, 502: {"model": ErrorBody},
    },
)
def create_checkout(
    tenant: CurrentTenant, service: Annotated[BillingService, Depends(get_billing_service)]
) -> CheckoutResponse:
    result = service.create_checkout(tenant)
    return CheckoutResponse(
        checkout_url=result.checkout_url,
        session_id=result.session_id,
        amount_php=format_php(result.amount_centavos),
        period_days=result.period_days,
    )


_PAGE = "<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"


@router.get("/billing/success", response_class=HTMLResponse)
def checkout_success() -> str:
    # Never changes the plan: only a verified webhook does.
    return _PAGE.format(
        title="Payment received",
        body="Thank you. Your plan switches to Pro as soon as PayMongo confirms the payment. "
        "Check GET /usage in a few seconds.",
    )


@router.get("/billing/cancel", response_class=HTMLResponse)
def checkout_cancel() -> str:
    return _PAGE.format(title="Checkout cancelled", body="No payment was made and your plan has not changed.")
