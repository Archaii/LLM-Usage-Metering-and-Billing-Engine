"""Helpers for payment tests: a fake PayMongo client and signed webhook delivery."""

from app.core.config import get_settings
from app.core.errors import BillingProviderError
from app.integrations.paymongo import SIGNATURE_HEADER, ProviderCheckout, build_signature_header
from scripts.send_test_webhook import build_paid_event, serialise

__all__ = ["FakePayMongoClient", "build_paid_event", "deliver", "serialise"]


class FakePayMongoClient:
    """Stands in for PayMongoClient so tests never touch the network."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []

    def create_checkout_session(self, **kwargs) -> ProviderCheckout:
        self.calls.append(kwargs)
        if self.fail:
            raise BillingProviderError("PayMongo rejected the request (HTTP 401, code unauthorized).")
        n = len(self.calls)
        return ProviderCheckout(id=f"cs_fake_{n}", checkout_url=f"https://checkout.paymongo.test/cs_fake_{n}")


def deliver(client, event: dict, *, secret: str | None = None, timestamp: int | None = None,
            header: str | None = "auto", body: bytes | None = None):
    """POST an event to the webhook. header='auto' signs it; header=None sends no signature."""
    raw = body if body is not None else serialise(event)
    secret = secret or get_settings().paymongo_webhook_secret
    headers = {"Content-Type": "application/json"}
    if header == "auto":
        headers[SIGNATURE_HEADER] = build_signature_header(secret, raw, timestamp)
    elif header is not None:
        headers[SIGNATURE_HEADER] = header
    return client.post("/webhooks/paymongo", content=raw, headers=headers)


