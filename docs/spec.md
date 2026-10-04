# Technical Specification — Usage Metering & Billing Engine

Source brief: [Usage Metering Billing Engine Live Capstone.pdf](Usage%20Metering%20Billing%20Engine%20Live%20Capstone.pdf)
(FlyRank Internship, Backend Track). This document is the build contract. When
code and this document disagree, fix one of them in the same commit.

---

## 1. Overview

The service answers three questions for every tenant:

1. **How much has this tenant used?** — usage metering.
2. **Have they reached their plan limit?** — quota enforcement.
3. **What does it cost?** — cost calculation, including AI-token pricing rules.

Plan changes come from Stripe (test mode only) through signed, deduplicated
webhooks. Stripe holds payment truth; the database mirrors it.

### 1.1 Goals

| # | Goal | Proof |
| --- | --- | --- |
| G1 | Exactly-once metering: one billable request plus one idempotency key creates one usage event, whatever the number of retries. | Probe 1 |
| G2 | Boundary honesty: the request that reaches the limit exactly is allowed; the next one is rejected with `402` or `429` and a clear message. | Probe 2 |
| G3 | Correct money math: integer micro-USD, cached input priced lower, reasoning priced as output. | Probe 5 |
| G4 | Safe Stripe sync: forged webhook gets `400` and changes nothing; a replayed event is processed once; Checkout flips a tenant Free to Pro. | Probes 3, 4 |

### 1.2 Non-goals

- Invoicing, proration, and overage billing (stretch goals only, see `tasks.md`).
- Stripe live mode or real money. Test mode only, test card `4242 4242 4242 4242`.
- Calling a real AI model. Token counts are simulated and supplied by the client.
- Per-tenant billing cycles. The usage period is the calendar month in UTC.
- A dashboard or frontend. The API is the product.

---

## 2. Tech stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Language | Python 3.12 | `tomllib` in the standard library reads the pricing file. |
| HTTP framework | FastAPI + Uvicorn | Swagger UI at `/docs`. |
| Validation / types | Pydantic v2 | All request and response bodies are Pydantic models. |
| Database | PostgreSQL 16 in Docker | `docker compose up`. Host port `5433`, container port `5432`. |
| DB driver | psycopg 3 (`psycopg[binary]`) | Raw SQL in the repository layer, no ORM. |
| Migrations | Alembic | Migrations written as explicit SQL with `op.execute`. |
| Payments | Stripe test mode, `stripe` Python SDK | Checkout Sessions + webhooks. |
| Local webhooks | Stripe CLI | `stripe listen`, `stripe trigger`, `stripe events resend`. |
| Config | python-dotenv + `config/pricing.toml` | Secrets in `.env`; prices pinned in TOML. |
| Tests | pytest + FastAPI `TestClient` (httpx) | Runs against the Compose database. |

---

## 3. Architecture

Three layers plus one background worker. Routes never write SQL; repositories
never decide business rules.

| Layer | Package | Responsibility |
| --- | --- | --- |
| HTTP | `app/api/` | Routes, Pydantic schemas, auth dependency, error-to-status mapping. |
| Logic | `app/services/` | `MeterService`, `QuotaService`, `PricingService`, `BillingService`, `WebhookService`. |
| Data | `app/repositories/` | SQL only. Every tenant-owned query takes `tenant_id` as a parameter. |
| Worker | `app/worker.py` | Applies queued Stripe events off the request path, with retries and alerts. |

```
Client ──POST /generate (X-API-Key, Idempotency-Key)──► api
   │
   └─► MeterService.record(tenant, request, idempotency_key)        [one DB transaction]
          │ lock tenant row (SELECT … FOR UPDATE)
          │ key seen, same body?      → return stored response (no new event)
          │ key seen, different body? → 422 idempotency_key_reused
          │ QuotaService.check(used + requested ≤ limit)
          │     └─ exceeded → 402 / 429 + clear message (nothing stored)
          │ PricingService.cost(tokens)
          └─ INSERT usage_event (+ stored response) → 201

Client ──GET /usage──► rollup(usage_events for current month) → { used, limit, cost }

Client ──POST /billing/checkout──► Stripe Checkout (test mode) → subscription created

Stripe ──signed webhook──► POST /webhooks/stripe
          │ verify signature (forged → 400, no write)
          │ INSERT stripe_events ON CONFLICT DO NOTHING (replay → ignored)
          └─ 200 fast
worker ──poll stripe_events (FOR UPDATE SKIP LOCKED)──► update tenant plan / status
          └─ retry with backoff; after 5 failures → status=failed + alert row + ERROR log
```

---

## 4. Project layout

```
.
├── README.md
├── capstone.yaml            # evaluator manifest
├── EVIDENCE.md              # one proof per requirement
├── BUILDLOG.md              # AI-usage log
├── .env.example
├── compose.yaml             # services: db, api, worker
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── config/
│   └── pricing.toml         # pinned plans + prices
├── migrations/
│   └── versions/            # 0001_initial_schema.py, …
├── app/
│   ├── main.py              # FastAPI app, exception handlers, router wiring
│   ├── worker.py            # background job loop
│   ├── seed.py              # demo tenants + API keys
│   ├── core/
│   │   ├── config.py        # env settings, pricing loader
│   │   ├── db.py            # connection pool, transaction helper
│   │   ├── money.py         # integer rounding helpers
│   │   └── security.py      # API-key hashing
│   ├── api/
│   │   ├── deps.py          # current_tenant dependency
│   │   ├── schemas.py       # Pydantic models (section 8.2)
│   │   ├── errors.py        # domain error → HTTP status mapping
│   │   └── routes/          # generate.py, usage.py, plans.py, billing.py, webhooks.py, health.py
│   ├── services/            # meter.py, quota.py, pricing.py, billing.py, webhook.py
│   └── repositories/        # tenants.py, plans.py, usage.py, subscriptions.py, stripe_events.py, alerts.py
├── tests/
└── docs/
    ├── spec.md
    └── tasks.md
```

---

## 5. Money rules

- All money is an integer number of **micro-USD**: `1 USD = 1,000,000 micros`.
  Database columns are `BIGINT`. Python values are `int`. Floats never touch money.
- Token prices are stored as **micros per 1,000,000 tokens**, so they stay integers.
- A token cost line is computed as `raw = tokens × price_per_million` (an exact
  integer), then divided by 1,000,000 **once**, at the end, with round-half-up:

  ```python
  def micros_from_raw(raw: int) -> int:
      # raw = tokens × micros-per-million-tokens; raw is never negative
      return (raw + 500_000) // 1_000_000
  ```

- The monthly rollup sums the **token counts** per category first, then prices
  each category once. It does not sum per-event rounded costs. The per-event
  `cost_micros` stored on each usage event is informational; the rollup is the
  authoritative figure.
- Display values (`"$0.010850"`) are formatted from integers with string
  operations or `Decimal`, never with `float`.

---

## 6. Plans and pricing

All constants live in `config/pricing.toml` and are loaded once at startup. The
`plans` table is seeded from this file, and the app refuses to start if the
table and the file disagree.

```toml
# config/pricing.toml — pinned pricing. Change only with a migration note in BUILDLOG.md.
currency = "usd"
micros_per_usd = 1_000_000

[plans.free]
name = "Free"
api_call_limit = 1_000          # per calendar month, UTC
token_limit = 100_000           # per calendar month, UTC
base_fee_micros = 0

[plans.pro]
name = "Pro"
api_call_limit = 50_000
token_limit = 5_000_000
base_fee_micros = 29_000_000    # $29.00 / month, matches the Stripe test Price
# The Stripe Price ID is environment-specific: STRIPE_PRO_PRICE_ID in .env

[rates]
api_call_micros = 2_000                         # $0.002 per API call
input_micros_per_million = 300_000              # $0.30 per 1M fresh input tokens
cached_input_micros_per_million = 75_000        # $0.075 per 1M cached input tokens (25% of input)
output_micros_per_million = 2_500_000           # $2.50 per 1M output tokens
reasoning_micros_per_million = 2_500_000        # reasoning is billed at the output rate
```

| Plan | API calls / month | AI tokens / month | Base fee |
| --- | --- | --- | --- |
| Free | 1,000 | 100,000 | $0.00 |
| Pro | 50,000 | 5,000,000 | $29.00 |

`reasoning_micros_per_million` must equal `output_micros_per_million`. The
config loader asserts this, so the rule "reasoning tokens count as output" cannot
drift.

---

## 7. Token pricing rules

### 7.1 Input fields

| Field | Meaning | Constraint |
| --- | --- | --- |
| `input_tokens` | Total prompt tokens, **including** cached ones. | `0 ≤ n ≤ 1,000,000` |
| `cached_input_tokens` | Part of `input_tokens` served from cache. | `0 ≤ n ≤ input_tokens` |
| `output_tokens` | Visible output tokens. | `0 ≤ n ≤ 1,000,000` |
| `reasoning_tokens` | Hidden "thinking" tokens. | `0 ≤ n ≤ 1,000,000` |

### 7.2 Rules

1. `fresh_input = input_tokens − cached_input_tokens`. Cached tokens are a
   subset of input, so they are never billed twice.
2. Fresh input is priced at the input rate. Cached input is priced at the
   cached rate.
3. `billed_output = output_tokens + reasoning_tokens`, priced at the output
   rate. Reasoning is never free.
4. Each category is priced separately and only then summed. Adding the raw
   counts and applying one rate is wrong.
5. Tokens counted against the quota: `input_tokens + output_tokens + reasoning_tokens`.
   Cached tokens are already inside `input_tokens`.

```python
raw = (
    (input_tokens - cached_input_tokens) * rates.input_micros_per_million
    + cached_input_tokens * rates.cached_input_micros_per_million
    + (output_tokens + reasoning_tokens) * rates.output_micros_per_million
)
token_cost_micros = micros_from_raw(raw)
```

### 7.3 Worked example (pinned — must appear as a test and in EVIDENCE.md)

Input: `input_tokens=10,000`, `cached_input_tokens=4,000`, `output_tokens=2,000`, `reasoning_tokens=1,500`.

| Category | Tokens | Rate (micros / 1M) | Raw |
| --- | --- | --- | --- |
| Fresh input | 6,000 | 300,000 | 1,800,000,000 |
| Cached input | 4,000 | 75,000 | 300,000,000 |
| Output + reasoning | 3,500 | 2,500,000 | 8,750,000,000 |
| **Total** | | | **10,850,000,000** |

`10,850,000,000 / 1,000,000` = **10,850 micros = $0.010850**.
Quota tokens consumed: `10,000 + 2,000 + 1,500 = 13,500`.

Wrong answers the tests must reject:

- Cached tokens billed at the full input rate: 11,750 micros.
- Reasoning tokens left out: 7,100 micros.
- All 17,500 counted tokens priced at one input rate: 5,250 micros.

### 7.4 API-call cost

`api_call_cost_micros = api_calls × rates.api_call_micros`. Each successful
`POST /generate` counts as one API call.

### 7.5 Monthly total

```
total_micros = plan.base_fee_micros + api_call_cost_micros + token_cost_micros
```

---

## 8. Data model

### 8.1 Tables (PostgreSQL)

```sql
CREATE TABLE plans (
    code              TEXT PRIMARY KEY,               -- 'free' | 'pro'
    name              TEXT   NOT NULL,
    api_call_limit    BIGINT NOT NULL CHECK (api_call_limit >= 0),
    token_limit       BIGINT NOT NULL CHECK (token_limit >= 0),
    base_fee_micros   BIGINT NOT NULL CHECK (base_fee_micros >= 0)
);

CREATE TABLE tenants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    api_key_hash        TEXT NOT NULL UNIQUE,          -- sha256 hex of the API key
    plan_code           TEXT NOT NULL REFERENCES plans(code) DEFAULT 'free',
    billing_status      TEXT NOT NULL DEFAULT 'ok'
                        CHECK (billing_status IN ('ok', 'past_due')),
    stripe_customer_id  TEXT UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE subscriptions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                UUID NOT NULL REFERENCES tenants(id),
    stripe_subscription_id   TEXT NOT NULL UNIQUE,
    status                   TEXT NOT NULL,            -- mirrors Stripe: active, trialing, past_due, unpaid, canceled, incomplete, …
    plan_code                TEXT NOT NULL REFERENCES plans(code),
    current_period_start     TIMESTAMPTZ,
    current_period_end       TIMESTAMPTZ,
    last_event_created       BIGINT NOT NULL,          -- Stripe event.created (unix s) of last applied event
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_tenant_idx ON subscriptions (tenant_id);

CREATE TABLE usage_events (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    idempotency_key       TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 255),
    request_hash          TEXT NOT NULL,               -- sha256 of canonical request body
    api_calls             INTEGER NOT NULL DEFAULT 1 CHECK (api_calls >= 0),
    input_tokens          BIGINT  NOT NULL CHECK (input_tokens >= 0),
    cached_input_tokens   BIGINT  NOT NULL CHECK (cached_input_tokens >= 0 AND cached_input_tokens <= input_tokens),
    output_tokens         BIGINT  NOT NULL CHECK (output_tokens >= 0),
    reasoning_tokens      BIGINT  NOT NULL CHECK (reasoning_tokens >= 0),
    cost_micros           BIGINT  NOT NULL CHECK (cost_micros >= 0),   -- informational, per event
    response_status       SMALLINT NOT NULL,
    response_body         JSONB   NOT NULL,            -- replayed verbatim on retry
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX usage_events_tenant_time_idx ON usage_events (tenant_id, created_at);

CREATE TABLE stripe_events (
    event_id         TEXT PRIMARY KEY,                 -- evt_… ; the dedupe key
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
);
CREATE INDEX stripe_events_queue_idx ON stripe_events (status, next_attempt_at);

CREATE TABLE alerts (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,                         -- e.g. 'stripe_worker'
    ref         TEXT,                                  -- e.g. event_id
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Design notes:

- One `POST /generate` creates **one** usage event that carries both meters
  (`api_calls` and the token breakdown). This makes "exactly one usage event"
  directly countable with `SELECT count(*)`.
- `UNIQUE (tenant_id, idempotency_key)` is the database-level guarantee. Even a
  race that slips past application logic cannot create a second row.
- Idempotency keys are scoped per tenant. Two tenants may use the same key.
- `gen_random_uuid()` is built into PostgreSQL 13 and later.

### 8.2 Application types (Pydantic v2)

```python
from typing import Literal
from pydantic import BaseModel, Field, model_validator

class TokenUsage(BaseModel):
    input_tokens: int = Field(ge=0, le=1_000_000)
    cached_input_tokens: int = Field(ge=0, le=1_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000)
    reasoning_tokens: int = Field(ge=0, le=1_000_000)

    @model_validator(mode="after")
    def cached_within_input(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        return self

    @property
    def quota_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens

class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4_000)
    tokens: TokenUsage                       # simulated counts; no model is called

class CostBreakdown(BaseModel):
    api_call_micros: int
    fresh_input_micros: int
    cached_input_micros: int
    output_micros: int                      # output + reasoning
    total_micros: int
    total_usd: str                          # "0.012850", formatted from integers

class QuotaSnapshot(BaseModel):
    api_calls_used: int
    api_call_limit: int
    tokens_used: int
    token_limit: int

class GenerateResponse(BaseModel):
    event_id: str
    tenant_id: str
    output: str                             # simulated text
    tokens: TokenUsage
    cost: CostBreakdown
    quota: QuotaSnapshot                    # after this request
    created_at: str                         # ISO 8601 UTC

class MeterUsage(BaseModel):
    used: int
    limit: int
    remaining: int
    cost_micros: int

class TokenMeterUsage(MeterUsage):
    breakdown: dict[str, int]               # input, cached_input, fresh_input, output, reasoning

class UsageSummary(BaseModel):
    tenant_id: str
    plan: Literal["free", "pro"]
    billing_status: Literal["ok", "past_due"]
    period_start: str                       # first instant of the month, UTC
    period_end: str                         # first instant of next month, UTC
    api_calls: MeterUsage
    tokens: TokenMeterUsage
    base_fee_micros: int
    total_micros: int
    total_usd: str

class ErrorBody(BaseModel):
    error: str                              # machine code, e.g. "quota_exceeded"
    message: str                            # human sentence explaining why
    details: dict | None = None             # e.g. {"meter": "tokens", "used": 99_000, "requested": 1_500, "limit": 100_000}
    upgrade_url: str | None = None
```

### 8.3 Domain enums

| Name | Values |
| --- | --- |
| `PlanCode` | `free`, `pro` |
| `BillingStatus` | `ok`, `past_due` |
| `StripeEventStatus` | `pending`, `processed`, `skipped`, `failed` |
| `Meter` | `api_calls`, `tokens` |

---

## 9. Authentication and tenant isolation

- Each tenant has one API key, sent as `X-API-Key: <key>`. Keys are random
  32-byte tokens with a `mk_test_` prefix, shown once at seed time.
- Only `sha256(key)` is stored. Lookup is `WHERE api_key_hash = $1`.
- Missing or unknown key → `401 {"error": "unauthorized"}`.
- The `current_tenant` dependency resolves the tenant once per request. Every
  repository function for tenant-owned data takes `tenant_id` as a required
  parameter, and every query filters on it.
- No endpoint takes a tenant ID from the URL or body. A tenant can reach only
  its own data. A test proves tenant B cannot see or consume tenant A's usage
  or idempotency keys.
- `/webhooks/stripe` does not use API keys. Its authentication is the Stripe
  signature.

---

## 10. API contract

Base URL (local): `http://localhost:8000`. All bodies are JSON. All errors use
`ErrorBody`.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Liveness + DB check. |
| `GET` | `/plans` | none | Plans, limits, and rates from `pricing.toml`. |
| `POST` | `/generate` | API key + `Idempotency-Key` | The billable action. Meter, check quota, price. |
| `GET` | `/usage` | API key | Current-month rollup: used, limit, cost. |
| `POST` | `/billing/checkout` | API key | Creates a Stripe Checkout Session for Pro. |
| `GET` | `/billing/success` | none | Landing page after Checkout. |
| `GET` | `/billing/cancel` | none | Landing page after a cancelled Checkout. |
| `POST` | `/webhooks/stripe` | Stripe signature | Receives Stripe events. |

### 10.1 `POST /generate`

Request:

```http
POST /generate
X-API-Key: mk_test_…
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

Response `201 Created` (first time) or `200 OK` with header
`Idempotent-Replayed: true` (replay, identical body):

```json
{
  "event_id": "3d0f…",
  "tenant_id": "a1b2…",
  "output": "[simulated] response to: Summarize this ticket",
  "tokens": { "input_tokens": 10000, "cached_input_tokens": 4000, "output_tokens": 2000, "reasoning_tokens": 1500 },
  "cost": {
    "api_call_micros": 2000,
    "fresh_input_micros": 1800,
    "cached_input_micros": 300,
    "output_micros": 8750,
    "total_micros": 12850,
    "total_usd": "0.012850"
  },
  "quota": { "api_calls_used": 1, "api_call_limit": 1000, "tokens_used": 13500, "token_limit": 100000 },
  "created_at": "2026-10-04T12:00:00Z"
}
```

Status codes:

| Status | `error` | When |
| --- | --- | --- |
| `201` | — | New usage event created. |
| `200` | — | Replay of a stored response (same key, same body). |
| `400` | `idempotency_key_missing` | No `Idempotency-Key` header, or longer than 255 characters. |
| `401` | `unauthorized` | Missing or unknown API key. |
| `402` | `upgrade_required` | Free tenant would exceed a Free quota. |
| `402` | `payment_required` | Tenant `billing_status = past_due`. |
| `422` | `validation_error` | Body fails validation (negative tokens, cached > input, …). |
| `422` | `idempotency_key_reused` | Same key, different body. |
| `429` | `quota_exceeded` | Pro tenant would exceed a Pro quota. Includes `Retry-After`. |

### 10.2 `GET /usage`

```json
{
  "tenant_id": "a1b2…",
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

### 10.3 `POST /billing/checkout`

Response `200`: `{"checkout_url": "https://checkout.stripe.com/c/pay/cs_test_…", "session_id": "cs_test_…"}`.
Returns `409 already_pro` if the tenant already has an active Pro subscription.

### 10.4 `POST /webhooks/stripe`

| Status | Body | When |
| --- | --- | --- |
| `200` | `{"received": true}` | Valid signature, new event queued. |
| `200` | `{"received": true, "duplicate": true}` | Valid signature, event ID already stored. |
| `400` | `{"error": "invalid_signature"}` | Signature missing, invalid, or outside tolerance. Nothing is written. |
| `400` | `{"error": "invalid_payload"}` | Body is not a parseable Stripe event. |

---

## 11. Idempotency algorithm

`MeterService.record(tenant_id, request, idempotency_key)` runs in **one
transaction**:

1. `request_hash = sha256(canonical_json(request))`, where `canonical_json`
   uses sorted keys and no spaces.
2. `SELECT … FROM tenants WHERE id = $1 FOR UPDATE`. This serializes all
   metering for one tenant, so two concurrent requests cannot both pass the
   quota check. Other tenants are not blocked.
3. Look up `usage_events` by `(tenant_id, idempotency_key)`:
   - Found, same `request_hash` → return stored `response_body` with `200` and
     `Idempotent-Replayed: true`. No new row. Quota is not re-checked.
   - Found, different `request_hash` → `422 idempotency_key_reused`.
4. Not found → `QuotaService.check(...)` (section 12). On rejection, roll back
   and return `402`/`429`. Rejections are **not** stored, so the same key can
   succeed later, for example after an upgrade.
5. `PricingService.cost(tokens)` → cost breakdown.
6. `INSERT INTO usage_events …` with the response body. If the insert hits the
   unique constraint (should be impossible under the row lock), catch it and
   go to step 3.
7. Commit, return `201`.

Probe 1 check: send the same request twice with one key → two identical bodies,
statuses `201` then `200`, and
`SELECT count(*) FROM usage_events WHERE idempotency_key = '<key>'` returns `1`.

---

## 12. Quota rules (boundary honesty)

- Period: calendar month in UTC, `[first day 00:00, first day of next month 00:00)`.
- `used` = sum over usage events in the period (`api_calls`, and
  `input + output + reasoning` for tokens).
- A request is allowed **only if, for both meters, `used + requested ≤ limit`**.
  - With 999 of 1,000 calls used, the next call makes 1,000 → **allowed**.
  - With 1,000 of 1,000 used, the next call → **rejected**.
  - Token requests are all-or-nothing. A request that would cross the token
    limit is rejected whole, even if some tokens would fit.
- Rejection status:

| Situation | Status | `error` | Why this code |
| --- | --- | --- | --- |
| `billing_status = past_due` | `402` | `payment_required` | Payment is the blocker, not usage. |
| Free plan, quota would be exceeded | `402` | `upgrade_required` | Paying (upgrading to Pro) unblocks it now. Body includes `upgrade_url` = `POST /billing/checkout`. |
| Pro plan, quota would be exceeded | `429` | `quota_exceeded` | Nothing to buy; wait for reset. `Retry-After` = seconds until `period_end`. |

- The check order is: billing status, then API-call meter, then token meter.
  The first failing meter is named in `details.meter`.
- Example body:

```json
{
  "error": "upgrade_required",
  "message": "Free plan token quota reached: 99,000 of 100,000 tokens used this month and this request needs 1,500. Upgrade to Pro for 5,000,000 tokens per month.",
  "details": { "meter": "tokens", "used": 99000, "requested": 1500, "limit": 100000, "period_end": "2026-11-01T00:00:00Z" },
  "upgrade_url": "/billing/checkout"
}
```

The quota is also the **budget guard** for AI cost (shared requirement 7): no
request can push a tenant past its token allowance.

---

## 13. Usage rollup

`GET /usage` runs one aggregate query for the current period:

```sql
SELECT coalesce(sum(api_calls), 0),
       coalesce(sum(input_tokens), 0),
       coalesce(sum(cached_input_tokens), 0),
       coalesce(sum(output_tokens), 0),
       coalesce(sum(reasoning_tokens), 0)
FROM usage_events
WHERE tenant_id = $1 AND created_at >= $2 AND created_at < $3;
```

`PricingService` then prices the summed counts with the rules in section 7, and
adds the plan's base fee. Limits come from the tenant's **current** plan, so
after an upgrade `GET /usage` shows Pro limits right away (Probe 3).

---

## 14. Stripe integration (test mode)

### 14.1 Setup

1. Create a Stripe account (no card needed) and stay in test mode.
2. Create Product "Pro" with a recurring monthly Price of $29.00. Put the
   `price_…` ID in `STRIPE_PRO_PRICE_ID`.
3. `stripe login`, then `stripe listen --forward-to localhost:8000/webhooks/stripe`.
   Copy the printed `whsec_…` into `STRIPE_WEBHOOK_SECRET`.

### 14.2 Checkout

`BillingService.create_checkout(tenant)`:

- Creates or reuses a Stripe Customer, stores `stripe_customer_id` on the tenant.
- `stripe.checkout.Session.create(mode="subscription", line_items=[{price: STRIPE_PRO_PRICE_ID, quantity: 1}], customer=…, client_reference_id=str(tenant.id), metadata={"tenant_id": …}, subscription_data={"metadata": {"tenant_id": …}}, success_url=APP_BASE_URL + "/billing/success?session_id={CHECKOUT_SESSION_ID}", cancel_url=APP_BASE_URL + "/billing/cancel")`.
- Pays with test card `4242 4242 4242 4242`, any future expiry, any CVC.

The success page does **not** change the plan. Only a verified webhook does.

### 14.3 Webhook receiver (request path)

1. Read the **raw** request body (`await request.body()`). Do not parse JSON first.
2. `stripe.Webhook.construct_event(raw, request.headers["Stripe-Signature"], STRIPE_WEBHOOK_SECRET)`.
   Any `SignatureVerificationError` or missing header → `400 invalid_signature`,
   nothing written.
3. `INSERT INTO stripe_events (event_id, type, event_created, payload) … ON CONFLICT (event_id) DO NOTHING RETURNING event_id`.
   No row returned → duplicate → `200 {"duplicate": true}`.
4. Return `200` at once. Handled event types are queued; other types are stored
   with `status = 'skipped'`.

### 14.4 Event → state mapping (worker)

Tenant lookup order: `metadata.tenant_id` → `client_reference_id` → `stripe_customer_id`.

| Event | Action |
| --- | --- |
| `checkout.session.completed` (mode `subscription`, `payment_status = paid`) | Link `stripe_customer_id`; upsert `subscriptions` row (status `active`, plan `pro`); set tenant `plan_code = 'pro'`, `billing_status = 'ok'`. |
| `customer.subscription.updated` | Upsert subscription status and period. `active`/`trialing` → plan `pro`, status `ok`. `past_due`/`unpaid` → `billing_status = 'past_due'`. `canceled`/`incomplete_expired` → plan `free`, status `ok`. |
| `customer.subscription.deleted` | Subscription status `canceled`; tenant `plan_code = 'free'`, `billing_status = 'ok'`. |

Ordering guard: Stripe does not guarantee event order. If
`event.created < subscriptions.last_event_created`, the event is stale; mark it
`skipped` and change nothing. Otherwise apply it and store
`last_event_created = event.created`. Event application and the
`stripe_events.status = 'processed'` update share one transaction.

Unknown tenant (for example a bare `stripe trigger` fixture without metadata) →
`skipped` with `last_error = 'tenant_not_found'`. For a CLI-driven test use:

```bash
stripe trigger checkout.session.completed \
  --add checkout_session:client_reference_id=<tenant_uuid> \
  --add checkout_session:metadata.tenant_id=<tenant_uuid>
```

### 14.5 Probe mapping

- Probe 3: real Checkout with the test card → worker applies
  `checkout.session.completed` → `GET /usage` shows `"plan": "pro"` and Pro limits.
- Probe 4: `curl` a body with a fake `Stripe-Signature` → `400`, no row in
  `stripe_events`, tenant unchanged. `stripe events resend evt_…` twice →
  second delivery returns `duplicate: true`; the event has one row with
  `status = 'processed'`.

---

## 15. Background job (shared requirement 3)

`python -m app.worker`, run as its own Compose service (`worker`), same image as `api`.

- Loop every 1 s:
  ```sql
  SELECT * FROM stripe_events
  WHERE status = 'pending' AND next_attempt_at <= now()
  ORDER BY event_created
  LIMIT 10
  FOR UPDATE SKIP LOCKED;
  ```
- Success → `status = 'processed'`, `processed_at = now()`.
- Failure → `attempts += 1`, `last_error = <message without secrets>`,
  `next_attempt_at = now() + 2^attempts seconds` (2, 4, 8, 16, 32).
- After 5 failed attempts → `status = 'failed'`, insert an `alerts` row, and log
  at `ERROR`. This is the failure alert.
- `SKIP LOCKED` makes it safe to run more than one worker.

---

## 16. Validation and error handling (shared requirement 2)

- Pydantic validates all bodies and headers. FastAPI's default 422 is replaced
  by a handler that returns `ErrorBody` with `error = "validation_error"` and the
  failing fields in `details`.
- Domain errors (`QuotaExceeded`, `UpgradeRequired`, `PaymentRequired`,
  `IdempotencyKeyReused`, …) are Python exceptions raised in services and
  mapped to statuses in `app/api/errors.py`. Services never import FastAPI.
- Integer bounds (section 7.1) keep every product inside `BIGINT`.
- Bad input never produces `500`. An unexpected exception returns
  `500 {"error": "internal_error"}` and logs a stack trace with no secrets,
  API keys, or webhook payload signatures.

---

## 17. Configuration and secrets (shared requirement 6)

| Variable | Used by | Example placeholder |
| --- | --- | --- |
| `POSTGRES_USER` | Compose | `billing` |
| `POSTGRES_PASSWORD` | Compose | `change-me` |
| `POSTGRES_DB` | Compose | `billing` |
| `DATABASE_URL` | api, worker, tests (outside Docker) | `postgresql://billing:change-me@localhost:5433/billing` |
| `STRIPE_SECRET_KEY` | api | `sk_test_replace_me` |
| `STRIPE_WEBHOOK_SECRET` | api | `whsec_replace_me` |
| `STRIPE_PRO_PRICE_ID` | api | `price_replace_me` |
| `APP_BASE_URL` | api (Checkout redirects) | `http://localhost:8000` |
| `LOG_LEVEL` | api, worker | `INFO` |

- `.env` is in `.gitignore` before the first commit. `.env.example` holds only
  placeholders.
- The app refuses to start if `STRIPE_SECRET_KEY` does not begin with
  `sk_test_` or `rk_test_`. Live keys are rejected by design.
- Settings objects override `__repr__` so secrets never reach logs.

---

## 18. Testing strategy

Run with `docker compose run --rm api pytest`. Tests use a separate database
(`billing_test`) created and migrated by a session fixture, and truncate tables
between tests.

| Area | Test | Covers |
| --- | --- | --- |
| Idempotency | Same key + same body twice → one row, identical bodies, `201` then `200`. | Probe 1, G1 |
| Idempotency | Same key + different body → `422 idempotency_key_reused`. | G1 |
| Idempotency | 20 concurrent requests with one key → one row. | G1 |
| Idempotency | Same key used by two tenants → two rows, no leak. | Isolation |
| Quota | 999 used + 1 → `201`; 1,000 used + 1 → Free `402`, Pro `429` with `Retry-After`. | Probe 2, G2 |
| Quota | Token request crossing the limit → rejected whole, no row. | G2 |
| Quota | `past_due` tenant → `402 payment_required`. | G2 |
| Pricing | Worked example → 10,850 micros; the three wrong answers are not produced. | Probe 5, G3 |
| Pricing | Rollup prices summed counts once (no per-event rounding drift). | G3 |
| Pricing | Config loader rejects reasoning rate ≠ output rate. | G3 |
| Webhooks | Bad signature → `400`, no rows. | Probe 4 |
| Webhooks | Same signed event posted twice → one row, processed once. | Probe 4 |
| Webhooks | `checkout.session.completed` → tenant `pro`; `GET /usage` limits change. | Probe 3 |
| Webhooks | Stale `subscription.updated` after `deleted` → skipped. | G4 |
| Worker | Handler raises 5 times → `failed` + `alerts` row. | Shared req 3 |
| Validation | Negative tokens, cached > input, missing prompt → `422`, never `500`. | Shared req 2 |

Signed webhook payloads in tests are built with
`stripe.WebhookSignature._compute_signature` (or an HMAC-SHA256 helper with the
test secret), so tests never call Stripe.

### 18.1 Shared requirements map

| # | Shared requirement | Where it is met |
| --- | --- | --- |
| 1 | Layered architecture | Section 3 — `api/`, `services/`, `repositories/`. |
| 2 | Validation at the boundary | Section 16. |
| 3 | ≥1 background job | Section 15 — Stripe event worker. |
| 4 | Real persistence | Section 8 — Alembic migrations, indexes, tenant-scoped queries. |
| 5 | Idempotency | Section 11 (metering) and section 14.3 (webhooks). |
| 6 | Secrets clean | Section 17. |
| 7 | Cost tracked, with budget guard | Sections 7, 12, 13 — cost per event and per month, quota as guard. |

---

## 19. Evaluator manifest (`capstone.yaml`)

```yaml
run: docker compose up --build -d
seed: docker compose run --rm api python -m app.seed
test: docker compose run --rm api pytest
base_url: http://localhost:8000
endpoints:
  - GET /health
  - GET /plans
  - POST /generate
  - GET /usage
  - POST /billing/checkout
  - POST /webhooks/stripe
```

The `api` container runs `alembic upgrade head` before starting Uvicorn, so
`run` alone produces a migrated database. `seed` creates two demo tenants (one
Free, one Pro) plus a Free tenant pre-filled to 999 API calls for Probe 2, and
prints their API keys.
