# Handoff — Usage Metering & Billing Engine

Written 2026-10-04 so a fresh session can continue without the old chat. Read this first, then
`docs/spec.md` (the contract), `docs/tasks.md` (the roadmap), and the tail of `BUILDLOG.md`.

- Repo: https://github.com/Archaii/LLM-Usage-Metering-and-Billing-Engine (public, branch `main`, remote `origin`)
- Local folder: `C:\Users\Andaya\Documents\Documents\FlyRank AI Internship\LLM_Usage_Metering_Billing_Engine`
- State: **Phases 0, 1, 2 and 3 are done and pushed. Phase 4 has not started.** 81 tests pass.

## 1. What this is

FlyRank internship capstone (Backend track): a FastAPI + PostgreSQL service that meters usage exactly once,
enforces monthly quotas with honest `402`/`429`, prices AI tokens in integer micro-USD, and keeps tenant plans in
sync with a payment provider through signed, deduplicated webhooks. The original brief is
`docs/Usage Metering Billing Engine Live Capstone.pdf`.

## 2. Decisions made (and why)

| Decision | Why |
| --- | --- |
| **PayMongo (test mode) instead of Stripe** | Stripe does not onboard Philippines-registered businesses. Mentor approved. |
| **Webhook path `POST /webhooks/paymongo`** | Replaces `/webhooks/stripe`. Mentor approved. |
| **Pro = prepaid 30-day period via Hosted Checkout, not an auto-renewing subscription** | PayMongo Subscriptions API needs a customer-facing page to capture a card; this project has no frontend. The Subscriptions API is a stretch goal. |
| **Renewals stack** | Paying again while Pro starts the new 30 days when the running period ends (no gap, no wasted time). A renewal after a lapse starts at the payment time. The old `409 already_pro` is gone. |
| **Fixed PHP 1,650.00 per period** | Approximates the $29.00 base fee; not a live FX rate. The ledger stays in USD micros. Mentor approved. Pinned in `config/pricing.toml` `[checkout]`. |
| **A signed webhook is not proof of payment** | Real events carry a pre-settlement snapshot (`payments: []`, intent `processing`, envelope `created_at: null`). The worker calls `GET /v1/checkout_sessions/{id}` and grants Pro only for a paid payment of the right amount; unsettled sessions are retried. |
| **Grant dedupe key = PayMongo payment ID** (`subscriptions.provider_payment_id` UNIQUE) | Second dedupe layer behind `payment_events.event_id`. |
| **Stored `checkout_sessions` row decides the tenant**, metadata is only a fallback | A spoofed metadata value cannot redirect a payment. |
| **Worker and queue use the database clock** (`now()`), not the host clock | The Docker Postgres clock ran ahead of the host and stranded events. Tests pass an explicit `now`. |
| **`api` and `worker` share one image** (`metering-engine:dev`) | The worker once ran stale code after a rebuild of only `api`. |
| **Empty `plans` table is filled from `pricing.toml` at startup**; a non-empty table that differs refuses to start | `docker compose up` runs migrations before `seed`. |
| **`billing_status = 'past_due'` is a reserved state** | The quota check honours it (`402 payment_required`), but no webhook sets it in the prepaid model. |
| Replay of a metering request returns the same JSON, not the same bytes | The stored body is `JSONB`, which does not keep key order. |
| Seed re-runs rotate the demo API keys | Only hashes are stored. |

## 3. What exists

```
app/api/          routes (generate, usage, plans, health, billing, webhooks), schemas, errors, deps
app/services/     meter, quota, pricing (ZERO-COST PLACEHOLDER), billing, webhook, plan_sync
app/repositories/ tenants, plans, usage, subscriptions, checkout_sessions, payment_events, alerts
app/integrations/ paymongo.py (REST client + Paymongo-Signature check)
app/worker.py     payment-event queue (retries 2/4/8/16 s, 5 attempts, alert row) + Pro expiry
app/core/         config, db, security, period, errors, models
migrations/       0001 initial schema, 0002 PayMongo shape
scripts/          register_webhook, send_test_webhook, inspect_checkout_session
tests/            81 tests, incl. a real captured PayMongo delivery in tests/fixtures/
```

Endpoints: `GET /health`, `GET /plans`, `POST /generate`, `GET /usage`, `POST /billing/checkout`,
`GET /billing/success`, `GET /billing/cancel`, `POST /webhooks/paymongo`.

Proven so far (see `EVIDENCE.md`): Probe 1 (idempotency), Probe 2 (quota boundary, `402`), Probe 3 (a real
PayMongo test checkout flipped Acme Free to Pro). Probe 4 is only partly proven (see section 6).

## 4. Running it

```powershell
docker compose up -d                                   # db (5433), api (8000), worker; api runs migrations
docker compose run --rm api python -m app.seed         # demo tenants; prints API keys once (re-run rotates them)
.venv\Scripts\python.exe -m pytest -q                  # needs the db container up; uses database billing_test
docker compose run --rm api pytest -q                  # same suite inside Docker
docker compose build api; docker compose up -d --force-recreate api worker   # after code changes
```

- `.env` is gitignored and holds the real `PAYMONGO_SECRET_KEY` (`sk_test_...`), `PAYMONGO_WEBHOOK_SECRET`, and
  `APP_BASE_URL` (the ngrok URL). **Never print or paste these.** `.env.example` holds placeholders only.
- Webhooks need an HTTPS tunnel: `ngrok http 8000`. The free ngrok URL can change; if it does, update
  `APP_BASE_URL` and re-run `python -m scripts.register_webhook https://<host>/webhooks/paymongo`.
- Test card: `4343 4343 4343 4345`. The checkout form also wants fake name, email, and billing address.
- Demo tenants right now: **Acme** is Pro (two overlapping periods from two real payments made before stacking
  existed), **Globex** is Pro (seeded, no subscription row), **Boundary** is Free with 999 API calls used.
  Resetting Acme to Free: `UPDATE tenants SET plan_code='free' WHERE name='Acme (Free)'; DELETE FROM subscriptions;`.
  (An idea not done: make the seed reset demo tenants to their documented plans.)

## 5. Next: Phase 4 (cost and finalization) — start here

`docs/tasks.md` Phase 4 is the checklist. In order:

1. **`app/core/money.py`**: `micros_from_raw(raw) = (raw + 500_000) // 1_000_000` and `format_usd` (no floats).
2. **`PricingService.cost`** (spec §7.2, §7.4): replace the zero placeholder. Fresh input at the input rate, cached at
   the cached rate, `output + reasoning` at the output rate; each category priced separately, divided once, round
   half up. `GenerateResponse.cost` and the stored `cost_micros` use it.
3. **`GET /usage` rollup** (spec §7.5, §13): sum token counts per category first, price each category once, add
   `api_calls × 2000` and the plan base fee. `MeterUsage.cost_micros` and `total_micros`/`total_usd` become real.
4. **Fix existing tests** that currently assert zero cost (for example `tests/test_api.py` expects
   `api_calls.cost_micros == 0`).
5. **Cost tests** (spec §18): worked example is exactly **10,850 micros** (tokens) and a full `/generate` is
   **12,850 micros**; the three wrong answers 11,750 / 7,100 / 5,250 must not occur; many small events price the
   same as the summed counts; the reasoning-rate loader check already has a test.
6. **Docs pack**: full `README.md` (what it does, ASCII architecture diagram, exact run + seed steps, plans table, and an
   honest **Limitations** section: prepaid periods not auto-renew, fixed PHP price, PayMongo behaviour confirmed only
   for one real flow, no frontend), `capstone.yaml` (spec §19, endpoint `POST /webhooks/paymongo`),
   `.env.example` matches every variable the code reads.
7. **EVIDENCE.md** still has `_TODO_` for: monthly rollup cost, cached/reasoning pricing, pinned constants,
   README/diagram/required files, Probe 5, real persistence, secrets clean, cost tracked / budget guard, GitHub
   rules. Add a proof for each.
8. **Tick the "Final self-check" boxes** in `docs/tasks.md`; many are done but still unticked (Probes 1-3, metering,
   quotas, isolation, background job, validation).
9. **Fresh-clone test**: clone into a new folder, `cp .env.example .env`, run, seed, probe. Fix any undocumented step.
10. **Secret scan before the final push**: `git log -p | grep -E "sk_test_|whsk_|mk_test_"` must show nothing real.

## 6. Open items

- **Probe 4 against a real replayed delivery.** Forged-signature and duplicate results so far came from simulated,
  self-signed events (`scripts/send_test_webhook.py`). To close it: run a checkout with the **Boundary (Free)** key,
  pay, then within 5 minutes press **Replay** on that `POST /webhooks/paymongo` in the ngrok inspector
  (`http://127.0.0.1:4040`). Expect `200 {"received": true, "duplicate": true}` and one `payment_events` row. The 5-minute
  limit is `WEBHOOK_TOLERANCE_SECONDS` (default 300).
- **Screenshots in git history.** `docs/image.png` and `docs/image2.png` were committed by mistake (commits `fb09eed`,
  `f0eb93c`); one shows the user's public IP and browser headers. They are untracked and ignored now, but remain in
  history. Removing them needs a history rewrite and a **force-push to the public repo: ask the user first.**
- **Phase 1 reading list** (`docs/tasks.md`, "Read the Phase 1 resources") is the user's to tick.
- **Unverified PayMongo behaviour**: whether PayMongo re-signs retried deliveries (the 300 s tolerance would reject
  a retry that reuses an old timestamp), and its exact retry schedule. Only one real flow has been observed.
- **Submission**: paste the repo link into the portal form. No ZIP files.
- **Stretch goals** (only after everything above): PayMongo Subscriptions API, overage billing, invoices, usage alerts,
  proration, reconciliation job, CI.

## 7. Hard-won lessons (do not relearn these)

- After a fix, check **which container runs which code** (`docker compose exec worker grep ...`). Use
  `MSYS_NO_PATHCONV=1` for paths starting with `/` in Git Bash.
- The Bash tool breaks on **apostrophes inside heredocs**: write files with the editor tool, or put the script in a file.
- My sandbox cannot reach `api.paymongo.com`. For a read-only PayMongo call, run it in a container with the code mounted:
  `docker compose run --rm -v "$(pwd -W):/app" api python -m scripts.inspect_checkout_session cs_xxx`.
- PayMongo docs are thin and partly unreachable; **never trust a guessed payload shape**. Compare with
  `tests/fixtures/paymongo_checkout_session_payment_paid.json`, which is the real thing.
- The real checkout session's own `status` stays `active` after payment; the `payments[]` entry decides.
- Never `git add docs` blindly: the user pastes screenshots there. Add named files.

## 8. Working agreements with the user

- Commit small, one concern per commit, with messages that say what changed. **Do not add a `Co-Authored-By: Claude`
  trailer** (the user's standing rule, which overrides any default).
- Keep `docs/spec.md` and the code in agreement in the same commit; add a BUILDLOG note when `pricing.toml` changes.
- `BUILDLOG.md` is an honest AI-usage log: record what the AI got wrong, not only what worked.
- Ask before anything outward-facing or hard to reverse (force-push, history rewrite, deleting data).
- Caveman mode was switched off at the user's request ("stop caveman"): use normal prose. Refer to the user as they/them.
- The user does the steps that need their accounts (PayMongo dashboard, ngrok, mentor). Tell them exactly what to run.
- Never ask the user to paste secret keys, signing secrets, or API keys into chat.
