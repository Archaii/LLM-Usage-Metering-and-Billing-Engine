import pytest

from tests.helpers import body, headers, small_body


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "x", "tokens": {"input_tokens": -1, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}},
        {"prompt": "x", "tokens": {"input_tokens": 10, "cached_input_tokens": 11, "output_tokens": 0, "reasoning_tokens": 0}},
        {"prompt": "", "tokens": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}},
        {"tokens": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}},
        {"prompt": "x", "tokens": {"input_tokens": 1_000_001, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}},
        {"prompt": "x"},
    ],
    ids=["negative", "cached>input", "empty-prompt", "missing-prompt", "too-big", "missing-tokens"],
)
def test_bad_input_is_422_never_500(client, make_tenant, count_events, payload):
    tenant, key = make_tenant()
    r = client.post("/generate", json=payload, headers=headers(key))

    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"
    assert r.json()["details"]["errors"]
    assert count_events(tenant.id) == 0


def test_malformed_json_is_422(client, make_tenant):
    _, key = make_tenant()
    r = client.post("/generate", content="{not json", headers={**headers(key), "Content-Type": "application/json"})
    assert r.status_code == 422


@pytest.mark.parametrize("api_key", [None, "mk_test_unknown"])
def test_missing_or_unknown_api_key_is_401(client, api_key):
    h = {"Idempotency-Key": "k"}
    if api_key:
        h["X-API-Key"] = api_key
    r = client.post("/generate", json=small_body(), headers=h)

    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_tenant_b_cannot_see_tenant_a_usage(client, make_tenant):
    _, key_a = make_tenant("A")
    _, key_b = make_tenant("B")
    client.post("/generate", json=body(), headers=headers(key_a, "k-a"))

    usage_a = client.get("/usage", headers={"X-API-Key": key_a}).json()
    usage_b = client.get("/usage", headers={"X-API-Key": key_b}).json()

    assert usage_a["api_calls"]["used"] == 1
    assert usage_a["tokens"]["used"] == 13_500
    assert usage_b["api_calls"]["used"] == 0
    assert usage_b["tokens"]["used"] == 0


def test_usage_reports_counts_limits_and_breakdown(client, make_tenant):
    _, key = make_tenant(plan="free")
    client.post("/generate", json=body(), headers=headers(key, "k-1"))

    u = client.get("/usage", headers={"X-API-Key": key}).json()

    assert u["plan"] == "free"
    assert u["billing_status"] == "ok"
    assert u["api_calls"] == {"used": 1, "limit": 1_000, "remaining": 999, "cost_micros": 0}
    assert u["tokens"]["limit"] == 100_000
    assert u["tokens"]["remaining"] == 86_500
    assert u["tokens"]["breakdown"] == {
        "input": 10_000, "cached_input": 4_000, "fresh_input": 6_000,
        "output": 2_000, "reasoning": 1_500,
    }
    assert u["period_start"].endswith("T00:00:00Z") and u["period_start"][8:10] == "01"


def test_usage_requires_api_key(client):
    assert client.get("/usage").status_code == 401


def test_plans_endpoint_is_public_and_matches_config(client):
    r = client.get("/plans")
    assert r.status_code == 200
    plans = {p["code"]: p for p in r.json()["plans"]}
    assert plans["free"]["api_call_limit"] == 1_000
    assert plans["pro"]["token_limit"] == 5_000_000
    assert r.json()["rates"]["reasoning_micros_per_million"] == r.json()["rates"]["output_micros_per_million"]


def test_health_checks_the_database(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}
