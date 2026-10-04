# Design — Usage Metering & Billing Engine

## 1. Problem

A SaaS product sells an AI feature on two plans (Free, Pro). For every tenant the
service must answer three questions:

1. How much has this tenant used this month?
2. Have they reached their plan limit?
3. What does it cost, including AI-token pricing rules?

It must meter exactly once under client retries, reject over-limit requests with
an honest status and message, price money without floats, and keep the tenant's
plan in sync with PayMongo (test mode only) through signed, deduplicated webhooks.

**Explicit non-goals:** invoicing, proration, and overage billing. No live PayMongo
mode and no auto-renewing subscriptions (Pro is a prepaid 30-day period; renewals
stack after the running one). No real AI model (token counts are simulated by the
client), no per-tenant billing cycles, no frontend.

## 2. Database schema

PostgreSQL 16, created by Alembic migrations (`0001_initial_schema`, then
`0002_paymongo_billing`). Money columns are `BIGINT` micro-USD (`1 USD = 1,000,000`).
Raw SQL in the repository layer, no ORM.

### 2.1 `plans`

| Column | Type | Notes |
| --- | --- | --- |
| `code` | `TEXT` PK | `free` or `pro` |
| `name` | `TEXT` | Display name |
| `api_call_limit` | `BIGINT` | Calls per calendar month, `>= 0` |
| `token_limit` | `BIGINT` | Quota tokens per calendar month, `>= 0` |
| `base_fee_micros` | `BIGINT` | Fee per period, `>= 0` |

Filled from `config/pricing.toml`. The app refuses to start if the table and the
file disagree.

### 2.2 `tenants`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` PK | `gen_random_uuid()` |
| `name` | `TEXT` | |
| `api_key_hash` | `TEXT` UNIQUE | SHA-256 hex of the API key; the key itself is never stored |
| `plan_code` | `TEXT` FK `plans` | Default `free` |
| `billing_status` | `TEXT` | `ok` or `past_due` (CHECK) |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | |

### 2.3 `subscriptions`

One row per paid 30-day Pro period.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK `tenants` | Indexed |
| `provider_payment_id` | `TEXT` UNIQUE | PayMongo `pay_...` ID; the grant dedupe key |
| `checkout_session_id` | `TEXT` FK `checkout_sessions` | |
| `plan_code` | `TEXT` FK `plans` | |
| `status` | `TEXT` | `active` or `expired` (CHECK) |
| `current_period_start`, `current_period_end` | `TIMESTAMPTZ` | Index on `(status, current_period_end)` for expiry |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | |

### 2.4 `usage_events`

One row per billable request. It carries both meters, so "exactly one event" is a
plain `SELECT count(*)`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK `tenants` | |
| `idempotency_key` | `TEXT` | 1 to 255 characters (CHECK) |
| `request_hash` | `TEXT` | SHA-256 of the canonical request body |
| `api_calls` | `INTEGER` | Default 1, `>= 0` |
| `input_tokens` | `BIGINT` | Includes cached tokens, `>= 0` |
| `cached_input_tokens` | `BIGINT` | `0 <= cached <= input_tokens` (CHECK) |
| `output_tokens`, `reasoning_tokens` | `BIGINT` | `>= 0` |
| `cost_micros` | `BIGINT` | Cost of this event; informational, the monthly rollup is authoritative |
| `response_status` | `SMALLINT` | |
| `response_body` | `JSONB` | Replayed on a retry |
| `created_at` | `TIMESTAMPTZ` | |

Constraints: `UNIQUE (tenant_id, idempotency_key)`; index `(tenant_id, created_at)`
for the monthly rollup.

### 2.5 Supporting tables

| Table | Purpose | Key constraints |
| --- | --- | --- |
| `checkout_sessions` | Maps each PayMongo checkout session (`cs_...`) to its tenant and amount, so a webhook never trusts metadata alone. | PK `id` |
| `payment_events` | Webhook inbox and work queue: status, attempts, `next_attempt_at`, `last_error`. | PK `event_id` (dedupe key); index `(status, next_attempt_at)` |
| `alerts` | Failure alerts raised by the worker. | PK `id` |

## 3. Plans and quotas

All constants are pinned in `config/pricing.toml` and loaded once at startup.

| Plan | API calls / month | AI tokens / month | Base fee |
| --- | --- | --- | --- |
| Free | 1,000 | 100,000 | $0.00 |
| Pro | 50,000 | 5,000,000 | $29.00 per 30-day period |

The Pro checkout charges a fixed PHP 1,650.00 (PayMongo charges Philippine pesos);
the ledger itself stays in USD micros.

**Quota tokens** for a request are `input_tokens + output_tokens + reasoning_tokens`.
Cached tokens are already inside `input_tokens`, so they are not counted twice.

**Period:** the calendar month in UTC, `[first day 00:00, first day of next month 00:00)`.

**Boundary rule:** a request is allowed only if, for both meters,
`used + requested <= limit`. Token requests are all-or-nothing: a request that would
cross the token limit is rejected whole.

- 999 of 1,000 calls used, next call: allowed (it makes 1,000).
- 1,000 of 1,000 used, next call: rejected.

The check order is billing status, then the API-call meter, then the token meter.
The first failing meter is named in `details.meter`. The quota is also the budget
guard for AI cost: no request can push a tenant past its token allowance.

| Situation | Status | `error` | Reason |
| --- | --- | --- | --- |
| `billing_status = past_due` | `402` | `payment_required` | Payment is the blocker, not usage |
| Free, quota would be exceeded | `402` | `upgrade_required` | Paying (Pro) unblocks now; body has `upgrade_url` |
| Pro, quota would be exceeded | `429` | `quota_exceeded` | Nothing to buy; wait for the reset; `Retry-After` = seconds to `period_end` |

## 4. Metering API contract

All bodies are JSON. Every error uses one body: `{error, message, details?, upgrade_url?}`.
Every tenant endpoint authenticates with `X-API-Key`; there is no tenant ID in the
URL or body.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Liveness and database check |
| `GET` | `/plans` | none | Plans, limits and rates |
| `POST` | `/generate` | API key + `Idempotency-Key` | The billable action: meter, check quota, price |
| `GET` | `/usage` | API key | Current-month rollup: used, limit, cost |
| `POST` | `/billing/checkout` | API key | PayMongo Hosted Checkout Session for one Pro period |
| `GET` | `/billing/success`, `/billing/cancel` | none | Landing pages; they never change the plan |
| `POST` | `/webhooks/paymongo` | `Paymongo-Signature` | Receives PayMongo events |

### 4.1 `POST /generate`

Request:

```http
POST /generate
X-API-Key: mk_test_...
Idempotency-Key: 7f9c2a1e-5b2d-4c1e-9a8b-0d3f6e2a1b44
Content-Type: application/json

{
  "prompt": "Summarize this ticket",
  "tokens": {
    "input_tokens": 10000,
    "cached_input_tokens": 4000,
    "output_tokens": 2000,
    "reasoning_tokens": 1500
  }
}
```

Validation: `prompt` 1 to 4,000 characters; each token count `0..1,000,000`;
`cached_input_tokens <= input_tokens`.

Response `201 Created` (first time) or `200 OK` with header `Idempotent-Replayed: true`
(a retry, same body):

```json
{
  "event_id": "3d0f...",
  "tenant_id": "a1b2...",
  "output": "[simulated] response to: Summarize this ticket",
  "tokens": { "input_tokens": 10000, "cached_input_tokens": 4000, "output_tokens": 2000, "reasoning_tokens": 1500 },
  "cost": {
    "api_call_micros": 2000, "fresh_input_micros": 1800, "cached_input_micros": 300,
    "output_micros": 8750, "total_micros": 12850, "total_usd": "0.012850"
  },
  "quota": { "api_calls_used": 1, "api_call_limit": 1000, "tokens_used": 13500, "token_limit": 100000 },
  "created_at": "2026-10-04T12:00:00Z"
}
```

| Status | `error` | When |
| --- | --- | --- |
| `201` | | New usage event created |
| `200` | | Replay of a stored response (same key, same body) |
| `400` | `idempotency_key_missing` | No `Idempotency-Key` header, or longer than 255 characters |
| `401` | `unauthorized` | Missing or unknown API key |
| `402` | `upgrade_required` | Free tenant would exceed a Free quota |
| `402` | `payment_required` | Tenant `billing_status = past_due` |
| `422` | `validation_error` | Body fails validation (negative tokens, cached > input, empty prompt) |
| `422` | `idempotency_key_reused` | Same key, different body |
| `429` | `quota_exceeded` | Pro tenant would exceed a Pro quota; includes `Retry-After` |

Bad input never produces a `500`; an unexpected error returns
`500 {"error": "internal_error"}` with no secrets in the body or the log.

### 4.2 `GET /usage`

```json
{
  "tenant_id": "a1b2...",
  "plan": "free",
  "billing_status": "ok",
  "period_start": "2026-10-01T00:00:00Z",
  "period_end": "2026-11-01T00:00:00Z",
  "api_calls": { "used": 1, "limit": 1000, "remaining": 999, "cost_micros": 2000 },
  "tokens": {
    "used": 13500, "limit": 100000, "remaining": 86500, "cost_micros": 10850,
    "breakdown": { "input": 10000, "cached_input": 4000, "fresh_input": 6000, "output": 2000, "reasoning": 1500 }
  },
  "base_fee_micros": 0,
  "total_micros": 12850,
  "total_usd": "0.012850"
}
```

One aggregate query sums the month's counts for the tenant. Limits come from the
tenant's current plan, so they change as soon as the tenant upgrades.

## 5. Idempotency strategy

- The key is **per tenant**: `UNIQUE (tenant_id, idempotency_key)` is the database-level
  guarantee. Two tenants may reuse the same key without seeing each other.
- `request_hash = sha256(canonical_json(body))`, with sorted keys and no spaces, so the
  same logical body always hashes the same way.
- `MeterService.record` runs in **one transaction**:
  1. `SELECT ... FROM tenants ... FOR UPDATE` serializes metering for that tenant, so two
     concurrent requests cannot both pass the quota check. Other tenants are not blocked.
  2. Key found, same hash: return the stored response (`200`, `Idempotent-Replayed: true`).
     No new row, and the quota is not re-checked.
  3. Key found, different hash: `422 idempotency_key_reused`.
  4. Key not found: quota check, then price, then insert the event with its stored
     response, then `201`. If the insert still hits the unique constraint (it should be
     impossible under the row lock), fall back to step 2.
- Rejections (`402`/`429`) are **not** stored, so the same key can succeed later, for
  example after an upgrade.
- Webhooks use the same idea with a different key: `payment_events.event_id` is the
  primary key, and `subscriptions.provider_payment_id` is a second layer.

## 6. Money and pricing

- Integer micro-USD everywhere. Floats never touch money.
- Token prices are stored as micros per 1,000,000 tokens, so they stay integers:
  input 300,000; cached input 75,000; output 2,500,000; reasoning 2,500,000 (billed as
  output); API call 2,000 per call.
- Cost of a token line is `tokens x price_per_million`, divided by 1,000,000 once at the end
  with round-half-up. Fresh input (`input - cached`), cached input and `output + reasoning`
  are priced separately, then summed.
- The monthly rollup sums token counts per category first and prices each category once,
  so there is no per-event rounding drift.
  `total = base_fee + api_calls x 2,000 + token_cost`.
- The loader refuses a config where the reasoning rate differs from the output rate.

Worked example: 10,000 input (4,000 cached), 2,000 output, 1,500 reasoning gives
10,850 micros for tokens ($0.010850), and 12,850 micros with the API call.

## 7. Payments and webhooks

1. `POST /billing/checkout` creates a PayMongo Hosted Checkout Session for one 30-day Pro
   period and stores the session-to-tenant mapping.
2. The webhook handler reads the **raw** body, verifies `Paymongo-Signature`
   (HMAC-SHA256 over `"{t}.{raw body}"`, constant-time compare, 300 s tolerance), and on
   failure returns `400` and writes nothing.
3. `INSERT INTO payment_events ... ON CONFLICT (event_id) DO NOTHING`. No row returned means
   a duplicate: `200 {"received": true, "duplicate": true}`. The handler returns `200` fast.
4. The worker polls `pending` events with `FOR UPDATE SKIP LOCKED`. A signed event is not
   proof of payment (the body is a pre-settlement snapshot), so the worker asks PayMongo for
   the session's current state. A paid payment of the right amount grants a 30-day Pro
   period; an unsettled one is retried.
5. Failures retry with backoff 2/4/8/16 s. The fifth failure sets `failed`, writes an
   `alerts` row and logs at `ERROR`.
6. The same worker expires lapsed Pro periods and returns the tenant to Free. A renewal
   paid while Pro starts after the running period ends, so there is no gap.

The success page never changes the plan. Only a verified webhook does.

## 8. Layers, auth and secrets

```
HTTP        app/api/           routes, Pydantic schemas, auth dependency, error to status map
Logic       app/services/      MeterService, QuotaService, PricingService, BillingService, WebhookService
Data        app/repositories/  SQL only; every tenant-owned query takes tenant_id
Integration app/integrations/  the only code that talks to PayMongo
Worker      app/worker.py      applies queued payment events, retries, alerts, expires Pro periods
```

Routes never write SQL, repositories never decide business rules, and services never
import FastAPI: they raise domain errors that `app/api/errors.py` maps to statuses.

- One API key per tenant (`mk_test_` prefix), stored as a SHA-256 hash only. The
  `current_tenant` dependency resolves the tenant once per request; every tenant-owned
  query filters on `tenant_id`.
- Secrets live in `.env` only (git-ignored; `.env.example` holds placeholders). The app
  refuses to start with a PayMongo key that does not begin with `sk_test_`, and settings
  hide secrets from `repr`.
