"""Seed demo plans and tenants. Prints each tenant's API key once.

Re-running rotates the keys of existing demo tenants, because only hashes are stored.
"""
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import get_pricing
from app.core.db import close_pool, init_pool, transaction
from app.core.models import Tenant
from app.core.security import generate_api_key, hash_api_key
from app.repositories import tenants, usage
from app.services.plan_sync import sync_plans

BOUNDARY_NAME = "Boundary (Free, 999 calls used)"
PREFILL_KEY = "seed-prefill-999-calls"
DEMO_TENANTS = [
    ("Acme (Free)", "free"),
    ("Globex (Pro)", "pro"),
    (BOUNDARY_NAME, "free"),
]


def _upsert_tenant(conn, name: str, plan_code: str) -> tuple[Tenant, str]:
    key = generate_api_key()
    existing = tenants.get_by_name(conn, name)
    if existing is None:
        return tenants.create(conn, name, hash_api_key(key), plan_code), key
    tenants.rotate_key(conn, existing.id, hash_api_key(key))
    return existing, key


def _prefill_boundary(conn, tenant: Tenant) -> None:
    """One event worth 999 API calls, so the next call is the boundary call (Probe 2)."""
    if usage.find_by_key(conn, tenant.id, PREFILL_KEY) is not None:
        return
    usage.insert_event(
        conn,
        tenant_id=tenant.id,
        event_id=uuid4(),
        idempotency_key=PREFILL_KEY,
        request_hash="seed",
        api_calls=999,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_micros=0,
        response_status=201,
        response_body={"seed": True},
        created_at=datetime.now(timezone.utc),
    )


def main() -> None:
    init_pool()
    try:
        sync_plans(get_pricing())
        keys: dict[str, str] = {}
        with transaction() as conn:
            for name, plan_code in DEMO_TENANTS:
                tenant, key = _upsert_tenant(conn, name, plan_code)
                keys[name] = key
                if name == BOUNDARY_NAME:
                    _prefill_boundary(conn, tenant)
        print("Demo tenants (API keys are shown once):")
        for name, key in keys.items():
            print(f"  {name}: {key}")
        print("Send the key in the X-API-Key header.")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
