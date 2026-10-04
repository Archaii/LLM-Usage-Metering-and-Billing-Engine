"""SQL for subscriptions: one row per paid Pro period."""
from datetime import datetime
from uuid import UUID

import psycopg


def insert_if_new(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    provider_payment_id: str,
    checkout_session_id: str | None,
    plan_code: str,
    period_start: datetime,
    period_end: datetime,
) -> bool:
    """True if a period was granted; False if this payment already granted one."""
    row = conn.execute(
        """
        INSERT INTO subscriptions (
            tenant_id, provider_payment_id, checkout_session_id, plan_code,
            status, current_period_start, current_period_end
        ) VALUES (%s, %s, %s, %s, 'active', %s, %s)
        ON CONFLICT (provider_payment_id) DO NOTHING
        RETURNING id
        """,
        (tenant_id, provider_payment_id, checkout_session_id, plan_code, period_start, period_end),
    ).fetchone()
    return row is not None


def has_active(conn: psycopg.Connection, tenant_id: UUID, now: datetime | None) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM subscriptions
        WHERE tenant_id = %s AND status = 'active' AND current_period_end > coalesce(%s::timestamptz, now())
        LIMIT 1
        """,
        (tenant_id, now),
    ).fetchone()
    return row is not None


def latest_active_end(conn: psycopg.Connection, tenant_id: UUID, after: datetime) -> datetime | None:
    """The end of the latest active period that is still running at `after`, or None.

    A renewal starts here, so paid periods stack back to back.
    """
    row = conn.execute(
        """
        SELECT max(current_period_end) AS latest_end
        FROM subscriptions
        WHERE tenant_id = %s AND status = 'active' AND current_period_end > %s
        """,
        (tenant_id, after),
    ).fetchone()
    return row["latest_end"]


def expire_due(conn: psycopg.Connection, now: datetime | None) -> list[UUID]:
    """Mark lapsed periods expired. Returns the distinct tenants that had one lapse.

    now=None uses the database clock.
    """
    rows = conn.execute(
        """
        UPDATE subscriptions
        SET status = 'expired', updated_at = now()
        WHERE status = 'active' AND current_period_end <= coalesce(%s::timestamptz, now())
        RETURNING tenant_id
        """,
        (now,),
    ).fetchall()
    return list({row["tenant_id"] for row in rows})
