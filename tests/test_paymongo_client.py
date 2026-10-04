import base64
import json

import httpx
import pytest

from app.core.errors import BillingProviderError, InvalidSignature
from app.integrations.paymongo import (
    PayMongoClient,
    build_signature_header,
    compute_signature,
    verify_signature,
)

SECRET = "whsk_unit_test_secret"
BODY = b'{"data":{"id":"evt_1"}}'


# --- signature ---------------------------------------------------------------------------


def test_signature_is_hmac_sha256_over_timestamp_dot_body():
    # Independent computation of the documented scheme: HMAC-SHA256(secret, "<t>.<body>").
    import hashlib
    import hmac

    expected = hmac.new(SECRET.encode(), b"1700000000." + BODY, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, 1_700_000_000, BODY) == expected


def test_valid_signature_passes():
    header = build_signature_header(SECRET, BODY, timestamp=1_700_000_000)
    verify_signature(BODY, header, SECRET, tolerance_seconds=300, now=1_700_000_100)


def test_wrong_secret_fails():
    header = build_signature_header("other-secret", BODY, timestamp=1_700_000_000)
    with pytest.raises(InvalidSignature):
        verify_signature(BODY, header, SECRET, tolerance_seconds=300, now=1_700_000_000)


def test_timestamp_tolerance_is_enforced_and_can_be_disabled():
    header = build_signature_header(SECRET, BODY, timestamp=1_700_000_000)
    with pytest.raises(InvalidSignature):
        verify_signature(BODY, header, SECRET, tolerance_seconds=300, now=1_700_000_301)
    verify_signature(BODY, header, SECRET, tolerance_seconds=0, now=1_800_000_000)  # 0 disables


def test_header_with_spaces_after_commas_is_accepted():
    sig = compute_signature(SECRET, 1_700_000_000, BODY)
    verify_signature(BODY, f"t=1700000000, te={sig}, li=", SECRET, tolerance_seconds=0)


# --- REST client -------------------------------------------------------------------------


def _client(handler) -> PayMongoClient:
    return PayMongoClient("sk_test_unit_secret", transport=httpx.MockTransport(handler))


def _checkout(client: PayMongoClient):
    return client.create_checkout_session(
        amount_centavos=165_000, currency="PHP", name="Pro", description="d",
        success_url="https://x/s", cancel_url="https://x/c", metadata={"tenant_id": "t"},
    )


def test_create_checkout_session_sends_basic_auth_and_parses_the_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "cs_1", "attributes": {"checkout_url": "https://pay/cs_1"}}})

    result = _checkout(_client(handler))

    assert (result.id, result.checkout_url) == ("cs_1", "https://pay/cs_1")
    assert seen["url"] == "https://api.paymongo.com/v1/checkout_sessions"
    assert seen["auth"] == "Basic " + base64.b64encode(b"sk_test_unit_secret:").decode()
    attrs = seen["body"]["data"]["attributes"]
    assert attrs["line_items"] == [{"currency": "PHP", "amount": 165000, "name": "Pro", "quantity": 1}]
    assert attrs["payment_method_types"] == ["card"]
    assert attrs["metadata"] == {"tenant_id": "t"}


@pytest.mark.parametrize("status", [400, 401, 500])
def test_http_errors_become_provider_errors_without_leaking_secrets_or_bodies(status):
    def handler(request):
        return httpx.Response(
            status, json={"errors": [{"code": "parameter_invalid", "detail": "secret detail sk_test_unit_secret"}]}
        )

    with pytest.raises(BillingProviderError) as err:
        _checkout(_client(handler))

    assert f"HTTP {status}" in err.value.message
    assert "parameter_invalid" in err.value.message
    assert "sk_test_" not in err.value.message
    assert "secret detail" not in err.value.message


def test_network_failure_becomes_a_provider_error():
    def handler(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(BillingProviderError, match="could not be reached"):
        _checkout(_client(handler))


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="<html>"), httpx.Response(200, json={"data": {}})],
    ids=["not-json", "missing-fields"],
)
def test_malformed_success_response_is_a_provider_error(response):
    with pytest.raises(BillingProviderError):
        _checkout(_client(lambda request: response))
