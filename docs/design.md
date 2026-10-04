# Design — Usage Metering & Billing Engine

One-page design. Full contract: [spec.md](spec.md). Roadmap: [tasks.md](tasks.md).

## 1. Problem

A SaaS product sells an AI feature on two plans (Free, Pro). For every tenant the
service must answer three questions:

1. How much has this tenant used this month?
2. Have they reached their plan limit?
3. What does it cost, including AI-token pricing rules?

It must meter exactly once under client retries, reject over-limit requests with
an honest status and message, price money without floats, and keep the tenant's
plan in sync with PayMongo (test mode only) through signed, deduplicated webhooks.

**Explicit non-goal:** invoicing, proration, and overage billing. No live PayMongo
mode, no auto-renewing subscriptions (Pro is a prepaid 30-day period), no real AI model (token counts are simulated), no per-tenant billing
cycles, no frontend (spec §1.2).

## 2. Data model

Six PostgreSQL tables, created by Alembic migration `0001_initial_schema`
(spec §8.1). Money columns are `BIGINT` micro-USD.

| Table | Purpose | Key constraints |
| --- | --- | --- |
| `plans` | `free` / `pro`, limits, base fee. Seeded from `config/pricing.toml`. | PK `code` |
| `tenants` | Tenant, SHA-256 API-key hash, `plan_code`, `billing_status` (`ok`/`past_due`). | UNIQUE `api_key_hash` |
| `checkout_sessions` | Maps each PayMongo checkout session to its tenant, so the webhook never trusts metadata alone. | PK `id` (cs_...) |
| `subscriptions` | One row per paid 30-day Pro period; the worker expires lapsed periods. | UNIQUE `provider_payment_id` |
| `usage_events` | One row per billable request: both meters, token breakdown, `cost_micros`, stored response. | UNIQUE `(tenant_id, idempotency_key)`; index `(tenant_id, created_at)` |
| `payment_events` | Webhook inbox and work queue; `event_id` is the dedupe key. | PK `event_id`; index `(status, next_attempt_at)` |
| `alerts` | Failure alerts raised by the worker. | — |

## 3. API surface

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Liveness + DB check |
| `GET` | `/plans` | none | Plans, limits, rates |
| `POST` | `/generate` | API key + `Idempotency-Key` | Billable action: meter, check quota, price |
| `GET` | `/usage` | API key | Current-month rollup: used, limit, cost |
| `POST` | `/billing/checkout` | API key | PayMongo Hosted Checkout Session for one Pro period |
| `GET` | `/billing/success`, `/billing/cancel` | none | Static landing pages; never change the plan |
| `POST` | `/webhooks/paymongo` | `Paymongo-Signature` | Receive PayMongo events |

All errors share one body: `{error, message, details?, upgrade_url?}`.

## 4. Layers

```
HTTP        app/api/           routes, Pydantic schemas, auth dependency, error → status map
Logic       app/services/      MeterService, QuotaService, PricingService, BillingService, WebhookService
Data        app/repositories/  SQL only; every tenant-owned query takes tenant_id
Worker      app/worker.py      applies queued payment events, retries, alerts, expires Pro periods
```

Routes never write SQL. Repositories never decide business rules. Services never
import FastAPI; they raise domain errors that `app/api/errors.py` maps to
statuses.

## 5. Money

- Integer micro-USD (`1 USD = 1,000,000`). Floats never touch money.
- Token prices are micros per 1,000,000 tokens, so they stay integers.
- A cost line is `tokens × price_per_million`, divided by 1,000,000 once at the
  end with round-half-up.
- The monthly rollup sums token counts per category, then prices each category
  once. No per-event rounding drift.
- Pinned constants live in `config/pricing.toml`; the loader asserts
  `reasoning rate == output rate`.

Pinned worked example: 10,000 input (4,000 cached), 2,000 output, 1,500
reasoning → 10,850 micros ($0.010850) tokens, 12,850 micros with the API call.

## 6. Idempotency strategy (spec §11)

- Key is per tenant: `UNIQUE (tenant_id, idempotency_key)` is the database-level
  guarantee. Two tenants may reuse the same key.
- `request_hash = sha256(canonical_json(body))` (sorted keys, no spaces).
- `MeterService.record` runs in one transaction:
  1. `SELECT … FROM tenants … FOR UPDATE` serializes metering per tenant, so two
     concurrent requests cannot both pass the quota check.
  2. Key found, same hash → replay stored response (`200`, `Idempotent-Replayed: true`), no new row, no quota re-check.
  3. Key found, different hash → `422 idempotency_key_reused`.
  4. Not found → quota check → price → insert event with stored response → `201`.
- Rejections are not stored, so the same key can succeed later (for example after an upgrade).

## 7. Quota boundary rule (spec §12)

Period: calendar month, UTC. A request is allowed only if, for both meters,
`used + requested ≤ limit`. Token requests are all-or-nothing.

- 999 of 1,000 calls used, next call → allowed (makes 1,000).
- 1,000 of 1,000 used, next call → rejected.

Check order: billing status, API-call meter, token meter. First failing meter is
named in `details.meter`.

| Situation | Status | `error` | Reason |
| --- | --- | --- | --- |
| `billing_status = past_due` | `402` | `payment_required` | Payment is the blocker |
| Free, quota would be exceeded | `402` | `upgrade_required` | Paying (Pro) unblocks now; body has `upgrade_url` |
| Pro, quota would be exceeded | `429` | `quota_exceeded` | Nothing to buy; wait for reset; `Retry-After` = seconds to `period_end` |

## 8. Webhook strategy (spec §14, §15)

1. Read the **raw** body. Do not parse JSON first.
2. HMAC-SHA256 over `"{t}.{raw body}"` is compared (constant time) with the `te` value of `Paymongo-Signature`. Failure → `400`,
   nothing written.
3. `INSERT INTO payment_events … ON CONFLICT (event_id) DO NOTHING`. No row
   returned → duplicate → `200 {"duplicate": true}`.
4. Return `200` fast. Unhandled event types are stored as `skipped`.
5. Worker polls `pending` rows with `FOR UPDATE SKIP LOCKED` and, for
   `checkout_session.payment.paid`, looks up the tenant through the stored
   `checkout_sessions` row, checks the paid amount, and grants a 30-day Pro period.
6. Second dedupe layer: `subscriptions.provider_payment_id` is UNIQUE, so two
   events about one payment grant Pro once. There is no ordering guard because
   grants are independent.
7. Failure -> retry with backoff 2/4/8/16/32 s. After 5 failures: `failed`, an
   `alerts` row, and an `ERROR` log.
8. The same worker expires lapsed Pro periods and returns the tenant to Free.

The Checkout success page never changes the plan. Only a verified webhook does.

## 9. Auth and isolation

One API key per tenant (`X-API-Key`, `mk_test_` prefix), stored as SHA-256 only.
`current_tenant` resolves the tenant once per request. No endpoint accepts a
tenant ID from the URL or body. Every tenant-owned query filters on `tenant_id`.
`/webhooks/paymongo` is authenticated by the `Paymongo-Signature` header, not an API key.

## 10. Secrets

Env only (`.env`, git-ignored; `.env.example` holds placeholders). The app
refuses to start with a PayMongo key that is not `sk_test_`. Settings
objects hide secrets from `repr`; logs never carry keys or signatures.
