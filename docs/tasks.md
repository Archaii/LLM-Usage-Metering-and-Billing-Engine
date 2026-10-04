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

- [ ] Create a new **public** GitHub repo, `flyrank-capstone-metering-billing`, holding only this project.
- [ ] Add `.gitignore` with `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/` **before the first commit**.
- [ ] Add `.env.example` with every variable from spec §17, placeholder values only.
- [ ] Add `requirements.txt` (direct dependencies only): `fastapi`, `uvicorn`, `psycopg[binary]`, `psycopg-pool`, `alembic`, `stripe`, `python-dotenv`, `pytest`, `httpx`.
- [ ] Add `Dockerfile` (Python 3.12 slim) and `compose.yaml` with services `db` (postgres:16, host port 5433, named volume, healthcheck), `api`, and `worker`.
- [ ] Start `BUILDLOG.md` with a dated first entry (where AI helped, where it was wrong, what you changed).
- [ ] Add a placeholder `EVIDENCE.md` with one heading per requirement from the Final self-check below.

**Gate 0:** `docker compose up db` starts a healthy Postgres, and `git log` shows `.gitignore` in the first commit.

---

## Phase 1 — Design (≈4–6 h)

- [ ] Write the one-page design doc `docs/design.md`: problem, data model, API surface, layer sketch, one explicit non-goal (spec §1.2).
- [ ] Fix the Pro plan numbers (spec §6) and copy them into the README plans table.
- [ ] Write `config/pricing.toml` with the pinned constants (spec §6).
- [ ] Document the idempotency strategy: per-tenant key, request hash, stored response, row lock (spec §11).
- [ ] Document the boundary rule `used + requested ≤ limit` and the 402 vs 429 table (spec §12).
- [ ] Document the webhook strategy: raw body, signature check, event-ID dedupe, worker, ordering guard (spec §14).
- [ ] Read the Phase 1 resources from the brief: Stripe idempotency article, Stripe usage-metering guide, "Floats don't work for storing cents".

**Gate 1:** `docs/design.md` is committed to the repository.

---

## Phase 2 — Core billing logic (≈9–13 h)

### 2.1 Persistence

- [ ] Configure Alembic (`alembic.ini`, `migrations/env.py` reading `DATABASE_URL`).
- [ ] Migration `0001_initial_schema`: tables `plans`, `tenants`, `subscriptions`, `usage_events`, `stripe_events`, `alerts`, with constraints and indexes from spec §8.1.
- [ ] `api` container runs `alembic upgrade head` before Uvicorn starts.
- [ ] `app/core/db.py`: connection pool and a `transaction()` context manager.
- [ ] `app/core/config.py`: env settings (secrets hidden from `repr`) and pricing loader that asserts reasoning rate = output rate and rejects non-`sk_test_` keys.
- [ ] Startup check: `plans` table matches `pricing.toml`, else refuse to start.

### 2.2 Tenants and auth

- [ ] `app/core/security.py`: generate `mk_test_` keys, `sha256` hashing.
- [ ] `app/seed.py`: upsert plans from `pricing.toml`; create tenants "Acme (Free)", "Globex (Pro)", and "Boundary (Free, 999 calls used)"; print their API keys once.
- [ ] `current_tenant` dependency: `X-API-Key` → tenant, else `401`.
- [ ] All tenant-owned repository functions take `tenant_id` and filter on it.

### 2.3 Metering

- [ ] Pydantic models from spec §8.2 in `app/api/schemas.py`.
- [ ] `canonical_json` + `request_hash` helper.
- [ ] `MeterService.record` with the algorithm in spec §11 (row lock, replay, key-reuse check, insert, stored response).
- [ ] `POST /generate`: requires `Idempotency-Key` (`400` if missing), returns `201`, or `200` + `Idempotent-Replayed: true` on replay.

### 2.4 Quotas

- [ ] Period helper: current calendar month bounds in UTC and seconds until reset.
- [ ] `QuotaService.check`: billing status → API-call meter → token meter; `used + requested ≤ limit`.
- [ ] Domain errors `PaymentRequired`, `UpgradeRequired`, `QuotaExceeded`, `IdempotencyKeyReused` mapped in `app/api/errors.py` to `402`/`402`/`429`/`422`.
- [ ] `429` responses include `Retry-After`; `402 upgrade_required` includes `upgrade_url`.
- [ ] Every rejection body names the meter, `used`, `requested`, `limit` in a human sentence (spec §12).

### 2.5 Validation and read path

- [ ] Custom handler: validation errors → `422` `ErrorBody`; unexpected errors → `500 internal_error`, logged without secrets.
- [ ] `GET /usage` returning counts and limits (cost fields can be `0` until Phase 4).
- [ ] `GET /plans` and `GET /health`.

### 2.6 Tests

- [ ] Same key + same body twice → one row, `201` then `200`, identical bodies.
- [ ] Same key + different body → `422 idempotency_key_reused`.
- [ ] 20 concurrent requests with one key → exactly one row.
- [ ] Same key from two tenants → two rows; tenant B cannot see tenant A's usage.
- [ ] 999 + 1 → allowed; 1,000 + 1 → Free `402`, Pro `429` with `Retry-After`.
- [ ] Token request crossing the limit → rejected whole, no row written.
- [ ] Bad input (negative tokens, cached > input, empty prompt) → `422`, never `500`.

**Gate 2:** the same request sent twice creates one event, and the boundary returns `429`/`402`. Paste both transcripts into `EVIDENCE.md`.

---

## Phase 3 — Stripe integration (≈8–12 h)

### 3.1 Stripe setup (test mode only)

- [ ] Create a Stripe account; confirm test mode; **never** switch to live mode.
- [ ] Create Product "Pro" with a $29.00/month recurring Price; put `price_…` in `.env` as `STRIPE_PRO_PRICE_ID`.
- [ ] Put `sk_test_…` in `.env` as `STRIPE_SECRET_KEY`. Confirm `git status` does not show `.env`.
- [ ] Install the Stripe CLI, run `stripe login`.
- [ ] Run `stripe listen --forward-to localhost:8000/webhooks/stripe`; put the `whsec_…` in `.env` as `STRIPE_WEBHOOK_SECRET`.

### 3.2 Checkout

- [ ] `BillingService.create_checkout`: create or reuse Stripe Customer, Checkout Session with `mode=subscription`, `client_reference_id`, and `metadata.tenant_id` (spec §14.2).
- [ ] `POST /billing/checkout` → `{checkout_url, session_id}`; `409 already_pro` if already Pro.
- [ ] `GET /billing/success` and `GET /billing/cancel` (static messages; they never change the plan).

### 3.3 Webhook receiver

- [ ] `POST /webhooks/stripe` reads the raw body and verifies with `stripe.Webhook.construct_event`; failure → `400`, nothing written.
- [ ] `INSERT … ON CONFLICT (event_id) DO NOTHING`; duplicate → `200 {"duplicate": true}`.
- [ ] Unhandled event types stored as `skipped`.

### 3.4 Background worker

- [ ] `app/worker.py` loop: claim pending events with `FOR UPDATE SKIP LOCKED`, apply, mark `processed`.
- [ ] Event → state mapping from spec §14.4 for `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`.
- [ ] Ordering guard: stale `event.created` → `skipped`.
- [ ] Retries with backoff 2/4/8/16/32 s; after 5 failures → `failed`, `alerts` row, `ERROR` log.
- [ ] `worker` service in `compose.yaml`.

### 3.5 Tests

- [ ] Forged signature → `400`, no `stripe_events` row, tenant unchanged.
- [ ] Same signed event posted twice → one row, applied once.
- [ ] `checkout.session.completed` → tenant `pro`; `GET /usage` shows Pro limits.
- [ ] `subscription.deleted` → tenant back to `free`; stale `subscription.updated` afterwards → skipped.
- [ ] `subscription.updated` with `past_due` → `/generate` returns `402 payment_required`.
- [ ] Handler that always fails → `failed` + `alerts` row after 5 attempts.

**Gate 3:** a test Checkout with card `4242 4242 4242 4242` flips a tenant Free → Pro via webhook. Paste the `stripe listen` log and the before/after `GET /usage` into `EVIDENCE.md`.

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

### Stripe integration
- [ ] Subscription checkout works end-to-end in Stripe test mode.
- [ ] Webhooks verify signatures, ignore duplicate events, and update tenant plan/status.

### Data model, tests and documentation
- [ ] Database includes tenants, plans, subscriptions, and usage events; customer data isolated per tenant.
- [ ] README + architecture diagram + setup instructions; required files present: `README.md`, `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, `.env.example`.

### Acceptance probes (brief §12, run against the live system)
- [ ] **Probe 1** — same billable request twice with one idempotency key → one usage event; second response mirrors the first.
- [ ] **Probe 2** — drive a tenant to its exact quota → boundary request follows the documented rule; the next returns `429`/`402` with a clear message.
- [ ] **Probe 3** — complete a Stripe test Checkout → webhook flips tenant Free → Pro; `GET /usage` shows the new limits.
- [ ] **Probe 4** — forged webhook → `400`, nothing changes; real event replayed twice → processed once.
- [ ] **Probe 5** — pinned pricing rules → cached-input and reasoning-token rules give the exact expected totals; `GET /usage` matches.

### Shared requirements (brief §12)
- [ ] Layered architecture — data / logic / HTTP separated.
- [ ] Validation at the boundary — bad input → clean 4xx, never a 500.
- [ ] ≥1 background job — Stripe event worker with retries + failure alert.
- [ ] Real persistence — migrations, right indexes, isolated tenants.
- [ ] Idempotency where it matters — metering and webhooks.
- [ ] Secrets clean — env only, never logged, no key in Git history.
- [ ] Cost tracked — per call, attributed to tenant, quota as budget guard.

### GitHub rules (brief §10)
- [ ] One dedicated public repo, public from day one.
- [ ] Each phase visible in commit history.
- [ ] No secret ever committed (`git log -p | grep -E "sk_test_|whsec_"` returns nothing real).
- [ ] A stranger can run it: one run command plus one seed step on a clean machine.

### Submit
- [ ] Paste the repository link into the portal submission form. Do not upload ZIP files or code.

---

## Stretch goals (only after every box above is ticked)

Pick one and finish it well rather than starting several.

- [ ] **Overage billing** — allow usage beyond limits for Pro, charge per extra unit, show projected month-end cost.
- [ ] **Invoices** — monthly statement per tenant with usage line items.
- [ ] **Usage alerts** — notify at 80% and 100% of each quota (once per threshold per period).
- [ ] **Proration** — correct charges for a mid-cycle upgrade.
- [ ] **Reconciliation job** — nightly comparison of local subscriptions against Stripe; alert on drift (catches missed webhooks).
- [ ] **Full test suite** — every scary case, deterministic, one command, run in CI (GitHub Actions free tier).
