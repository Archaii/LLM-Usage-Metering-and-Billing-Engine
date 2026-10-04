"""SQL for payment_events: the webhook inbox and the worker's queue."""
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from app.core.models import PaymentEvent

_COLUMNS = "event_id, type, event_created, payload, status, attempts, next_attempt_at, last_error"


def _event(row: dict) -> PaymentEvent:
    return PaymentEvent(**{k: row[k] for k in PaymentEvent.__dataclass_fields__})


def insert_if_new(
    conn: psycopg.Connection,
    *,
    event_id: str,
    event_type: str,
    event_created: int,
    payload: dict,
    status: str,
    last_error: str | None = None,
) -> bool:
    """True if the event is new; False if this event_id was already stored (a replay)."""
    row = conn.execute(
        """
        INSERT INTO payment_events (event_id, type, event_created, payload, status, last_error)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        (event_id, event_type, event_created, Jsonb(payload), status, last_error),
    ).fetchone()
    return row is not None


def get(conn: psycopg.Connection, event_id: str) -> PaymentEvent | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM payment_events WHERE event_id = %s", (event_id,)
    ).fetchone()
    return _event(row) if row else None


def claim_batch(conn: psycopg.Connection, now: datetime | None, limit: int = 10) -> list[PaymentEvent]:
    """Lock due pending events. SKIP LOCKED lets several workers run side by side.

    now=None uses the database clock, so the queue never depends on the worker host clock.
    """
    rows = conn.execute(
        f"""
        SELECT {_COLUMNS} FROM payment_events
        WHERE status = 'pending' AND next_attempt_at <= coalesce(%s::timestamptz, now())
        ORDER BY event_created, received_at
        LIMIT %s
        FOR UPDATE SKIP LOCKED
        """,
        (now, limit),
    ).fetchall()
    return [_event(r) for r in rows]


def mark_done(
    conn: psycopg.Connection, event_id: str, status: str, reason: str | None, now: datetime | None
) -> None:
    """status is 'processed' or 'skipped'; reason explains a skip."""
    conn.execute(
        """
        UPDATE payment_events
        SET status = %s, last_error = %s, processed_at = coalesce(%s::timestamptz, now())
        WHERE event_id = %s
        """,
        (status, reason, now, event_id),
    )


def mark_retry(
    conn: psycopg.Connection, event_id: str, attempts: int, delay_seconds: int,
    error: str, now: datetime | None,
) -> None:
    conn.execute(
        """
        UPDATE payment_events
        SET attempts = %s,
            next_attempt_at = coalesce(%s::timestamptz, now()) + make_interval(secs => %s),
            last_error = %s
        WHERE event_id = %s
        """,
        (attempts, now, delay_seconds, error, event_id),
    )


def mark_failed(
    conn: psycopg.Connection, event_id: str, attempts: int, error: str, now: datetime | None
) -> None:
    conn.execute(
        """
        UPDATE payment_events
        SET status = 'failed', attempts = %s, last_error = %s, processed_at = coalesce(%s::timestamptz, now())
        WHERE event_id = %s
        """,
        (attempts, error, now, event_id),
    )
