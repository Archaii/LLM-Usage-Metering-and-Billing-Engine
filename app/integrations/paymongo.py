"""PayMongo: the REST client and webhook signature handling. The only module that talks to it."""
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

import httpx

from app.core.errors import BillingProviderError, InvalidSignature

log = logging.getLogger(__name__)

API_BASE = "https://api.paymongo.com/v1"
SIGNATURE_HEADER = "Paymongo-Signature"


# --- Webhook signature -------------------------------------------------------------------


def compute_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    """HMAC-SHA256 over "<timestamp>.<raw body>", lowercase hex."""
    message = f"{timestamp}.".encode("utf-8") + raw_body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_signature_header(secret: str, raw_body: bytes, timestamp: int | None = None) -> str:
    """A test-mode Paymongo-Signature header value. Used by tests and scripts/send_test_webhook.py."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    return f"t={timestamp},te={compute_signature(secret, timestamp, raw_body)},li="


def _parse_header(header: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for item in header.split(","):
        key, sep, value = item.strip().partition("=")
        if sep:
            parts[key] = value
    return parts


def verify_signature(
    raw_body: bytes,
    header: str | None,
    secret: str,
    tolerance_seconds: int,
    now: float | None = None,
) -> None:
    """Raise InvalidSignature unless the header proves PayMongo (test mode) sent this exact body.

    Only the test-mode value (te) is accepted. The live value (li) is ignored on purpose:
    this app is test-mode only.
    """
    if not header:
        raise InvalidSignature("The Paymongo-Signature header is missing.")
    parts = _parse_header(header)
    try:
        timestamp = int(parts["t"])
        test_signature = parts["te"]
    except (KeyError, ValueError):
        raise InvalidSignature("The Paymongo-Signature header is malformed.") from None
    if not test_signature:
        raise InvalidSignature("The Paymongo-Signature header has no test-mode signature.")

    expected = compute_signature(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, test_signature):
        raise InvalidSignature("The webhook signature does not match the request body.")

    now = time.time() if now is None else now
    if tolerance_seconds > 0 and abs(now - timestamp) > tolerance_seconds:
        raise InvalidSignature("The webhook timestamp is outside the allowed tolerance.")


# --- REST client -------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderCheckout:
    id: str
    checkout_url: str


class PayMongoClient:
    """Thin client. Errors become BillingProviderError with no key and no raw response body."""

    def __init__(
        self,
        secret_key: str,
        base_url: str = API_BASE,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            auth=(secret_key, ""),  # HTTP Basic: key as username, empty password
            timeout=timeout,
            transport=transport,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )

    def create_checkout_session(
        self,
        *,
        amount_centavos: int,
        currency: str,
        name: str,
        description: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
    ) -> ProviderCheckout:
        payload = {
            "data": {
                "attributes": {
                    "line_items": [
                        {"currency": currency, "amount": amount_centavos, "name": name, "quantity": 1}
                    ],
                    "payment_method_types": ["card"],
                    "description": description,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "metadata": metadata,
                }
            }
        }
        data = self._post("/checkout_sessions", payload)
        try:
            return ProviderCheckout(
                id=data["data"]["id"],
                checkout_url=data["data"]["attributes"]["checkout_url"],
            )
        except (KeyError, TypeError):
            raise BillingProviderError("PayMongo returned an unexpected checkout response.") from None

    def get_checkout_session(self, session_id: str) -> dict:
        """The session as PayMongo holds it now (the webhook only carries a snapshot)."""
        data = self._request("GET", f"/checkout_sessions/{session_id}")
        resource = data.get("data")
        if not isinstance(resource, dict):
            raise BillingProviderError("PayMongo returned an unexpected checkout response.")
        return resource

    def create_webhook(self, url: str, events: list[str]) -> dict:
        """Returns the raw data object; the signing secret (if present) is data.attributes.secret_key."""
        data = self._post("/webhooks", {"data": {"attributes": {"url": url, "events": events}}})
        return data.get("data", {})

    def _post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = self._client.request(method, self._base_url + path, json=payload)
        except httpx.HTTPError as exc:
            log.warning("PayMongo request failed: %s", type(exc).__name__)
            raise BillingProviderError("PayMongo could not be reached. Try again shortly.") from None
        if response.status_code >= 400:
            code = _first_error_code(response)
            log.warning("PayMongo rejected %s: HTTP %s code=%s", path, response.status_code, code)
            raise BillingProviderError(
                f"PayMongo rejected the request (HTTP {response.status_code}, code {code})."
            )
        try:
            return response.json()
        except ValueError:
            raise BillingProviderError("PayMongo returned a response that is not JSON.") from None


def _first_error_code(response: httpx.Response) -> str:
    try:
        return str(response.json()["errors"][0]["code"])
    except (ValueError, KeyError, IndexError, TypeError):
        return "unknown"
