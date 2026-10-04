"""PayMongo billing shape: checkout_sessions, per-payment subscriptions, payment_events

Moves the Stripe-shaped schema from 0001 to the PayMongo shape in spec section 8.1.
No production data exists, so the old subscriptions table is dropped, not converted.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN stripe_customer_id")
    op.execute("DROP TABLE subscriptions")

    op.execute(
        """
        CREATE TABLE checkout_sessions (
            id                TEXT PRIMARY KEY,
            tenant_id         UUID NOT NULL REFERENCES tenants(id),
            plan_code         TEXT NOT NULL REFERENCES plans(code),
            amount_centavos   BIGINT NOT NULL CHECK (amount_centavos > 0),
            status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid')),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            paid_at           TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX checkout_sessions_tenant_idx ON checkout_sessions (tenant_id)")

    op.execute(
        """
        CREATE TABLE subscriptions (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id),
            provider_payment_id   TEXT NOT NULL UNIQUE,
            checkout_session_id   TEXT REFERENCES checkout_sessions(id),
            plan_code             TEXT NOT NULL REFERENCES plans(code),
            status                TEXT NOT NULL CHECK (status IN ('active', 'expired')),
            current_period_start  TIMESTAMPTZ NOT NULL,
            current_period_end    TIMESTAMPTZ NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX subscriptions_tenant_idx ON subscriptions (tenant_id)")
    op.execute("CREATE INDEX subscriptions_expiry_idx ON subscriptions (status, current_period_end)")

    op.execute("ALTER TABLE stripe_events RENAME TO payment_events")
    op.execute("ALTER INDEX stripe_events_queue_idx RENAME TO payment_events_queue_idx")


def downgrade() -> None:
    op.execute("ALTER INDEX payment_events_queue_idx RENAME TO stripe_events_queue_idx")
    op.execute("ALTER TABLE payment_events RENAME TO stripe_events")

    op.execute("DROP TABLE subscriptions")
    op.execute("DROP TABLE checkout_sessions")
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
    op.execute("ALTER TABLE tenants ADD COLUMN stripe_customer_id TEXT UNIQUE")
