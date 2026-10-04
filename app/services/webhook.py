"""Webhooks: receive and store on the request path; apply in the worker (spec section 14)."""
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg

from app.core.config import get_pricing, get_settings
from app.core.db import transaction
from app.core.errors import InvalidPayload, PaymentNotSettled
from app.core.models import PaymentEvent
from app.integrations.paymongo import PayMongoClient, verify_signature
from app.repositories import checkout_sessions, payment_events, subscriptions, tenants

HANDLED_TYPES = frozenset({"checkout_session.payment.paid"})


@dataclass(frozen=True)
class ReceiveResult:
    duplicate: bool


@dataclass(frozen=True)
class Outcome:
    status: str  # 'processed' or 'skipped'
    reason: str | None = None


@dataclass(frozen=True)
class ParsedEvent:
    event_id: str
    type: str
    created: int
    payload: dict


def parse_event(raw_body: bytes, now: float | None = None) -> ParsedEvent:
    """Pull the fields we need out of PayMongo's event envelope, or raise InvalidPayload.

    Real events carry `created_at: null` on the envelope, so the receipt time stands in for it.
    """
    try:
        payload = json.loads(raw_body)
        event = payload["data"]
        attributes = event["attributes"]
        event_id = event["id"]
        event_type = attributes["type"]
        created_raw = attributes.get("created_at")
        created = int(time.time() if now is None else now) if created_raw is None else int(created_raw)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidPayload("The body is not a PayMongo event.") from None
    if not isinstance(event_id, str) or not isinstance(event_type, str) or not event_id:
        raise InvalidPayload("The body is not a PayMongo event.")
    if attributes.get("livemode") is True:
        raise InvalidPayload("Live-mode events are not accepted: this app is test-mode only.")
    return ParsedEvent(event_id=event_id, type=event_type, created=created, payload=payload)


class WebhookService:
    def __init__(self, client: PayMongoClient | None = None):
        self._client = client

    def _provider(self) -> PayMongoClient:
        if self._client is None:
            self._client = PayMongoClient(get_settings().paymongo_secret_key)
        return self._client

    def receive(self, raw_body: bytes, signature_header: str | None, now: float | None = None) -> ReceiveResult:
        """Verify, parse, and store one delivery. Raises InvalidSignature or InvalidPayload."""
        settings = get_settings()
        verify_signature(
            raw_body,
            signature_header,
            settings.paymongo_webhook_secret,
            settings.webhook_tolerance_seconds,
            now,
        )
        event = parse_event(raw_body, now)
        handled = event.type in HANDLED_TYPES
        with transaction() as conn:
            inserted = payment_events.insert_if_new(
                conn,
                event_id=event.event_id,
                event_type=event.type,
                event_created=event.created,
                payload=event.payload,
                status="pending" if handled else "skipped",
                last_error=None if handled else "unhandled_type",
            )
        return ReceiveResult(duplicate=not inserted)

    def process(self, conn: psycopg.Connection, event: PaymentEvent, now: datetime | None) -> Outcome:
        """Apply one stored event. Runs inside the worker's transaction (and savepoint)."""
        if event.type == "checkout_session.payment.paid":
            return self._apply_payment_paid(conn, event)
        return Outcome("skipped", "unhandled_type")

    def _apply_payment_paid(self, conn: psycopg.Connection, event: PaymentEvent) -> Outcome:
        resource = event.payload["data"]["attributes"].get("data") or {}
        session_id = resource.get("id")
        if not isinstance(session_id, str) or not session_id:
            return Outcome("skipped", "no_session_id")

        session = checkout_sessions.get(conn, session_id)
        tenant_id = session.tenant_id if session else _tenant_from_metadata(conn, resource.get("attributes") or {})
        if tenant_id is None:
            return Outcome("skipped", "tenant_not_found")

        # The event body is only a snapshot taken before the payment settled: real events arrive
        # with payments [] and paid_at null. PayMongo's current record decides.
        current = self._provider().get_checkout_session(session_id)
        payment = _first_paid_payment(current.get("attributes") or {})
        if payment is None:
            raise PaymentNotSettled(f"Checkout session {session_id} has no paid payment yet.")

        checkout = get_pricing().checkout
        expected_amount = session.amount_centavos if session else checkout.pro_amount_centavos
        if payment["amount"] != expected_amount:
            return Outcome("skipped", "amount_mismatch")

        # Same lock metering takes, so a plan change never lands in the middle of a request.
        if tenants.lock_for_update(conn, tenant_id) is None:
            return Outcome("skipped", "tenant_not_found")

        paid_at = datetime.fromtimestamp(payment["paid_at"] or event.event_created, tz=timezone.utc)
        granted = subscriptions.insert_if_new(
            conn,
            tenant_id=tenant_id,
            provider_payment_id=payment["id"],
            checkout_session_id=session.id if session else None,
            plan_code="pro",
            period_start=paid_at,
            period_end=paid_at + timedelta(days=checkout.pro_period_days),
        )
        if not granted:
            return Outcome("skipped", "duplicate_payment")

        tenants.set_plan(conn, tenant_id, "pro", billing_status="ok")
        if session:
            checkout_sessions.mark_paid(conn, session.id, paid_at)
        return Outcome("processed")


def _tenant_from_metadata(conn: psycopg.Connection, attributes: dict) -> UUID | None:
    raw = (attributes.get("metadata") or {}).get("tenant_id")
    try:
        tenant_id = UUID(str(raw))
    except ValueError:
        return None
    return tenant_id if tenants.get_by_id(conn, tenant_id) else None


def _first_paid_payment(attributes: dict) -> dict | None:
    """The first payment marked paid, as {'id', 'amount', 'paid_at'}; None if there is none."""
    for payment in attributes.get("payments") or []:
        pay_attrs = payment.get("attributes") or {}
        if pay_attrs.get("status") == "paid" and isinstance(payment.get("id"), str):
            amount = pay_attrs.get("amount")
            if isinstance(amount, int):
                paid_at = pay_attrs.get("paid_at")
                return {
                    "id": payment["id"],
                    "amount": amount,
                    "paid_at": paid_at if isinstance(paid_at, int) else None,
                }
    return None
