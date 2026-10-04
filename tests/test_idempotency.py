from concurrent.futures import ThreadPoolExecutor

from tests.helpers import body, headers, small_body


def test_same_key_same_body_twice_creates_one_row(client, make_tenant, count_events):
    tenant, key = make_tenant()
    first = client.post("/generate", json=body(), headers=headers(key, "k-1"))
    second = client.post("/generate", json=body(), headers=headers(key, "k-1"))

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert first.json() == second.json()
    assert count_events(tenant.id, "k-1") == 1


def test_replay_does_not_consume_quota_again(client, make_tenant):
    tenant, key = make_tenant()
    client.post("/generate", json=body(), headers=headers(key, "k-1"))
    client.post("/generate", json=body(), headers=headers(key, "k-1"))

    usage = client.get("/usage", headers={"X-API-Key": key}).json()
    assert usage["api_calls"]["used"] == 1
    assert usage["tokens"]["used"] == 13_500


def test_same_key_different_body_is_rejected(client, make_tenant, count_events):
    tenant, key = make_tenant()
    client.post("/generate", json=body("first"), headers=headers(key, "k-1"))
    reuse = client.post("/generate", json=body("second"), headers=headers(key, "k-1"))

    assert reuse.status_code == 422
    assert reuse.json()["error"] == "idempotency_key_reused"
    assert count_events(tenant.id) == 1


def test_missing_idempotency_key_is_400(client, make_tenant, count_events):
    tenant, key = make_tenant()
    r = client.post("/generate", json=body(), headers=headers(key, None))

    assert r.status_code == 400
    assert r.json()["error"] == "idempotency_key_missing"
    assert count_events(tenant.id) == 0


def test_idempotency_key_over_255_chars_is_400(client, make_tenant):
    _, key = make_tenant()
    r = client.post("/generate", json=body(), headers=headers(key, "x" * 256))
    assert r.status_code == 400
    assert r.json()["error"] == "idempotency_key_missing"


def test_20_concurrent_requests_with_one_key_create_one_row(client, make_tenant, count_events):
    tenant, key = make_tenant()

    def send(_):
        return client.post("/generate", json=body(), headers=headers(key, "race-key"))

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(send, range(20)))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] * 19 + [201]
    assert all(r.json() == responses[0].json() for r in responses)  # identical bodies (JSONB reorders keys, so compare parsed)
    assert count_events(tenant.id, "race-key") == 1


def test_same_key_from_two_tenants_makes_two_rows(client, make_tenant, count_events):
    a, key_a = make_tenant("A")
    b, key_b = make_tenant("B")

    ra = client.post("/generate", json=small_body("from A"), headers=headers(key_a, "shared"))
    rb = client.post("/generate", json=small_body("from B"), headers=headers(key_b, "shared"))

    assert ra.status_code == 201
    assert rb.status_code == 201  # B is not served A's stored response
    assert ra.json()["tenant_id"] == str(a.id)
    assert rb.json()["tenant_id"] == str(b.id)
    assert "from A" in ra.json()["output"] and "from B" in rb.json()["output"]
    assert count_events(a.id, "shared") == 1
    assert count_events(b.id, "shared") == 1
