# Build log — AI usage

Honest record of where AI helped, where it was wrong, and what I changed.

## 2026-10-04 — Phase 0: repository setup

- **Where AI helped:** Claude Code read `docs/spec.md` and `docs/tasks.md`, then
  generated the Phase 0 scaffolding: `.gitignore`, `.dockerignore`,
  `.env.example` (all variables from spec §17), `requirements.txt`,
  `Dockerfile`, `compose.yaml` (`db`, `api`, `worker`), and the placeholder
  `EVIDENCE.md`.
- **Where AI was wrong / caught late:** nothing wrong yet. One catch: the folder
  sat inside a parent Git repo with no commits, so a plain `git init` check
  would have reported the wrong repo root. The project needs its own repo, so a
  new one was initialised inside this folder.
- **What I changed:** nothing so far; scaffolding accepted as generated.

## 2026-10-04 — Phase 0 gate, Phase 1 design, Phase 2 core logic

- **Where AI helped:** wrote `config/pricing.toml`, `docs/design.md`, the Alembic
  migration, layered code (`api/`, `services/`, `repositories/`), seed script, and
  30 tests (idempotency, 20-way concurrency, quota boundary, validation, isolation).
- **Where AI was wrong / what I caught:**
  - Shell heredocs failed on this Windows Git Bash, so files were written with the
    editor tool instead. No code impact.
  - The first concurrency test compared raw response text. Replays came back with
    different key order because the stored body is `JSONB`, which does not keep key
    order. The JSON is equal; the bytes are not. I changed the test to compare
    parsed JSON instead of changing storage. Consequence: a replay is semantically
    identical to the first response, not byte-identical.
  - A stray dead expression (`... if False else None`) slipped into
    `MeterService.usage_summary`; replaced with a proper `tenants.get_by_id`.
- **Decisions that differ from a literal reading of the spec:**
  - Startup plan check: spec says refuse to start when `plans` and `pricing.toml`
    disagree, but `docker compose up` (migrations only) runs before `seed`. An empty
    `plans` table is therefore filled from `pricing.toml` at startup; a non-empty
    table that differs still refuses to start.
  - `PricingService` is a zero-cost placeholder until Phase 4, so `/generate` and
    `/usage` report `0` cost for now.
  - Seed re-runs rotate demo API keys, because only hashes are stored.

## 2026-10-04 — Stripe to PayMongo switch, Phase 3 code

- **Why:** Stripe does not onboard Philippines-registered businesses. Switched the
  provider to PayMongo (test mode) and rewrote `docs/spec.md`, `docs/tasks.md`,
  `docs/design.md`, and the README before writing code.
- **Where AI helped:** researched PayMongo, rewrote the docs, wrote migration `0002`,
  the PayMongo client and signature check, billing and webhook services, the worker
  (retry, alert, expiry), two scripts, and 45 new tests (75 total).
- **Design decision, and its cost:** PayMongo's Subscriptions API needs a customer-facing
  step to capture a card, and this project has no frontend. Pro is therefore a
  **prepaid 30-day period** bought through Hosted Checkout, not an auto-renewing
  subscription. This departs from the brief's "subscription checkout". The Subscriptions
  API is listed as a stretch goal. Needs mentor confirmation.
- **Where AI was uncertain or wrong:**
  - PayMongo's docs were hard to fetch (several 404s and redirects) and thin on webhook
    payloads. The event envelope (`data.attributes.type`, `created_at`, `data.attributes.data`),
    the event name `checkout_session.payment.paid`, the `payments[].attributes.amount` field,
    and the webhook creation response (`secret_key`) come from doc summaries and search
    snippets. **None is verified against a real PayMongo event yet.** Gate 3 requires
    comparing the first real delivery with spec section 14.3.
  - A test failed intermittently: the worker compared Python's clock with the database
    default `now()`, and the Docker Postgres clock runs ahead of the host. Fixed by making
    the queue and expiry use the database clock. The suite then passed 5 runs in a row.
  - The worker started before the API had run migrations on a fresh `docker compose up`
    and logged errors until the tables existed. Added an API healthcheck that the worker
    waits for.
  - Spec said retries wait 2/4/8/16/32 s but also fail after 5 attempts; five attempts
    only have four waits. Corrected the spec to 2/4/8/16 s.
- **Checks I ran:** broke the signature compare and the `ON CONFLICT DO NOTHING` dedupe on
  purpose; the forged-signature and duplicate tests failed each time, then I restored both.
- **Evidence limits:** the Probe 4 transcript uses simulated events signed by
  `scripts/send_test_webhook.py`. It proves our code, not that PayMongo's real signatures
  match our scheme. Probe 3 (real checkout) is not done.
- **Pricing note:** `config/pricing.toml` gained a `[checkout]` section (PHP 1,650.00 per
  30-day period, a fixed figure approximating the $29.00 base fee, not a live exchange rate).
