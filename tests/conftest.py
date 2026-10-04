"""Test harness: a separate billing_test database, migrated once, truncated per test.

The database server is the one in DATABASE_URL (the Compose db). Set DATABASE_URL to
the test database BEFORE any app module reads settings.
"""
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = "billing_test"

load_dotenv(ROOT / ".env")
ADMIN_URL = os.environ["DATABASE_URL"]
TEST_URL = urlsplit(ADMIN_URL)._replace(path=f"/{TEST_DB}").geturl()
os.environ["DATABASE_URL"] = TEST_URL  # app settings read this

from app.core.db import transaction  # noqa: E402
from app.core.security import generate_api_key, hash_api_key  # noqa: E402
from app.main import app  # noqa: E402
from app.repositories import tenants, usage  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
        ).fetchone()
        if not exists:
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def client(_database):
    # raise_server_exceptions=False so an unexpected error shows up as a 500 status.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean(client):
    with transaction() as conn:
        conn.execute(
            "TRUNCATE alerts, payment_events, subscriptions, checkout_sessions, usage_events, tenants "
            "RESTART IDENTITY CASCADE"
        )
    yield


@pytest.fixture
def make_tenant():
    """Create a tenant. Returns (Tenant, api_key)."""
    def _make(name: str = "Test Tenant", plan: str = "free"):
        key = generate_api_key()
        with transaction() as conn:
            tenant = tenants.create(conn, name, hash_api_key(key), plan)
        return tenant, key
    return _make


@pytest.fixture
def prefill():
    """Insert past usage for a tenant, as if earlier requests had been made this month."""
    def _prefill(tenant_id, api_calls: int = 0, tokens: int = 0):
        with transaction() as conn:
            usage.insert_event(
                conn,
                tenant_id=tenant_id,
                event_id=uuid4(),
                idempotency_key=f"prefill-{api_calls}-{tokens}",
                request_hash="prefill",
                api_calls=api_calls,
                input_tokens=tokens,
                cached_input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cost_micros=0,
                response_status=201,
                response_body={"prefill": True},
                created_at=datetime.now(timezone.utc),
            )
    return _prefill


@pytest.fixture
def count_events():
    def _count(tenant_id=None, key: str | None = None) -> int:
        sql, params = "SELECT count(*) AS n FROM usage_events WHERE true", []
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        if key is not None:
            sql += " AND idempotency_key = %s"
            params.append(key)
        with transaction() as conn:
            return int(conn.execute(sql, params).fetchone()["n"])
    return _count
