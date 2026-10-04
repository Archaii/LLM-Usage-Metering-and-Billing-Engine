"""Print the non-sensitive shape of a PayMongo checkout session (read-only GET).

    python -m scripts.inspect_checkout_session cs_xxx
"""
import json
import sys

from app.core.config import get_settings
from app.integrations.paymongo import PayMongoClient

session_id = sys.argv[1]
resource = PayMongoClient(get_settings().paymongo_secret_key).get_checkout_session(session_id)
attrs = resource["attributes"]
intent = attrs.get("payment_intent") or {}
out = {
    "id": resource.get("id"),
    "status": attrs.get("status"),
    "paid_at": attrs.get("paid_at"),
    "payments": [
        {
            "id": p.get("id"),
            "type": p.get("type"),
            "attributes": {k: v for k, v in (p.get("attributes") or {}).items()
                           if k in ("status", "amount", "currency", "paid_at", "created_at", "payment_intent_id", "fee", "net_amount")},
        }
        for p in (attrs.get("payments") or [])
    ],
    "payment_intent": {
        "id": intent.get("id"),
        "status": (intent.get("attributes") or {}).get("status"),
        "amount": (intent.get("attributes") or {}).get("amount"),
    },
    "metadata": attrs.get("metadata"),
    "line_items": attrs.get("line_items"),
    "attribute_keys": sorted(attrs.keys()),
}
print(json.dumps(out, indent=2))
