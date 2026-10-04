"""Build and post a correctly signed SIMULATED PayMongo event (checkout_session.payment.paid).

This is not a PayMongo-originated delivery. It exists so forged, replayed, and duplicate
deliveries are repeatable without the dashboard. The event body is a snapshot shaped like the
real one (payments empty, event created_at null). To grant Pro, the worker asks PayMongo for the
session's current state, so --session-id must name a real, paid test checkout session.

    python -m scripts.send_test_webhook --tenant-id <uuid> --session-id cs_xxx [--event-id evt_...]
    python -m scripts.send_test_webhook ... --bad-signature      # expect 400
    python -m scripts.send_test_webhook ... --event-id evt_x     # run twice to see duplicate: true
"""
import argparse
import json
import secrets

import httpx

from app.core.config import get_pricing, get_settings
from app.integrations.paymongo import SIGNATURE_HEADER, build_signature_header


def build_paid_event(
    *, tenant_id: str, session_id: str | None = None, event_id: str | None = None
) -> dict:
    """An event shaped like the real captured delivery (see tests/fixtures and spec section 14.3)."""
    suffix = secrets.token_hex(6)
    amount = get_pricing().checkout.pro_amount_centavos
    return {
        "data": {
            "id": event_id or f"evt_sim_{suffix}",
            "type": "event",
            "attributes": {
                "type": "checkout_session.payment.paid",
                "livemode": False,
                "data": {
                    "id": session_id or f"cs_sim_{suffix}",
                    "type": "checkout_session",
                    "attributes": {
                        "line_items": [
                            {"amount": amount, "currency": "PHP", "name": "Pro plan", "quantity": 1}
                        ],
                        "metadata": {"tenant_id": tenant_id},
                        "paid_at": None,
                        "payments": [],
                        "status": "active",
                    },
                },
                "previous_data": {},
                "pending_webhooks": 1,
                "created_at": None,
                "updated_at": None,
            },
        }
    }


def serialise(event: dict) -> bytes:
    return json.dumps(event, separators=(",", ":")).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000/webhooks/paymongo")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--session-id", required=True, help="a real, paid test checkout session (cs_...)")
    parser.add_argument("--event-id")
    parser.add_argument("--bad-signature", action="store_true", help="sign with the wrong secret")
    args = parser.parse_args()

    event = build_paid_event(tenant_id=args.tenant_id, session_id=args.session_id, event_id=args.event_id)
    body = serialise(event)
    secret = "not-the-real-secret" if args.bad_signature else get_settings().paymongo_webhook_secret
    response = httpx.post(
        args.url,
        content=body,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: build_signature_header(secret, body)},
        timeout=15,
    )
    print(f"event_id={event['data']['id']} session_id={args.session_id}")
    print(f"HTTP {response.status_code} {response.text}")


if __name__ == "__main__":
    main()
