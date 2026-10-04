"""SQL for usage_events. Every function takes tenant_id and filters on it."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.models import UsageTotals


@dataclass(frozen=True)
class StoredEvent:
    request_hash: str
    response_body: dict


def find_by_key(
    conn: psycopg.Connection, tenant_id: UUID, idempotency_key: str
) -> StoredEvent | None:
    row = conn.execute(
        """
        SELECT request_hash, response_body
        FROM usage_events
        WHERE tenant_id = %s AND idempotency_key = %s
        """,
        (tenant_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return StoredEvent(request_hash=row["request_hash"], response_body=row["response_body"])


def totals_for_period(
    conn: psycopg.Connection, tenant_id: UUID, start: datetime, end: datetime
) -> UsageTotals:
    row = conn.execute(
        """
        SELECT coalesce(sum(api_calls), 0)           AS api_calls,
               coalesce(sum(input_tokens), 0)        AS input_tokens,
               coalesce(sum(cached_input_tokens), 0) AS cached_input_tokens,
               coalesce(sum(output_tokens), 0)       AS output_tokens,
               coalesce(sum(reasoning_tokens), 0)    AS reasoning_tokens
        FROM usage_events
        WHERE tenant_id = %s AND created_at >= %s AND created_at < %s
        """,
        (tenant_id, start, end),
    ).fetchone()
    return UsageTotals(
        api_calls=int(row["api_calls"]),
        input_tokens=int(row["input_tokens"]),
        cached_input_tokens=int(row["cached_input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        reasoning_tokens=int(row["reasoning_tokens"]),
    )


def insert_event(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    event_id: UUID,
    idempotency_key: str,
    request_hash: str,
    api_calls: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cost_micros: int,
    response_status: int,
    response_body: dict,
    created_at: datetime,
) -> None:
    """Raises psycopg.errors.UniqueViolation on a duplicate (tenant_id, idempotency_key)."""
    conn.execute(
        """
        INSERT INTO usage_events (
            id, tenant_id, idempotency_key, request_hash, api_calls,
            input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
            cost_micros, response_status, response_body, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id, tenant_id, idempotency_key, request_hash, api_calls,
            input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
            cost_micros, response_status, Jsonb(response_body), created_at,
        ),
    )
