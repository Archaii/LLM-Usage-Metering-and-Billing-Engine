from datetime import datetime, timedelta, timezone

from app.core.db import transaction
from app.repositories import payment_events
from app.services.webhook import Outcome, WebhookService
from app.worker import expire_periods, process_pending
from tests.payments import build_paid_event, deliver

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


class AlwaysFails(WebhookService):
    def process(self, conn, event, now):
        raise RuntimeError("boom")


class FailsOnlyFor(WebhookService):
    def __init__(self, bad_id: str):
        self.bad_id = bad_id

    def process(self, conn, event, now):
        if event.event_id == self.bad_id:
            raise RuntimeError("boom")
        return Outcome("processed")


def _store_pending(event_id: str, created: int = 1) -> None:
    with transaction() as conn:
        payment_events.insert_if_new(
            conn, event_id=event_id, event_type="checkout_session.payment.paid",
            event_created=created, payload={}, status="pending",
        )


def _event(event_id: str):
    with transaction() as conn:
        return payment_events.get(conn, event_id)


def _alerts() -> list[dict]:
    with transaction() as conn:
        return conn.execute("SELECT * FROM alerts").fetchall()


def test_failing_handler_retries_with_backoff_then_fails_with_an_alert(client):
    _store_pending("evt_bad")
    service = AlwaysFails()

    expected_delays = [2, 4, 8, 16]
    now = T0
    for attempt, delay in enumerate(expected_delays, start=1):
        assert process_pending(service, now=now) == 1
        ev = _event("evt_bad")
        assert (ev.status, ev.attempts) == ("pending", attempt)
        assert ev.next_attempt_at == now + timedelta(seconds=delay)
        assert "boom" in ev.last_error
        # Not due yet: the worker must leave it alone.
        assert process_pending(service, now=now + timedelta(seconds=delay - 1)) == 0
        now = now + timedelta(seconds=delay)

    assert _alerts() == []
    assert process_pending(service, now=now) == 1  # fifth failure

    ev = _event("evt_bad")
    assert (ev.status, ev.attempts) == ("failed", 5)
    [alert] = _alerts()
    assert alert["source"] == "payment_worker"
    assert alert["ref"] == "evt_bad"
    assert process_pending(service, now=now + timedelta(days=1)) == 0  # failed events are not retried


def test_one_failing_event_does_not_block_the_others_in_the_batch(client):
    _store_pending("evt_bad", created=1)
    _store_pending("evt_good", created=2)

    process_pending(FailsOnlyFor("evt_bad"), now=T0)

    assert _event("evt_bad").status == "pending"
    assert _event("evt_bad").attempts == 1
    assert _event("evt_good").status == "processed"


def test_expiry_returns_a_lapsed_tenant_to_free(client, make_tenant):
    tenant, _ = make_tenant(plan="free")
    paid_at = int(T0.timestamp())
    deliver(client, build_paid_event(tenant_id=str(tenant.id), event_id="evt_x", created_at=paid_at))
    process_pending(now=T0)

    def plan():
        with transaction() as conn:
            return conn.execute("SELECT plan_code FROM tenants WHERE id = %s", (tenant.id,)).fetchone()["plan_code"]

    assert plan() == "pro"
    assert expire_periods(now=T0 + timedelta(days=29)) == 0
    assert plan() == "pro"

    assert expire_periods(now=T0 + timedelta(days=30, seconds=1)) == 1
    assert plan() == "free"
    with transaction() as conn:
        assert conn.execute("SELECT status FROM subscriptions").fetchone()["status"] == "expired"


def test_tenant_with_a_second_active_period_stays_pro(client, make_tenant):
    tenant, _ = make_tenant(plan="free")
    first = int(T0.timestamp())
    second = int((T0 + timedelta(days=20)).timestamp())
    deliver(client, build_paid_event(tenant_id=str(tenant.id), event_id="evt_1", created_at=first))
    deliver(client, build_paid_event(tenant_id=str(tenant.id), event_id="evt_2", created_at=second))
    process_pending(now=T0)

    # Day 31: the first period lapsed, the second (paid day 20, ends day 50) is still active.
    assert expire_periods(now=T0 + timedelta(days=31)) == 0
    with transaction() as conn:
        plan = conn.execute("SELECT plan_code FROM tenants WHERE id = %s", (tenant.id,)).fetchone()["plan_code"]
    assert plan == "pro"


def test_seeded_pro_tenant_without_a_subscription_is_never_downgraded(client, make_tenant):
    tenant, _ = make_tenant(plan="pro")
    assert expire_periods(now=T0 + timedelta(days=365)) == 0
    with transaction() as conn:
        plan = conn.execute("SELECT plan_code FROM tenants WHERE id = %s", (tenant.id,)).fetchone()["plan_code"]
    assert plan == "pro"
