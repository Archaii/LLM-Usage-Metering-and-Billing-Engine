import pytest

from app.api.routes.billing import get_billing_service
from app.core.db import transaction
from app.main import app
from app.services.billing import BillingService
from tests.payments import FakePayMongoClient


@pytest.fixture
def provider():
    fake = FakePayMongoClient()
    app.dependency_overrides[get_billing_service] = lambda: BillingService(fake)
    yield fake
    app.dependency_overrides.pop(get_billing_service, None)


def _sessions() -> list[dict]:
    with transaction() as conn:
        return conn.execute("SELECT * FROM checkout_sessions").fetchall()


def test_checkout_returns_url_and_stores_the_session(client, make_tenant, provider):
    tenant, key = make_tenant(plan="free")

    r = client.post("/billing/checkout", headers={"X-API-Key": key})

    assert r.status_code == 200
    body = r.json()
    assert body["checkout_url"] == "https://checkout.paymongo.test/cs_fake_1"
    assert body["session_id"] == "cs_fake_1"
    assert body["amount_php"] == "1650.00"
    assert body["period_days"] == 30

    [row] = _sessions()
    assert (row["id"], row["tenant_id"], row["status"], row["amount_centavos"]) == (
        "cs_fake_1", tenant.id, "pending", 165_000
    )
    [call] = provider.calls
    assert call["amount_centavos"] == 165_000
    assert call["currency"] == "PHP"
    assert call["metadata"] == {"tenant_id": str(tenant.id)}
    assert call["success_url"].endswith("/billing/success")
    assert call["cancel_url"].endswith("/billing/cancel")


def test_a_pro_tenant_can_start_a_renewal_checkout(client, make_tenant, provider):
    tenant, key = make_tenant(plan="pro")

    r = client.post("/billing/checkout", headers={"X-API-Key": key})

    assert r.status_code == 200
    assert r.json()["session_id"] == "cs_fake_1"
    [row] = _sessions()
    assert (row["tenant_id"], row["status"]) == (tenant.id, "pending")
    assert len(provider.calls) == 1


def test_provider_failure_is_502_with_no_stored_session_and_no_secret(client, make_tenant, provider):
    provider.fail = True
    _, key = make_tenant()

    r = client.post("/billing/checkout", headers={"X-API-Key": key})

    assert r.status_code == 502
    assert r.json()["error"] == "billing_provider_error"
    assert "sk_test_" not in r.text
    assert _sessions() == []


def test_checkout_requires_an_api_key(client, provider):
    assert client.post("/billing/checkout").status_code == 401


def test_success_and_cancel_pages_never_change_the_plan(client, make_tenant):
    tenant, _ = make_tenant(plan="free")

    ok = client.get("/billing/success")
    cancelled = client.get("/billing/cancel")

    assert ok.status_code == 200 and cancelled.status_code == 200
    assert "text/html" in ok.headers["content-type"]
    with transaction() as conn:
        plan = conn.execute("SELECT plan_code FROM tenants WHERE id = %s", (tenant.id,)).fetchone()["plan_code"]
    assert plan == "free"
