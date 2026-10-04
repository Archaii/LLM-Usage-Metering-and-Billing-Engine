# Roadmap — Usage Metering & Billing Engine

Actionable checklist, in build order. Each phase ends with a **gate**: do not
start the next phase until the gate passes. Section numbers such as "spec §11"
point to [spec.md](spec.md). Effort estimates come from the brief and are
orientation only.

Rule for every phase: commit small, with messages that say what changed, so each
phase is visible in the Git history. Add proofs to `EVIDENCE.md` as you go, not
at the end.

---

## Phase 0 — Repository setup

- [x] Create a new **public** GitHub repo holding only this project (`Archaii/LLM-Usage-Metering-and-Billing-Engine`).
- [x] Add `.gitignore` with `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/` **before the first commit**.
- [x] Add `.env.example` with every variable from spec §17, placeholder values only.
- [x] Add `requirements.txt` (direct dependencies only): `fastapi`, `uvicorn`, `psycopg[binary]`, `psycopg-pool`, `alembic`, `python-dotenv`, `pytest`, `httpx`.
- [x] Add `Dockerfile` (Python 3.12 slim) and `compose.yaml` with services `db` (postgres:16, host port 5433, named volume, healthcheck), `api`, and `worker`.
- [x] Start `BUILDLOG.md` with a dated first entry (where AI helped, where it was wrong, what you changed).
- [x] Add a placeholder `EVIDENCE.md` with one heading per requirement from the Final self-check below.

**Gate 0:** `docker compose up db` starts a healthy Postgres, and `git log` shows `.gitignore` in the first commit.

---

## Phase 1 — Design (≈4–6 h)

- [x] Write the one-page design doc `docs/design.md`: problem, data model, API surface, layer sketch, one explicit non-goal (spec §1.2).
- [x] Fix the Pro plan numbers (spec §6) and copy them into the README plans table.
- [x] Write `config/pricing.toml` with the pinned constants (spec §6).
- [x] Document the idempotency strategy: per-tenant key, request hash, stored response, row lock (spec §11).
- [x] Document the boundary rule `used + requested ≤ limit` and the 402 vs 429 table (spec §12).
- [x] Document the webhook strategy: raw body, signature check, event-ID dedupe, worker, ordering guard (spec §14).
- [ ] Read the Phase 1 resources from the brief: Stripe idempotency article, Stripe usage-metering guide (concepts carry over to PayMongo), PayMongo webhooks and Checkout Session docs, "Floats don't work for storing cents".

**Gate 1:** `docs/design.md` is committed to the repository.

---

## Phase 2 — Core billing logic (≈9–13 h)

### 2.1 Persistence

- [x] Configure Alembic (`alembic.ini`, `migrations/env.py` reading `DATABASE_URL`).
- [x] Migration `0001_initial_schema`: tables `plans`, `tenants`, `subscriptions`, `usage_events`, `stripe_events`, `alerts`, with constraints and indexes from spec §8.1.
- [x] `api` container runs `alembic upgrade head` before Uvicorn starts.
- [x] `app/core/db.py`: connection pool and a `transaction()` context manager.
- [x] `app/core/config.py`: env settings (secrets hidden from `repr`) and pricing loader that asserts reasoning rate = output rate and rejects non-`sk_test_` keys.
- [x] Startup check: `plans` table matches `pricing.toml`, else refuse to start.

### 2.2 Tenants and auth

- [x] `app/core/security.py`: generate `mk_test_` keys, `sha256` hashing.
- [x] `app/seed.py`: upsert plans from `pricing.toml`; create tenants "Acme (Free)", "Globex (Pro)", and "Boundary (Free, 999 calls used)"; print their API keys once.
- [x] `current_tenant` dependency: `X-API-Key` → tenant, else `401`.
- [x] All tenant-owned repository functions take `tenant_id` and filter on it.

### 2.3 Metering

- [x] Pydantic models from spec §8.2 in `app/api/schemas.py`.
- [x] `canonical_json` + `request_hash` helper.
- [x] `MeterService.record` with the algorithm in spec §11 (row lock, replay, key-reuse check, insert, stored response).
- [x] `POST /generate`: requires `Idempotency-Key` (`400` if missing), returns `201`, or `200` + `Idempotent-Replayed: true` on replay.

### 2.4 Quotas

- [x] Period helper: current calendar month bounds in UTC and seconds until reset.
- [x] `QuotaService.check`: billing status → API-call meter → token meter; `used + requested ≤ limit`.
- [x] Domain errors `PaymentRequired`, `UpgradeRequired`, `QuotaExceeded`, `IdempotencyKeyReused` mapped in `app/api/errors.py` to `402`/`402`/`429`/`422`.
- [x] `429` responses include `Retry-After`; `402 upgrade_required` includes `upgrade_url`.
- [x] Every rejection body names the meter, `used`, `requested`, `limit` in a human sentence (spec §12).

### 2.5 Validation and read path

- [x] Custom handler: validation errors → `422` `ErrorBody`; unexpected errors → `500 internal_error`, logged without secrets.
- [x] `GET /usage` returning counts and limits (cost fields can be `0` until Phase 4).
- [x] `GET /plans` and `GET /health`.

### 2.6 Tests

- [x] Same key + same body twice → one row, `201` then `200`, identical bodies.
- [x] Same key + different body → `422 idempotency_key_reused`.
- [x] 20 concurrent requests with one key → exactly one row.
- [x] Same key from two tenants → two rows; tenant B cannot see tenant A's usage.
- [x] 999 + 1 → allowed; 1,000 + 1 → Free `402`, Pro `429` with `Retry-After`.
- [x] Token request crossing the limit → rejected whole, no row written.
- [x] Bad input (negative tokens, cached > input, empty prompt) → `422`, never `500`.

**Gate 2:** the same request sent twice creates one event, and the boundary returns `429`/`402`. Paste both transcripts into `EVIDENCE.md`.

---

## Phase 3 — PayMongo integration (≈8–12 h)

> Provider change: Stripe is not available to Philippines-registered businesses, so
> this phase uses PayMongo (test mode). See spec §1 (provider note) and §14.

### 3.0 Migration to the PayMongo shape

- [x] Migration `0002_paymongo_billing`: drop `tenants.stripe_customer_id`; replace `subscriptions`; add `checkout_sessions`; rename `stripe_events` → `payment_events` and its index (spec §8.1).
- [x] Update `app/core/models.py`, `tenants` repository, and `tests/conftest.py` truncate list for the new tables.
- [x] Config: `PAYMONGO_SECRET_KEY` (must start `sk_test_`), `PAYMONGO_WEBHOOK_SECRET`, optional `WEBHOOK_TOLERANCE_SECONDS`; add the `[checkout]` section to `config/pricing.toml` and the loader; remove the `stripe` dependency.

### 3.1 PayMongo setup (test mode only) — you do this part

- [ ] Create a PayMongo account; stay in **test mode**; never use live keys.
- [ ] Put `sk_test_…` in `.env` as `PAYMONGO_SECRET_KEY`. Confirm `git status` does not show `.env`.
- [ ] Install a tunnel (ngrok or cloudflared) and run it against port 8000; copy the public HTTPS URL into `.env` as `APP_BASE_URL`.
- [ ] Run `python -m scripts.register_webhook https://<tunnel-host>/webhooks/paymongo`; put the printed secret in `.env` as `PAYMONGO_WEBHOOK_SECRET`.
- [ ] Ask the mentor to confirm PayMongo and the prepaid-30-day Pro model are acceptable in place of Stripe subscriptions.

### 3.2 Checkout

- [x] `app/integrations/paymongo.py`: `httpx` client for `POST /v1/checkout_sessions` (Basic auth), timeouts, error mapping with no secrets in messages.
- [x] `BillingService.create_checkout`: `409 already_pro` guard, create session, store `checkout_sessions` row (spec §14.2).
- [x] `POST /billing/checkout` → `{checkout_url, session_id, amount_php, period_days}`; `502 billing_provider_error` on provider failure.
- [x] `GET /billing/success` and `GET /billing/cancel` (static messages; they never change the plan).

### 3.3 Webhook receiver

- [x] `verify_signature` in `app/integrations/paymongo.py`: parse `t,te,li`, HMAC-SHA256 over `"{t}.{raw body}"`, constant-time compare to `te`, timestamp tolerance (spec §14.3).
- [x] `POST /webhooks/paymongo` reads the raw body, verifies, parses the envelope; failure → `400`, nothing written.
- [x] `INSERT … ON CONFLICT (event_id) DO NOTHING`; duplicate → `200 {"duplicate": true}`.
- [x] Unhandled event types stored as `skipped`.

### 3.4 Background worker

- [x] `app/worker.py` loop: claim pending events with `FOR UPDATE SKIP LOCKED`, apply each in a savepoint, mark `processed`.
- [x] Event → state mapping from spec §14.4 for `checkout_session.payment.paid`: tenant lookup, paid-payment check, amount guard, once-per-payment grant, tenant → Pro.
- [x] Expiry step: lapsed Pro periods → `expired`, tenant → Free.
- [x] Retries with backoff 2/4/8/16 s; the fifth failure → `failed`, `alerts` row, `ERROR` log.
- [x] `worker` service in `compose.yaml` works with the new module.

### 3.5 Scripts

- [ ] `scripts/register_webhook.py`: `POST /v1/webhooks` with the tunnel URL; prints the signing secret once; never logs the key. (Written; not yet run against PayMongo, needs your key.)
- [x] `scripts/send_test_webhook.py`: builds a correctly signed simulated `checkout_session.payment.paid` event and posts it; supports `--bad-signature`.

### 3.6 Tests

- [x] Forged, missing, malformed, and stale signatures → `400`, no `payment_events` row, tenant unchanged.
- [x] Same signed event posted twice → one row, applied once.
- [x] `checkout_session.payment.paid` → tenant `pro`; `GET /usage` shows Pro limits.
- [x] Two different events for the same payment → one `subscriptions` row.
- [x] Amount mismatch, unknown tenant, unhandled event type → `skipped`, tenant unchanged.
- [x] `POST /billing/checkout` with a fake provider client → session stored; second call while Pro → `409`; provider error → `502`, nothing stored.
- [x] Handler that always fails → `failed` + `alerts` row after 5 attempts.
- [x] Lapsed Pro period → tenant back to `free`.

**Gate 3:** a real test Checkout with card `4343 4343 4343 4345` flips a tenant Free → Pro via webhook. Paste the raw webhook delivery (from the tunnel inspector or PayMongo dashboard) and the before/after `GET /usage` into `EVIDENCE.md`. Compare the real payload against spec §14.3 and fix any difference in spec and code together.

---

## Phase 4 — Cost and finalization (≈7–10 h)

### 4.1 Cost

- [ ] `app/core/money.py`: `micros_from_raw` (round half up) and `format_usd` (no floats).
- [ ] `PricingService.cost(tokens)` per spec §7.2, plus API-call cost (spec §7.4).
- [ ] `POST /generate` response includes `cost` breakdown; event stores `cost_micros`.
- [ ] `GET /usage` prices summed counts once and adds the base fee (spec §7.5, §13).

### 4.2 Cost tests

- [ ] Worked example (spec §7.3) → exactly 10,850 micros; a full `/generate` → 12,850 micros.
- [ ] The three wrong answers (11,750 / 7,100 / 5,250) are asserted not to occur.
- [ ] Many small events: rollup equals pricing the summed counts (no per-event rounding drift).
- [ ] Config with reasoning rate ≠ output rate → loader raises.

### 4.3 Documentation and submission pack

- [ ] `README.md`: what it does, architecture diagram (ASCII), exact run + seed steps, plans table, honest "Limitations" section.
- [ ] `capstone.yaml` with `run`, `seed`, `test`, `base_url`, endpoints (spec §19).
- [ ] `EVIDENCE.md`: one proof per box in the Final self-check (test name + output, curl transcript, or log line).
- [ ] `BUILDLOG.md` up to date and honest.
- [ ] `.env.example` matches every variable the code reads.
- [ ] Fresh-clone test: clone into a new folder, `cp .env.example .env`, run, seed, probe. Fix anything that needs an undocumented step.

**Gate 4:** `GET /usage` numbers match the pinned pricing constants. Paste the request, response, and hand calculation into `EVIDENCE.md`.

---

## Final self-check (brief §6 — every box needs a proof in EVIDENCE.md)

### Metering
- [ ] A billable action creates exactly one usage event, even under retries — deduplicated by idempotency key.
- [ ] Proof in `EVIDENCE.md` that double-counting cannot happen: a test output or a transcript of the same request sent twice.

### Quotas
- [ ] Usage is checked against the tenant's plan; requests over the limit are rejected.
- [ ] Responses carry the correct status codes (`429` / `402`) and a message explaining why.

### Cost calculation
- [ ] Monthly usage rolls up into a cost figure per tenant.
- [ ] AI token pricing handles cached input tokens, reasoning tokens, and output pricing correctly.
- [ ] Pricing constants are pinned in config, with proof of correct totals in `EVIDENCE.md`.

### Payment integration (PayMongo)
- [ ] Checkout works end-to-end in PayMongo test mode (prepaid 30-day Pro period).
- [ ] Webhooks verify signatures, ignore duplicate events, and update tenant plan; lapsed periods expire.

### Data model, tests and documentation
- [ ] Database includes tenants, plans, subscriptions, and usage events; customer data isolated per tenant.
- [ ] README + architecture diagram + setup instructions; required files present: `README.md`, `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, `.env.example`.

### Acceptance probes (brief §12, run against the live system)
- [ ] **Probe 1** — same billable request twice with one idempotency key → one usage event; second response mirrors the first.
- [ ] **Probe 2** — drive a tenant to its exact quota → boundary request follows the documented rule; the next returns `429`/`402` with a clear message.
- [ ] **Probe 3** — complete a PayMongo test Checkout → webhook flips tenant Free → Pro; `GET /usage` shows the new limits.
- [ ] **Probe 4** — forged webhook → `400`, nothing changes; real event replayed twice → processed once.
- [ ] **Probe 5** — pinned pricing rules → cached-input and reasoning-token rules give the exact expected totals; `GET /usage` matches.

### Shared requirements (brief §12)
- [ ] Layered architecture — data / logic / HTTP separated.
- [ ] Validation at the boundary — bad input → clean 4xx, never a 500.
- [ ] ≥1 background job — payment event worker with retries + failure alert.
- [ ] Real persistence — migrations, right indexes, isolated tenants.
- [ ] Idempotency where it matters — metering and webhooks.
- [ ] Secrets clean — env only, never logged, no key in Git history.
- [ ] Cost tracked — per call, attributed to tenant, quota as budget guard.

### GitHub rules (brief §10)
- [ ] One dedicated public repo, public from day one.
- [ ] Each phase visible in commit history.
- [ ] No secret ever committed (`git log -p | grep -E "sk_test_|whsk_"` returns nothing real).
- [ ] A stranger can run it: one run command plus one seed step on a clean machine.

### Submit
- [ ] Paste the repository link into the portal submission form. Do not upload ZIP files or code.

---

## Stretch goals (only after every box above is ticked)

Pick one and finish it well rather than starting several.

- [ ] **Overage billing** — allow usage beyond limits for Pro, charge per extra unit, show projected month-end cost.
- [ ] **Invoices** — monthly statement per tenant with usage line items.
- [ ] **Usage alerts** — notify at 80% and 100% of each quota (once per threshold per period).
- [ ] **PayMongo Subscriptions API** — auto-renewing Pro (plans, customers, subscriptions) with a small card-capture page; maps statuses `active`/`incomplete`/`past_due`/`unpaid` onto `billing_status`.
- [ ] **Proration** — correct charges for a mid-cycle upgrade.
- [ ] **Reconciliation job** — nightly comparison of local subscriptions against PayMongo payments; alert on drift (catches missed webhooks).
- [ ] **Full test suite** — every scary case, deterministic, one command, run in CI (GitHub Actions free tier).
