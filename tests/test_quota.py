from tests.helpers import body, headers, small_body


def test_999_plus_1_is_allowed_then_1000_plus_1_is_rejected_free(
    client, make_tenant, prefill, count_events
):
    tenant, key = make_tenant(plan="free")
    prefill(tenant.id, api_calls=999)

    boundary = client.post("/generate", json=small_body(), headers=headers(key, "k-1000"))
    assert boundary.status_code == 201
    assert boundary.json()["quota"]["api_calls_used"] == 1_000

    over = client.post("/generate", json=small_body(), headers=headers(key, "k-1001"))
    assert over.status_code == 402
    err = over.json()
    assert err["error"] == "upgrade_required"
    assert err["upgrade_url"] == "/billing/checkout"
    assert err["details"]["meter"] == "api_calls"
    assert (err["details"]["used"], err["details"]["requested"], err["details"]["limit"]) == (1_000, 1, 1_000)
    assert "1,000 of 1,000 API calls" in err["message"]
    assert "Upgrade to Pro" in err["message"]
    assert count_events(tenant.id, "k-1001") == 0  # rejections are not stored


def test_pro_over_quota_is_429_with_retry_after(client, make_tenant, prefill):
    tenant, key = make_tenant(plan="pro")
    prefill(tenant.id, api_calls=50_000)

    r = client.post("/generate", json=small_body(), headers=headers(key, "k-1"))

    assert r.status_code == 429
    assert r.json()["error"] == "quota_exceeded"
    assert r.json()["details"]["meter"] == "api_calls"
    assert "upgrade_url" not in r.json()
    assert int(r.headers["Retry-After"]) > 0


def test_token_request_crossing_the_limit_is_rejected_whole(
    client, make_tenant, prefill, count_events
):
    tenant, key = make_tenant(plan="free")
    prefill(tenant.id, tokens=99_000)  # 1,000 tokens of room left

    r = client.post(
        "/generate",
        json=body(input_tokens=1_000, cached=0, output=500, reasoning=0),  # needs 1,500
        headers=headers(key, "k-big"),
    )

    assert r.status_code == 402
    err = r.json()
    assert err["details"] == {
        "meter": "tokens", "used": 99_000, "requested": 1_500, "limit": 100_000,
        "period_end": err["details"]["period_end"],
    }
    assert "99,000 of 100,000 tokens" in err["message"]
    assert count_events(tenant.id) == 1  # only the prefill row; nothing partial


def test_token_request_exactly_filling_the_limit_is_allowed(client, make_tenant, prefill):
    tenant, key = make_tenant(plan="free")
    prefill(tenant.id, tokens=99_000)

    r = client.post(
        "/generate",
        json=body(input_tokens=1_000, cached=0, output=0, reasoning=0),
        headers=headers(key, "k-exact"),
    )

    assert r.status_code == 201
    assert r.json()["quota"]["tokens_used"] == 100_000


def test_past_due_tenant_gets_402_payment_required(client, make_tenant):
    from app.core.db import transaction

    tenant, key = make_tenant(plan="pro")
    with transaction() as conn:
        conn.execute("UPDATE tenants SET billing_status = 'past_due' WHERE id = %s", (tenant.id,))

    r = client.post("/generate", json=small_body(), headers=headers(key, "k-1"))

    assert r.status_code == 402
    assert r.json()["error"] == "payment_required"
