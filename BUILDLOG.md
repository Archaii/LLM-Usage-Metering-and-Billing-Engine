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

## 2026-10-04 (later) — first real PayMongo delivery broke two assumptions

- **What happened:** the first real test checkout reached the webhook. The signature check passed,
  which confirms the HMAC scheme. The body was then rejected with `invalid_payload`: seven times,
  as PayMongo retried.
- **What I got wrong:** I had guessed the event shape from doc summaries (flagged as unverified in the
  entry above). Two guesses failed on the first real event:
  1. The event envelope's `created_at` is `null`, not a unix time. My parser required a number.
  2. The body is a snapshot taken before the payment settles: `payments` is `[]`, `paid_at` is `null`,
     and the payment intent is still `processing`. My worker looked for a paid payment inside the body,
     so even with the parser fixed it would never have found one.
- **Fix:** receipt time stands in for the null `created_at`. The worker now confirms with PayMongo
  (`GET /v1/checkout_sessions/{id}`) and grants Pro only when PayMongo shows a paid payment for the
  right amount; an unsettled session is retried with the normal backoff. I read the settled shape
  from the real session (`payments[0]` is `paid`, `amount` 165000, `paid_at` set; the session `status`
  stays `active`), and I kept the real delivery as `tests/fixtures/paymongo_checkout_session_payment_paid.json`
  (client keys redacted) so it is a permanent regression test.
- **Side effect:** a signed event is no longer treated as proof of payment. There is a test where the
  event claims a payment PayMongo does not confirm; it never grants Pro.
- **Cost:** `scripts/send_test_webhook.py` can no longer grant Pro by itself. It needs a real paid
  `--session-id`, because the worker checks with PayMongo. The earlier Probe 4 transcript predates
  this and is flagged in `EVIDENCE.md`.
- **Environment note:** my shell sandbox cannot reach `api.paymongo.com`, so the one read-only
  session lookup ran inside a Docker container with the code mounted. No write calls were made.

## 2026-10-04 (later still) — Gate 3, and a stale worker

- **Gate 3 passed.** A real PayMongo test checkout flipped Acme from Free to Pro through a verified
  webhook, with the worker confirming each payment against PayMongo first. Evidence: `EVIDENCE.md`, Probe 3.
- **My mistake:** after the parser fix I rebuilt only the `api` image. `worker` had its own `build: .`
  entry and therefore its own image, so it kept running the old code and marked both real events
  `skipped` / `no_paid_payment`. The reason string was the clue: the new code cannot produce it. Fixed by
  giving both services one shared image (`metering-engine:dev`). I then reset those two rows to `pending`
  with a manual SQL update on the dev database so the new worker could process them. Lesson: after a fix,
  check which container is actually running which code.
- **Open items:** mentor confirmation of PayMongo and the prepaid-30-day model; Probe 4 against a *real*
  replayed delivery (the forged-signature and duplicate probes so far used simulated, self-signed events).

## 2026-10-04 — mentor answers on the PayMongo switch

- Webhook path `POST /webhooks/paymongo` instead of `/webhooks/stripe`: **approved** (Stripe is unavailable).
- Fixed PHP 1,650.00 per 30-day Pro period (not a live exchange rate): **approved**.
- Prepaid 30-day Pro vs an auto-renewing subscription: the mentor gave no ruling. Decision: keep prepaid,
  because PayMongo's Subscriptions API needs a customer-facing step to capture a card and this project has
  no frontend. The README Limitations section will say so. The Subscriptions API stays a stretch goal.

## 2026-10-04 — stacking renewals, and two screenshots committed by mistake

- **Change:** the `409 already_pro` rejection is gone. A Pro tenant can start another checkout, and the
  webhook starts the new 30-day period when the running one ends (no gap, no wasted paid time); a renewal
  after a lapse starts at the payment time. Decided with the user: keep the prepaid model and add this
  upgrade. Mutation check: forcing `period_start = paid_at` fails the stacking test.
- **Detail found while testing:** two events handled in one batch have the same receipt time (the real
  `created_at` is null), so their order was arbitrary and stacking could come out reversed. The queue
  now orders by `event_created, received_at`.
- **My mistake:** I ran `git add docs` while the user's pasted screenshots sat in `docs/`, so
  `docs/image.png` and `docs/image2.png` reached the public repo (commits `fb09eed`, `f0eb93c`). One shows the
  user's public IP and browser headers (ngrok inspector); another shows tenant IDs. No API keys or secrets
  were in the committed versions as far as I can tell, but I did not inspect every version. They are now
  untracked and ignored. They remain in git history until it is rewritten, which needs the user's approval
  because it means a force-push.

## 2026-10-04 (Phase 4) — real pricing, docs pack, fresh-clone test

- **Done:** `app/core/money.py`, real `PricingService`, priced `/usage` rollup, cost tests (worked example
  10,850 / 12,850 micros, the three wrong answers, 100 small events pricing to 340 not 300). README,
  `capstone.yaml`, EVIDENCE proofs. A fresh clone built, seeded, passed all tests, and answered the probes
  with no undocumented step.
- **Design choice:** the three `CostBreakdown` category lines are rounded separately, but `total_micros` is
  one division of the summed raw cost, as spec section 5 says. The lines can therefore differ from the total by
  one micro. Written into the spec.
- **What the AI got wrong (earlier):** the handoff said 81 tests passed. Five worker/webhook tests actually failed
  once the clock passed 12:00 UTC: they used a hardcoded `T0` of 2026-10-04 12:00, while queued events get the
  real database `now()`, so events were "not due yet". They had only passed earlier in the day. Fixed with a
  far-future `T0`. Lesson: a test that mixes a fixed time with a real clock is time-dependent.
- **Environment note:** the fresh-clone test needed ports 8000 and 5433, so I stopped (not removed) the dev
  containers, ran the clone, tore it down with its own volume, and started the dev stack again. Dev data was untouched.

