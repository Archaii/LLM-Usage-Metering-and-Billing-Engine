"""SQL for checkout_sessions.

get() looks a session up by its provider ID alone because the webhook has no tenant
yet: finding the tenant is the point of the lookup.
"""
from datetime import datetime
from uuid import UUID

import psycopg

from app.core.models import CheckoutSession


def insert(
    conn: psycopg.Connection, *, session_id: str, tenant_id: UUID, plan_code: str, amount_centavos: int
) -> None:
    conn.execute(
        """
        INSERT INTO checkout_sessions (id, tenant_id, plan_code, amount_centavos)
        VALUES (%s, %s, %s, %s)
        """,
        (session_id, tenant_id, plan_code, amount_centavos),
    )


def get(conn: psycopg.Connection, session_id: str) -> CheckoutSession | None:
    row = conn.execute(
        "SELECT id, tenant_id, plan_code, amount_centavos, status FROM checkout_sessions WHERE id = %s",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return CheckoutSession(
        id=row["id"],
        tenant_id=row["tenant_id"],
        plan_code=row["plan_code"],
        amount_centavos=row["amount_centavos"],
        status=row["status"],
    )


def mark_paid(conn: psycopg.Connection, session_id: str, paid_at: datetime) -> None:
    conn.execute(
        "UPDATE checkout_sessions SET status = 'paid', paid_at = %s WHERE id = %s",
        (paid_at, session_id),
    )
