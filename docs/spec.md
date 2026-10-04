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

Plan changes come from PayMongo (test mode only) through signed, deduplicated
webhooks. PayMongo holds payment truth; the database mirrors it.

> **Provider note.** The brief names Stripe, which does not onboard
> Philippines-registered businesses. This project uses PayMongo (test mode) as the
> payment provider instead. The architecture is unchanged: hosted checkout, a
> signed webhook, event-ID dedupe, a worker. One behavioural difference matters:
> PayMongo's Subscriptions API needs a customer-facing page to capture a card, and
> this project has no frontend (§1.2). Pro is therefore sold as a **prepaid
> 30-day period** through PayMongo Hosted Checkout (§14), not an auto-renewing
> subscription.

### 1.1 Goals

| # | Goal | Proof |
| --- | --- | --- |
| G1 | Exactly-once metering: one billable request plus one idempotency key creates one usage event, whatever the number of retries. | Probe 1 |
| G2 | Boundary honesty: the request that reaches the limit exactly is allowed; the next one is rejected with `402` or `429` and a clear message. | Probe 2 |
| G3 | Correct money math: integer micro-USD, cached input priced lower, reasoning priced as output. | Probe 5 |
| G4 | Safe payment sync: forged webhook gets `400` and changes nothing; a replayed event is processed once; Checkout flips a tenant Free to Pro. | Probes 3, 4 |

### 1.2 Non-goals

- Invoicing, proration, and overage billing (stretch goals only, see `tasks.md`).
- PayMongo live mode or real money. Test mode only, test card `4343 4343 4343 4345` (Visa, no 3-D Secure).
- Auto-renewing subscriptions and card capture. Pro is a prepaid 30-day period bought through hosted checkout; renewal means buying again after the period ends.
- Currency conversion. PayMongo charges Philippine pesos; the ledger is USD micros. The Pro checkout amount is a fixed pinned peso figure, not a live exchange rate (§6).
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
| Payments | PayMongo test mode, REST API through `httpx` | Hosted Checkout Sessions (`POST /v1/checkout_sessions`) + webhooks. No SDK. |
| Local webhooks | HTTPS tunnel (ngrok or cloudflared) | PayMongo cannot reach `localhost`; the tunnel gives it a public HTTPS URL. |
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
| Integration | `app/integrations/` | The only code that talks to PayMongo: REST client and webhook signature check. |
| Worker | `app/worker.py` | Applies queued payment events off the request path, with retries and alerts. Also expires lapsed Pro periods. |

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

Client ──POST /billing/checkout──► PayMongo Hosted Checkout (test mode) → checkout_url
          └─ store checkout_sessions row (session id → tenant)

PayMongo ──signed webhook──► POST /webhooks/paymongo
          │ verify Paymongo-Signature (forged → 400, no write)
          │ INSERT payment_events ON CONFLICT DO NOTHING (replay → ignored)
          └─ 200 fast
worker ──poll payment_events (FOR UPDATE SKIP LOCKED)──► grant 30-day Pro period, set tenant plan
          ├─ retry with backoff; after 5 failures → status=failed + alert row + ERROR log
          └─ expire lapsed Pro periods → tenant back to Free
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
│   ├── integrations/        # paymongo.py (REST client + webhook signature verification)
│   ├── services/            # meter.py, quota.py, pricing.py, billing.py, webhook.py
│   └── repositories/        # tenants.py, plans.py, usage.py, subscriptions.py, checkout_sessions.py, payment_events.py, alerts.py
├── scripts/
│   ├── register_webhook.py  # registers the tunnel URL with PayMongo, prints the signing secret once
│   └── send_test_webhook.py # builds and posts a correctly signed simulated event (Probe 4, manual tests)
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
base_fee_micros = 29_000_000    # $29.00 per 30-day Pro period, in the USD ledger

[checkout]
# PayMongo charges Philippine pesos in centavos (minimum 2,000 = PHP 20.00).
# A fixed pinned amount that approximates the $29.00 base fee; it is NOT a live FX rate.
currency = "PHP"
pro_amount_centavos = 165_000   # PHP 1,650.00
pro_period_days = 30            # length of one paid Pro period

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

The Pro checkout charges PHP 1,650.00 for one 30-day Pro period. The ledger
(`base_fee_micros`, `total_micros`) stays in USD micros; the peso amount only sets
what PayMongo collects.

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
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per hosted checkout the API created. Maps PayMongo's session ID to a tenant,
-- so the webhook never has to trust metadata to find the tenant.
CREATE TABLE checkout_sessions (
    id                TEXT PRIMARY KEY,                -- cs_… from PayMongo
    tenant_id         UUID NOT NULL REFERENCES tenants(id),
    plan_code         TEXT NOT NULL REFERENCES plans(code),
    amount_centavos   BIGINT NOT NULL CHECK (amount_centavos > 0),
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at           TIMESTAMPTZ
);
CREATE INDEX checkout_sessions_tenant_idx ON checkout_sessions (tenant_id);

-- One row per paid Pro period. Replaces the Stripe-era subscription mirror.
CREATE TABLE subscriptions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    provider_payment_id   TEXT NOT NULL UNIQUE,        -- pay_… ; the grant dedupe key
    checkout_session_id   TEXT REFERENCES checkout_sessions(id),
    plan_code             TEXT NOT NULL REFERENCES plans(code),
    status                TEXT NOT NULL CHECK (status IN ('active', 'expired')),
    current_period_start  TIMESTAMPTZ NOT NULL,
    current_period_end    TIMESTAMPTZ NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_tenant_idx ON subscriptions (tenant_id);
CREATE INDEX subscriptions_expiry_idx ON subscriptions (status, current_period_end);

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

CREATE TABLE payment_events (
    event_id         TEXT PRIMARY KEY,                 -- evt_… ; the dedupe key
    type             TEXT NOT NULL,                    -- e.g. checkout_session.payment.paid
    event_created    BIGINT NOT NULL,                  -- PayMongo created_at (unix s)
    payload          JSONB NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'processed', 'skipped', 'failed')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error       TEXT,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at     TIMESTAMPTZ
);
CREATE INDEX payment_events_queue_idx ON payment_events (status, next_attempt_at);

CREATE TABLE alerts (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,                         -- e.g. 'payment_worker'
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
- Schema history: migration `0001` created the first schema with Stripe-shaped
  `subscriptions` and `stripe_events` tables and `tenants.stripe_customer_id`.
  Migration `0002_paymongo_billing` moves to the PayMongo shape above: it drops
  `stripe_customer_id`, replaces `subscriptions`, adds `checkout_sessions`, and
  renames `stripe_events` to `payment_events`.
- `tenants.billing_status = 'past_due'` is a reserved state. The quota check
  honours it (`402 payment_required`, §12), but no PayMongo webhook sets it in the
  prepaid model. Operators or a later stretch goal set it.

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
| `PaymentEventStatus` | `pending`, `processed`, `skipped`, `failed` |
| `SubscriptionStatus` | `active`, `expired` |
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
- `/webhooks/paymongo` does not use API keys. Its authentication is the
  `Paymongo-Signature` header.

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
| `POST` | `/billing/checkout` | API key | Creates a PayMongo Hosted Checkout Session for one Pro period. |
| `GET` | `/billing/success` | none | Landing page after Checkout. |
| `GET` | `/billing/cancel` | none | Landing page after a cancelled Checkout. |
| `POST` | `/webhooks/paymongo` | `Paymongo-Signature` | Receives PayMongo events. |

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

Response `200`: `{"checkout_url": "https://checkout.paymongo.com/cs_…", "session_id": "cs_…", "amount_php": "1650.00", "period_days": 30}`.

| Status | `error` | When |
| --- | --- | --- |
| `200` | — | Session created and stored. |
| `401` | `unauthorized` | Missing or unknown API key. |
| `409` | `already_pro` | The tenant is already on Pro. Buy again after the period expires. |
| `502` | `billing_provider_error` | PayMongo rejected or did not answer the request. The message holds no secrets. |

### 10.4 `POST /webhooks/paymongo`

| Status | Body | When |
| --- | --- | --- |
| `200` | `{"received": true}` | Valid signature, new event queued. |
| `200` | `{"received": true, "duplicate": true}` | Valid signature, event ID already stored. |
| `400` | `{"error": "invalid_signature"}` | Signature missing, invalid, or outside tolerance. Nothing is written. |
| `400` | `{"error": "invalid_payload"}` | Body is not a parseable PayMongo event. |

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

## 14. PayMongo integration (test mode)

### 14.1 Setup

1. Create a PayMongo account and stay in **test mode**. Test keys start with `sk_test_` and `pk_test_`.
2. Put the secret key in `PAYMONGO_SECRET_KEY`. This project never needs the public key (hosted checkout).
3. PayMongo cannot reach `localhost`. Start an HTTPS tunnel to port 8000 (for example `ngrok http 8000`) and note the public `https://…` URL.
4. Register the webhook: `python -m scripts.register_webhook https://<tunnel-host>/webhooks/paymongo`. It calls `POST /v1/webhooks` with `events: ["checkout_session.payment.paid"]` and prints the returned signing secret once. Put it in `PAYMONGO_WEBHOOK_SECRET`. (The dashboard's Developers > Webhooks page does the same.)
5. Set `APP_BASE_URL` to the tunnel URL so checkout redirects work from the browser.

### 14.2 Checkout

`BillingService.create_checkout(tenant)`:

- Raises `AlreadyPro` (`409`) if the tenant's `plan_code` is already `pro`. This also covers seeded Pro tenants that have no subscription row. After a period expires the worker returns the tenant to Free, and it can buy again.
- Calls `POST https://api.paymongo.com/v1/checkout_sessions` with HTTP Basic auth (secret key as the username, empty password):

  ```json
  {"data": {"attributes": {
    "line_items": [{"currency": "PHP", "amount": 165000, "name": "Pro plan - 30 days", "quantity": 1}],
    "payment_method_types": ["card"],
    "description": "Usage Metering & Billing Engine - Pro",
    "success_url": "<APP_BASE_URL>/billing/success",
    "cancel_url": "<APP_BASE_URL>/billing/cancel",
    "metadata": {"tenant_id": "<uuid>"}
  }}}
  ```

- Reads `data.id` (`cs_…`) and `data.attributes.checkout_url` from the response and inserts a `checkout_sessions` row (`pending`) that maps the session to the tenant.
- Any non-2xx answer, timeout, or malformed response raises `BillingProviderError` (`502`). The error text never includes the key or the raw response body.
- Test card: `4343 4343 4343 4345` (Visa, no 3-D Secure), any future expiry, any CVC. `4120 0000 0000 0007` forces the 3-D Secure test prompt. `4111 1111 1111 1111` is a generic decline.

The success page does **not** change the plan. Only a verified webhook does.

### 14.3 Webhook receiver (request path)

1. Read the **raw** request body (`await request.body()`). Do not parse JSON first.
2. Verify the `Paymongo-Signature` header, which looks like `t=<unix>,te=<hex>,li=<hex>`:
   - Compute `HMAC-SHA256(PAYMONGO_WEBHOOK_SECRET, "<t>.<raw body>")` as lowercase hex.
   - Compare it with the `te` (test-mode) value using `hmac.compare_digest`. The `li` (live) value is never accepted: this app is test-mode only.
   - Reject if `|now − t| > 300` seconds (`WEBHOOK_TOLERANCE_SECONDS`, optional, default `300`).
   - A missing header, a malformed header, a bad signature, or a stale timestamp → `400 invalid_signature`, nothing written.
3. Parse the verified body. Expected envelope (confirm against the first real event; see §14.5):

   ```json
   {"data": {"id": "evt_…", "type": "event", "attributes": {
     "type": "checkout_session.payment.paid",
     "livemode": false,
     "created_at": 1791095841,
     "data": {"id": "cs_…", "type": "checkout_session", "attributes": {
       "payments": [{"id": "pay_…", "attributes": {"status": "paid", "amount": 165000, "currency": "PHP"}}],
       "metadata": {"tenant_id": "<uuid>"}
     }}
   }}}
   ```

   Missing `data.id`, `attributes.type`, or `attributes.created_at`, or `livemode: true` → `400 invalid_payload`, nothing written.
4. `INSERT INTO payment_events (event_id, type, event_created, payload, status) … ON CONFLICT (event_id) DO NOTHING RETURNING event_id`. No row returned → duplicate → `200 {"received": true, "duplicate": true}`.
5. Return `200` at once. Handled event types are stored `pending`; every other type is stored `skipped`.

### 14.4 Event → state mapping (worker)

Handled event type: `checkout_session.payment.paid`. Steps, in one transaction with the `payment_events.status = 'processed'` update:

1. **Tenant lookup:** `checkout_sessions.id = data.id` → `tenant_id`. Fallback: `metadata.tenant_id`. Neither → `skipped`, `last_error = 'tenant_not_found'`.
2. **Paid payment:** the first entry of `payments[]` whose `attributes.status` is `paid`. None → `skipped`, `last_error = 'no_paid_payment'`.
3. **Amount guard:** `payment.attributes.amount` must equal `checkout_sessions.amount_centavos`. Otherwise → `skipped`, `last_error = 'amount_mismatch'`. A cheaper or altered session can never grant Pro.
4. **Grant, once per payment:** `INSERT INTO subscriptions (…, provider_payment_id) … ON CONFLICT (provider_payment_id) DO NOTHING`. No row inserted → `skipped`, `last_error = 'duplicate_payment'`. This is a second dedupe layer: two different events about the same payment grant Pro once.
5. **Apply:** `current_period_start = event time`, `current_period_end = start + pro_period_days`, `status = 'active'`. Set tenant `plan_code = 'pro'`, `billing_status = 'ok'`. Mark the `checkout_sessions` row `paid` with `paid_at`.

There is no ordering guard. Grants are independent, so a late or reordered event cannot undo anything, and `provider_payment_id` already blocks double grants.

**Expiry (also the worker):** `UPDATE subscriptions SET status = 'expired' WHERE status = 'active' AND current_period_end <= now()`. For each affected tenant with no remaining `active` subscription: `plan_code = 'free'`. After expiry the tenant can buy another period.

### 14.5 Local testing and probe mapping

- **Real events (Probe 3, Gate 3):** tunnel + registered webhook, then `POST /billing/checkout`, open `checkout_url`, pay with the test card. The first real event's payload shape must be compared with §14.3 and any difference fixed in this spec and the code in the same commit. Save the event's raw body and headers (tunnel inspector, or the PayMongo dashboard) for `EVIDENCE.md`.
- **Signed simulations (tests and Probe 4):** `scripts/send_test_webhook.py` builds a correctly signed event with the real secret and posts it to a URL, so forged and replayed deliveries are repeatable without the dashboard. It simulates PayMongo; it is not a PayMongo-originated event, and `EVIDENCE.md` says so.
- **Probe 3:** real Checkout with the test card → worker applies `checkout_session.payment.paid` → `GET /usage` shows `"plan": "pro"` and Pro limits.
- **Probe 4:** `curl` a body with a fake `Paymongo-Signature` → `400`, no row in `payment_events`, tenant unchanged. The same signed event delivered twice → second delivery returns `duplicate: true`; the event has one row with `status = 'processed'` and `subscriptions` has one row.

---

## 15. Background job (shared requirement 3)

`python -m app.worker`, run as its own Compose service (`worker`), same image as `api`.

- Loop every 1 s:
  ```sql
  SELECT * FROM payment_events
  WHERE status = 'pending' AND next_attempt_at <= now()
  ORDER BY event_created
  LIMIT 10
  FOR UPDATE SKIP LOCKED;
  ```
  Each event runs inside a savepoint, so one failing event does not roll back the others in the batch.
- Success → `status = 'processed'` (or `skipped` with a reason from §14.4), `processed_at = now()`.
- Failure → `attempts += 1`, `last_error = <message without secrets>`,
  `next_attempt_at = now() + 2^attempts seconds` (2, 4, 8, 16 s: five attempts, four waits).
- The fifth failed attempt → `status = 'failed'`, insert an `alerts` row, and log
  at `ERROR`. This is the failure alert.
- The queue and the expiry step compare against the **database** clock (`now()`), not the worker host's clock, so clock skew between the two can never strand an event.
- Each loop also runs the expiry step from §14.4.
- `SKIP LOCKED` makes it safe to run more than one worker.

---

## 16. Validation and error handling (shared requirement 2)

- Pydantic validates all bodies and headers. FastAPI's default 422 is replaced
  by a handler that returns `ErrorBody` with `error = "validation_error"` and the
  failing fields in `details`.
- Domain errors (`QuotaExceeded`, `UpgradeRequired`, `PaymentRequired`,
  `IdempotencyKeyReused`, `AlreadyPro`, `BillingProviderError`, …) are Python exceptions raised in services and
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
| `PAYMONGO_SECRET_KEY` | api | `sk_test_replace_me` |
| `PAYMONGO_WEBHOOK_SECRET` | api | `whsk_replace_me` |
| `APP_BASE_URL` | api (Checkout redirects) | `http://localhost:8000` |
| `WEBHOOK_TOLERANCE_SECONDS` | api (optional) | `300` |
| `LOG_LEVEL` | api, worker | `INFO` |

- `.env` is in `.gitignore` before the first commit. `.env.example` holds only
  placeholders.
- The app refuses to start if `PAYMONGO_SECRET_KEY` does not begin with
  `sk_test_`. Live keys are rejected by design.
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
| Webhooks | Forged, missing, malformed, or stale signature → `400`, no rows. | Probe 4 |
| Webhooks | Same signed event posted twice → one row, processed once. | Probe 4 |
| Webhooks | `checkout_session.payment.paid` → tenant `pro`; `GET /usage` limits change. | Probe 3 |
| Webhooks | Two different events for the same payment → one subscription row. | G4 |
| Webhooks | Amount mismatch, unknown tenant, unhandled type → skipped, tenant unchanged. | G4 |
| Billing | `POST /billing/checkout` (fake PayMongo client) → URL returned, session stored; second call while Pro → `409`. | Probe 3 |
| Billing | Provider error → `502 billing_provider_error`, no stored session, no secret in body. | Shared req 2 |
| Worker | Handler raises 5 times → `failed` + `alerts` row. | Shared req 3 |
| Worker | Lapsed Pro period → tenant back to `free`. | G4 |
| Validation | Negative tokens, cached > input, missing prompt → `422`, never `500`. | Shared req 2 |

Signed webhook payloads in tests use the same HMAC-SHA256 helper as
`scripts/send_test_webhook.py` with the test secret. The PayMongo client is
replaced by a fake, so tests never call PayMongo.

### 18.1 Shared requirements map

| # | Shared requirement | Where it is met |
| --- | --- | --- |
| 1 | Layered architecture | Section 3 — `api/`, `services/`, `repositories/`. |
| 2 | Validation at the boundary | Section 16. |
| 3 | ≥1 background job | Section 15 — payment event worker and Pro expiry. |
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
  - POST /webhooks/paymongo
```

The `api` container runs `alembic upgrade head` before starting Uvicorn, so
`run` alone produces a migrated database. `seed` creates two demo tenants (one
Free, one Pro) plus a Free tenant pre-filled to 999 API calls for Probe 2, and
prints their API keys.
