import time
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import transaction
from app.repositories import payment_events
from app.worker import process_pending
from tests.payments import build_paid_event, deliver, serialise


def _stored(event_id):
    with transaction() as conn:
        return payment_events.get(conn, event_id)


def _count(table: str) -> int:
    with transaction() as conn:
        return int(conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"])


def _plan_of(tenant_id) -> str:
    with transaction() as conn:
        return conn.execute("SELECT plan_code FROM tenants WHERE id = %s", (tenant_id,)).fetchone()["plan_code"]


# --- signature checks (Probe 4) ----------------------------------------------------------


def test_forged_signature_is_400_and_writes_nothing(client, make_tenant):
    tenant, _ = make_tenant()
    event = build_paid_event(tenant_id=str(tenant.id))

    r = deliver(client, event, secret="not-the-real-secret")

    assert r.status_code == 400
    assert r.json()["error"] == "invalid_signature"
    assert _count("payment_events") == 0
    assert _plan_of(tenant.id) == "free"


@pytest.mark.parametrize(
    "header",
    [None, "garbage", "t=abc,te=deadbeef,li=", "t=1700000000", "te=deadbeef"],
    ids=["missing", "garbage", "bad-timestamp", "no-te", "no-t"],
)
def test_missing_or_malformed_signature_header_is_400(client, make_tenant, header):
    tenant, _ = make_tenant()
    r = deliver(client, build_paid_event(tenant_id=str(tenant.id)), header=header)

    assert r.status_code == 400
    assert r.json()["error"] == "invalid_signature"
    assert _count("payment_events") == 0


def test_live_mode_signature_only_is_rejected(client, make_tenant):
    tenant, _ = make_tenant()
    r = deliver(client, build_paid_event(tenant_id=str(tenant.id)), header=f"t={int(time.time())},te=,li=abc123")
    assert r.status_code == 400
    assert _count("payment_events") == 0


def test_stale_timestamp_is_rejected_even_when_correctly_signed(client, make_tenant):
    tenant, _ = make_tenant()
    r = deliver(client, build_paid_event(tenant_id=str(tenant.id)), timestamp=int(time.time()) - 3_600)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_signature"
    assert _count("payment_events") == 0


def test_body_changed_after_signing_is_rejected(client, make_tenant):
    tenant, _ = make_tenant()
    signed = serialise(build_paid_event(tenant_id=str(tenant.id), event_id="evt_a"))
    tampered = serialise(build_paid_event(tenant_id=str(tenant.id), event_id="evt_b"))
    from app.core.config import get_settings
    from app.integrations.paymongo import SIGNATURE_HEADER, build_signature_header

    header = build_signature_header(get_settings().paymongo_webhook_secret, signed)
    r = client.post("/webhooks/paymongo", content=tampered, headers={SIGNATURE_HEADER: header})

    assert r.status_code == 400
    assert _count("payment_events") == 0


@pytest.mark.parametrize(
    "body",
    [b"not json", b"{}", b'{"data": {"id": "evt_x"}}', b'{"data": {"id": "evt_x", "attributes": {"type": "t"}}}'],
    ids=["not-json", "empty-object", "no-attributes", "no-created-at"],
)
def test_validly_signed_but_unparseable_event_is_400_invalid_payload(client, body):
    r = deliver(client, {}, body=body)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"
    assert _count("payment_events") == 0


def test_live_mode_event_is_rejected(client, make_tenant):
    tenant, _ = make_tenant()
    event = build_paid_event(tenant_id=str(tenant.id))
    event["data"]["attributes"]["livemode"] = True
    r = deliver(client, event)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"
    assert _count("payment_events") == 0


# --- dedupe and application (Probes 3 and 4) ---------------------------------------------


def test_same_signed_event_twice_is_one_row_applied_once(client, make_tenant):
    tenant, _ = make_tenant()
    event = build_paid_event(tenant_id=str(tenant.id), event_id="evt_dup")

    first = deliver(client, event)
    second = deliver(client, event)

    assert first.status_code == 200 and first.json() == {"received": True}
    assert second.status_code == 200 and second.json() == {"received": True, "duplicate": True}
    assert _count("payment_events") == 1

    process_pending()
    assert _stored("evt_dup").status == "processed"
    assert _count("subscriptions") == 1
    assert _plan_of(tenant.id) == "pro"


def test_paid_event_flips_tenant_to_pro_and_usage_shows_pro_limits(client, make_tenant):
    tenant, key = make_tenant(plan="free")
    before = client.get("/usage", headers={"X-API-Key": key}).json()
    assert (before["plan"], before["api_calls"]["limit"], before["tokens"]["limit"]) == ("free", 1_000, 100_000)

    paid_at = int(time.time())
    deliver(client, build_paid_event(tenant_id=str(tenant.id), event_id="evt_pro", created_at=paid_at))
    process_pending()

    after = client.get("/usage", headers={"X-API-Key": key}).json()
    assert (after["plan"], after["api_calls"]["limit"], after["tokens"]["limit"]) == ("pro", 50_000, 5_000_000)
    assert after["billing_status"] == "ok"
    with transaction() as conn:
        sub = conn.execute("SELECT * FROM subscriptions WHERE tenant_id = %s", (tenant.id,)).fetchone()
    assert sub["status"] == "active"
    assert sub["current_period_end"] - sub["current_period_start"] == timedelta(days=30)
    assert int(sub["current_period_start"].timestamp()) == paid_at


def test_two_different_events_for_one_payment_grant_once(client, make_tenant):
    tenant, _ = make_tenant()
    a = build_paid_event(tenant_id=str(tenant.id), event_id="evt_1", payment_id="pay_same")
    b = build_paid_event(tenant_id=str(tenant.id), event_id="evt_2", payment_id="pay_same")
    deliver(client, a)
    deliver(client, b)

    process_pending()

    assert _count("subscriptions") == 1
    statuses = {_stored("evt_1").status, _stored("evt_2").status}
    assert statuses == {"processed", "skipped"}
    skipped = next(e for e in ("evt_1", "evt_2") if _stored(e).status == "skipped")
    assert _stored(skipped).last_error == "duplicate_payment"


def test_amount_mismatch_is_skipped_and_tenant_unchanged(client, make_tenant):
    tenant, _ = make_tenant()
    deliver(client, build_paid_event(tenant_id=str(tenant.id), event_id="evt_cheap", amount_centavos=2_000))

    process_pending()

    assert _stored("evt_cheap").status == "skipped"
    assert _stored("evt_cheap").last_error == "amount_mismatch"
    assert _plan_of(tenant.id) == "free"
    assert _count("subscriptions") == 0


def test_unknown_tenant_is_skipped(client):
    deliver(client, build_paid_event(tenant_id="00000000-0000-0000-0000-000000000000", event_id="evt_ghost"))
    process_pending()
    assert _stored("evt_ghost").status == "skipped"
    assert _stored("evt_ghost").last_error == "tenant_not_found"


def test_event_without_a_paid_payment_is_skipped(client, make_tenant):
    tenant, _ = make_tenant()
    event = build_paid_event(tenant_id=str(tenant.id), event_id="evt_unpaid")
    event["data"]["attributes"]["data"]["attributes"]["payments"][0]["attributes"]["status"] = "failed"
    deliver(client, event)

    process_pending()

    assert _stored("evt_unpaid").last_error == "no_paid_payment"
    assert _plan_of(tenant.id) == "free"


def test_unhandled_event_type_is_stored_skipped_on_receipt(client, make_tenant):
    tenant, _ = make_tenant()
    event = build_paid_event(tenant_id=str(tenant.id), event_id="evt_other")
    event["data"]["attributes"]["type"] = "payment.failed"

    r = deliver(client, event)

    assert r.status_code == 200
    stored = _stored("evt_other")
    assert (stored.status, stored.last_error) == ("skipped", "unhandled_type")
    assert process_pending() == 0  # nothing pending for the worker


def test_stored_checkout_session_wins_over_spoofed_metadata(client, make_tenant):
    from app.repositories import checkout_sessions

    owner, _ = make_tenant("Owner")
    other, _ = make_tenant("Other")
    with transaction() as conn:
        checkout_sessions.insert(conn, session_id="cs_owned", tenant_id=owner.id, plan_code="pro", amount_centavos=165_000)

    # Metadata names a different tenant; our own session record decides.
    deliver(client, build_paid_event(tenant_id=str(other.id), session_id="cs_owned", event_id="evt_spoof"))
    process_pending()

    assert _plan_of(owner.id) == "pro"
    assert _plan_of(other.id) == "free"
    with transaction() as conn:
        row = conn.execute("SELECT status FROM checkout_sessions WHERE id = 'cs_owned'").fetchone()
    assert row["status"] == "paid"


def test_paid_event_clears_nothing_for_other_tenants(client, make_tenant):
    a, _ = make_tenant("A")
    b, _ = make_tenant("B")
    deliver(client, build_paid_event(tenant_id=str(a.id), event_id="evt_a"))
    process_pending()
    assert _plan_of(a.id) == "pro"
    assert _plan_of(b.id) == "free"
