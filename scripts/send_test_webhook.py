"""Build and post a correctly signed SIMULATED PayMongo event (checkout_session.payment.paid).

This is not a PayMongo-originated delivery. It exists so forged, replayed, and duplicate
deliveries are repeatable without the dashboard. Real events come from a real test checkout.

    python -m scripts.send_test_webhook --tenant-id <uuid> [--session-id cs_...] [--event-id evt_...]
    python -m scripts.send_test_webhook ... --bad-signature      # expect 400
    python -m scripts.send_test_webhook ... --event-id evt_x     # run twice to see duplicate: true
"""
import argparse
import json
import secrets
import time

import httpx

from app.core.config import get_pricing, get_settings
from app.integrations.paymongo import SIGNATURE_HEADER, build_signature_header


def build_paid_event(
    *,
    tenant_id: str,
    session_id: str | None = None,
    event_id: str | None = None,
    payment_id: str | None = None,
    amount_centavos: int | None = None,
    created_at: int | None = None,
) -> dict:
    """An event shaped like spec section 14.3."""
    suffix = secrets.token_hex(6)
    amount = get_pricing().checkout.pro_amount_centavos if amount_centavos is None else amount_centavos
    return {
        "data": {
            "id": event_id or f"evt_sim_{suffix}",
            "type": "event",
            "attributes": {
                "type": "checkout_session.payment.paid",
                "livemode": False,
                "created_at": int(time.time()) if created_at is None else created_at,
                "data": {
                    "id": session_id or f"cs_sim_{suffix}",
                    "type": "checkout_session",
                    "attributes": {
                        "payments": [
                            {
                                "id": payment_id or f"pay_sim_{suffix}",
                                "type": "payment",
                                "attributes": {"status": "paid", "amount": amount, "currency": "PHP"},
                            }
                        ],
                        "metadata": {"tenant_id": tenant_id},
                    },
                },
            },
        }
    }


def serialise(event: dict) -> bytes:
    return json.dumps(event, separators=(",", ":")).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000/webhooks/paymongo")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--event-id")
    parser.add_argument("--payment-id")
    parser.add_argument("--amount-centavos", type=int)
    parser.add_argument("--bad-signature", action="store_true", help="sign with the wrong secret")
    args = parser.parse_args()

    event = build_paid_event(
        tenant_id=args.tenant_id,
        session_id=args.session_id,
        event_id=args.event_id,
        payment_id=args.payment_id,
        amount_centavos=args.amount_centavos,
    )
    body = serialise(event)
    secret = "not-the-real-secret" if args.bad_signature else get_settings().paymongo_webhook_secret
    response = httpx.post(
        args.url,
        content=body,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: build_signature_header(secret, body)},
        timeout=15,
    )
    print(f"event_id={event['data']['id']} session_id={event['data']['attributes']['data']['id']}")
    print(f"HTTP {response.status_code} {response.text}")


if __name__ == "__main__":
    main()
