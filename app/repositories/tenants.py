"""SQL for the tenants table. Lookups by API-key hash and by id only; no cross-tenant listing."""
from uuid import UUID

import psycopg

from app.core.models import Tenant

_COLUMNS = "id, name, plan_code, billing_status"


def _tenant(row: dict) -> Tenant:
    return Tenant(
        id=row["id"],
        name=row["name"],
        plan_code=row["plan_code"],
        billing_status=row["billing_status"],
    )


def get_by_key_hash(conn: psycopg.Connection, api_key_hash: str) -> Tenant | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM tenants WHERE api_key_hash = %s", (api_key_hash,)
    ).fetchone()
    return _tenant(row) if row else None


def get_by_id(conn: psycopg.Connection, tenant_id: UUID) -> Tenant | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM tenants WHERE id = %s", (tenant_id,)
    ).fetchone()
    return _tenant(row) if row else None


def lock_for_update(conn: psycopg.Connection, tenant_id: UUID) -> Tenant | None:
    """Serialises all metering and plan changes for one tenant until the transaction ends."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM tenants WHERE id = %s FOR UPDATE", (tenant_id,)
    ).fetchone()
    return _tenant(row) if row else None


def get_by_name(conn: psycopg.Connection, name: str) -> Tenant | None:
    """Used by the seed script only."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM tenants WHERE name = %s", (name,)
    ).fetchone()
    return _tenant(row) if row else None


def create(conn: psycopg.Connection, name: str, api_key_hash: str, plan_code: str) -> Tenant:
    row = conn.execute(
        f"""
        INSERT INTO tenants (name, api_key_hash, plan_code)
        VALUES (%s, %s, %s)
        RETURNING {_COLUMNS}
        """,
        (name, api_key_hash, plan_code),
    ).fetchone()
    return _tenant(row)


def rotate_key(conn: psycopg.Connection, tenant_id: UUID, api_key_hash: str) -> None:
    conn.execute(
        "UPDATE tenants SET api_key_hash = %s, updated_at = now() WHERE id = %s",
        (api_key_hash, tenant_id),
    )


def set_plan(
    conn: psycopg.Connection, tenant_id: UUID, plan_code: str, billing_status: str | None = None
) -> None:
    """Change a tenant's plan. billing_status is left alone unless given."""
    conn.execute(
        """
        UPDATE tenants
        SET plan_code = %s,
            billing_status = coalesce(%s, billing_status),
            updated_at = now()
        WHERE id = %s
        """,
        (plan_code, billing_status, tenant_id),
    )
