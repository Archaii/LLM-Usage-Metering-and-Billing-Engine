"""initial schema: plans, tenants, subscriptions, usage_events, stripe_events, alerts

Revision ID: 0001
Revises:
Create Date: 2026-10-04
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE plans (
            code              TEXT PRIMARY KEY,
            name              TEXT   NOT NULL,
            api_call_limit    BIGINT NOT NULL CHECK (api_call_limit >= 0),
            token_limit       BIGINT NOT NULL CHECK (token_limit >= 0),
            base_fee_micros   BIGINT NOT NULL CHECK (base_fee_micros >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tenants (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                TEXT NOT NULL,
            api_key_hash        TEXT NOT NULL UNIQUE,
            plan_code           TEXT NOT NULL REFERENCES plans(code) DEFAULT 'free',
            billing_status      TEXT NOT NULL DEFAULT 'ok'
                                CHECK (billing_status IN ('ok', 'past_due')),
            stripe_customer_id  TEXT UNIQUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE subscriptions (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                UUID NOT NULL REFERENCES tenants(id),
            stripe_subscription_id   TEXT NOT NULL UNIQUE,
            status                   TEXT NOT NULL,
            plan_code                TEXT NOT NULL REFERENCES plans(code),
            current_period_start     TIMESTAMPTZ,
            current_period_end       TIMESTAMPTZ,
            last_event_created       BIGINT NOT NULL,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX subscriptions_tenant_idx ON subscriptions (tenant_id)")
    op.execute(
        """
        CREATE TABLE usage_events (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id),
            idempotency_key       TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 255),
            request_hash          TEXT NOT NULL,
            api_calls             INTEGER NOT NULL DEFAULT 1 CHECK (api_calls >= 0),
            input_tokens          BIGINT  NOT NULL CHECK (input_tokens >= 0),
            cached_input_tokens   BIGINT  NOT NULL CHECK (cached_input_tokens >= 0 AND cached_input_tokens <= input_tokens),
            output_tokens         BIGINT  NOT NULL CHECK (output_tokens >= 0),
            reasoning_tokens      BIGINT  NOT NULL CHECK (reasoning_tokens >= 0),
            cost_micros           BIGINT  NOT NULL CHECK (cost_micros >= 0),
            response_status       SMALLINT NOT NULL,
            response_body         JSONB   NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute("CREATE INDEX usage_events_tenant_time_idx ON usage_events (tenant_id, created_at)")
    op.execute(
        """
        CREATE TABLE stripe_events (
            event_id         TEXT PRIMARY KEY,
            type             TEXT NOT NULL,
            event_created    BIGINT NOT NULL,
            payload          JSONB NOT NULL,
            status           TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'processed', 'skipped', 'failed')),
            attempts         INTEGER NOT NULL DEFAULT 0,
            next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_error       TEXT,
            received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at     TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX stripe_events_queue_idx ON stripe_events (status, next_attempt_at)")
    op.execute(
        """
        CREATE TABLE alerts (
            id          BIGSERIAL PRIMARY KEY,
            source      TEXT NOT NULL,
            ref         TEXT,
            message     TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    for table in ("alerts", "stripe_events", "usage_events", "subscriptions", "tenants", "plans"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
