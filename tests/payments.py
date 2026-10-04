"""Helpers for payment tests: a fake PayMongo client and signed webhook delivery."""
from app.core.config import get_settings
from app.core.errors import BillingProviderError
from app.integrations.paymongo import SIGNATURE_HEADER, ProviderCheckout, build_signature_header
from scripts.send_test_webhook import build_paid_event, serialise

__all__ = ["FakePayMongoClient", "build_paid_event", "deliver", "serialise"]

PAID_AT = 1_791_100_960


class FakePayMongoClient:
    """Stands in for PayMongoClient so tests never touch the network.

    get_checkout_session answers like PayMongo's current record: by default every session is
    settled and paid for `amount` centavos. Put a session id in `unsettled` to make it show no
    payment yet, or in `missing` to make PayMongo answer with an error.
    """

    def __init__(self, fail: bool = False, amount: int = 165_000):
        self.fail = fail
        self.amount = amount
        self.unsettled: set[str] = set()
        self.missing: set[str] = set()
        self.paid_at: dict[str, int] = {}
        self.calls: list[dict] = []
        self.retrievals: list[str] = []

    def create_checkout_session(self, **kwargs) -> ProviderCheckout:
        self.calls.append(kwargs)
        if self.fail:
            raise BillingProviderError("PayMongo rejected the request (HTTP 401, code unauthorized).")
        n = len(self.calls)
        return ProviderCheckout(id=f"cs_fake_{n}", checkout_url=f"https://checkout.paymongo.test/cs_fake_{n}")

    def get_checkout_session(self, session_id: str) -> dict:
        self.retrievals.append(session_id)
        if session_id in self.missing:
            raise BillingProviderError("PayMongo rejected the request (HTTP 404, code resource_not_found).")
        paid_at = self.paid_at.get(session_id, PAID_AT)
        payments = []
        if session_id not in self.unsettled:
            payments = [{
                "id": f"pay_{session_id}",
                "type": "payment",
                "attributes": {"status": "paid", "amount": self.amount, "currency": "PHP", "paid_at": paid_at},
            }]
        return {
            "id": session_id,
            "type": "checkout_session",
            "attributes": {"status": "active", "paid_at": paid_at if payments else None, "payments": payments},
        }


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
