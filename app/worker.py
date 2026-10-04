"""Background worker: applies queued payment events and expires lapsed Pro periods.

Run with `python -m app.worker`. Safe to run more than one copy (FOR UPDATE SKIP LOCKED).
"""
import logging
import signal
import threading
from datetime import datetime

from app.core.config import get_settings
from app.core.db import close_pool, init_pool, transaction
from app.repositories import alerts, payment_events, subscriptions, tenants
from app.services.webhook import WebhookService

log = logging.getLogger("app.worker")

MAX_ATTEMPTS = 5
BATCH_SIZE = 10
POLL_SECONDS = 1.0
ALERT_SOURCE = "payment_worker"


def _safe_error(exc: Exception) -> str:
    """Exception type and message only, truncated. Never the event payload."""
    return f"{type(exc).__name__}: {exc}"[:500]


def process_pending(
    service: WebhookService | None = None, now: datetime | None = None, limit: int = BATCH_SIZE
) -> int:
    """Claim and apply due events. Returns how many were handled. One failure never blocks the rest.

    now=None (production) uses the database clock; tests pass an explicit time.
    """
    service = service or WebhookService()
    handled = 0
    with transaction() as conn:
        for event in payment_events.claim_batch(conn, now, limit):
            handled += 1
            try:
                with conn.transaction():  # savepoint: a failing event rolls back only itself
                    outcome = service.process(conn, event, now)
                    payment_events.mark_done(conn, event.event_id, outcome.status, outcome.reason, now)
            except Exception as exc:
                attempts = event.attempts + 1
                error = _safe_error(exc)
                if attempts >= MAX_ATTEMPTS:
                    payment_events.mark_failed(conn, event.event_id, attempts, error, now)
                    alerts.insert(
                        conn,
                        source=ALERT_SOURCE,
                        ref=event.event_id,
                        message=f"Event {event.event_id} ({event.type}) failed {attempts} times: {error}",
                    )
                    log.error("payment event %s failed permanently after %d attempts: %s",
                              event.event_id, attempts, error)
                else:
                    delay = 2 ** attempts  # 2, 4, 8, 16 s between the five attempts
                    payment_events.mark_retry(conn, event.event_id, attempts, delay, error, now)
                    log.warning("payment event %s failed (attempt %d), retry in %ds: %s",
                                event.event_id, attempts, delay, error)
    return handled


def expire_periods(now: datetime | None = None) -> int:
    """Expire lapsed Pro periods; tenants with no active period go back to Free (now=None: DB clock)."""
    downgraded = 0
    with transaction() as conn:
        for tenant_id in subscriptions.expire_due(conn, now):
            tenants.lock_for_update(conn, tenant_id)
            if not subscriptions.has_active(conn, tenant_id, now):
                tenants.set_plan(conn, tenant_id, "free")
                downgraded += 1
                log.info("pro period expired; tenant %s back to free", tenant_id)
    return downgraded


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_pool(max_size=3)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker started")
    try:
        while not stop.is_set():
            try:
                process_pending()
                expire_periods()
            except Exception:
                log.exception("worker loop error")
            stop.wait(POLL_SECONDS)
    finally:
        close_pool()
        log.info("worker stopped")


if __name__ == "__main__":
    main()
