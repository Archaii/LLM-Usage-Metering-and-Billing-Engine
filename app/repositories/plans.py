"""SQL for the plans table."""
import psycopg

from app.core.models import Plan


def _plan(row: dict) -> Plan:
    return Plan(
        code=row["code"],
        name=row["name"],
        api_call_limit=row["api_call_limit"],
        token_limit=row["token_limit"],
        base_fee_micros=row["base_fee_micros"],
    )


def get(conn: psycopg.Connection, code: str) -> Plan | None:
    row = conn.execute("SELECT * FROM plans WHERE code = %s", (code,)).fetchone()
    return _plan(row) if row else None


def list_all(conn: psycopg.Connection) -> list[Plan]:
    rows = conn.execute("SELECT * FROM plans ORDER BY code").fetchall()
    return [_plan(r) for r in rows]


def upsert(conn: psycopg.Connection, plan: Plan) -> None:
    conn.execute(
        """
        INSERT INTO plans (code, name, api_call_limit, token_limit, base_fee_micros)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name,
            api_call_limit = EXCLUDED.api_call_limit,
            token_limit = EXCLUDED.token_limit,
            base_fee_micros = EXCLUDED.base_fee_micros
        """,
        (plan.code, plan.name, plan.api_call_limit, plan.token_limit, plan.base_fee_micros),
    )
