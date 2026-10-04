"""Register your public tunnel URL with PayMongo and print the signing secret once.

    python -m scripts.register_webhook https://<tunnel-host>/webhooks/paymongo

Put the printed secret in .env as PAYMONGO_WEBHOOK_SECRET. If PayMongo does not return a
secret, copy it from the dashboard (Developers > Webhooks) instead.
"""
import argparse

from app.core.config import get_settings
from app.core.errors import BillingProviderError
from app.integrations.paymongo import PayMongoClient

EVENTS = ["checkout_session.payment.paid"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="public HTTPS URL of POST /webhooks/paymongo")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        raise SystemExit("PayMongo needs a public https:// URL. Start a tunnel (ngrok http 8000) first.")

    client = PayMongoClient(get_settings().paymongo_secret_key)
    try:
        webhook = client.create_webhook(args.url, EVENTS)
    except BillingProviderError as exc:
        raise SystemExit(f"Registration failed: {exc.message}") from None

    print(f"Registered webhook {webhook.get('id', '<unknown id>')} for {', '.join(EVENTS)}")
    secret = (webhook.get("attributes") or {}).get("secret_key")
    if secret:
        print(f"Signing secret (shown once, put it in .env): PAYMONGO_WEBHOOK_SECRET={secret}")
    else:
        print("PayMongo returned no signing secret. Copy it from the dashboard: Developers > Webhooks.")


if __name__ == "__main__":
    main()
