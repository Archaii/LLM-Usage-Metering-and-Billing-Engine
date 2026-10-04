# Usage Metering & Billing Engine

A backend service that answers three questions for every tenant of a SaaS
product: how much have they used, what does it cost, and have they reached their
plan limit? It meters usage exactly once under retries, enforces monthly quotas
with honest `402`/`429` responses, prices AI tokens with real-world rules, and
keeps plans in sync with PayMongo (test mode only).

## Tech stack

- **Language:** Python 3.12
- **API:** FastAPI + Uvicorn, Pydantic v2 for validation
- **Database:** PostgreSQL 16 in Docker, psycopg 3 (raw SQL), Alembic migrations
- **Payments:** PayMongo test mode (Hosted Checkout + signed webhooks, REST via `httpx`) and an HTTPS tunnel (ngrok or cloudflared) for local webhooks
- **Config:** python-dotenv for secrets, `config/pricing.toml` for pinned prices
- **Tests:** pytest + FastAPI `TestClient`
- **Runtime:** Docker Compose — services `db`, `api`, `worker`

## Architecture

A layered FastAPI service: HTTP routes call services (`MeterService`,
`QuotaService`, `PricingService`, `BillingService`), and only repositories talk to
PostgreSQL. A billable `POST /generate` locks the tenant row, deduplicates by
idempotency key, checks `used + requested ≤ limit`, prices the tokens in integer
micro-USD, and stores exactly one usage event; `GET /usage` rolls the month's
events up into used, limit, and cost. PayMongo webhooks are signature-verified and
deduplicated by event ID on receipt, then a background worker applies them to the
tenant's plan with retries and failure alerts.

```
Client --POST /generate (X-API-Key, Idempotency-Key)--> api
   |
   '-> MeterService.record                      [one DB transaction]
         lock tenant row (SELECT ... FOR UPDATE)
         key seen + same body      -> stored response (no new event)
         key seen + different body -> 422 idempotency_key_reused
         QuotaService.check(used + requested <= limit)
            '- exceeded -> 402 (Free) / 429 (Pro), nothing stored
         PricingService.cost(tokens)            [integer micro-USD]
         INSERT usage_event -> 201

Client --GET /usage--> sum token counts for the month, price each category once,
                       add API-call cost and the plan base fee

Client --POST /billing/checkout--> PayMongo Hosted Checkout (test mode) -> checkout_url

PayMongo --signed webhook--> POST /webhooks/paymongo
            verify Paymongo-Signature (forged -> 400, nothing written)
            INSERT payment_events ON CONFLICT DO NOTHING (replay -> duplicate)
            200 at once
worker --poll payment_events (SKIP LOCKED)--> confirm with PayMongo, grant a 30-day Pro period
            retries 2/4/8/16 s, then status=failed + alerts row + ERROR log
            also expires lapsed Pro periods -> tenant back to Free
```

## Plans

| Plan | API calls / month | AI tokens / month | Base fee |
| --- | --- | --- | --- |
| Free | 1,000 | 100,000 | $0.00 |
| Pro | 50,000 | 5,000,000 | $29.00 |

Rates: $0.002 per API call; per 1M tokens — input $0.30, cached input $0.075,
output and reasoning $2.50. All money is stored as integer micro-USD.

## Build, run, and test

### With Docker (recommended)

```bash
cp .env.example .env                                   # then fill in the PayMongo test keys
docker compose up --build                              # starts db, api (runs migrations), worker
docker compose run --rm api python -m app.seed         # demo tenants; prints their API keys
docker compose run --rm api pytest                     # full test suite
docker compose run --rm api alembic upgrade head       # apply migrations by hand (optional)
docker compose down                                    # stop; data stays in the volume
```

- API: <http://localhost:8000>
- Swagger UI: <http://localhost:8000/docs>

### PayMongo webhooks (separate terminal)

PayMongo cannot reach `localhost`, so expose the API through an HTTPS tunnel.

```bash
ngrok http 8000                                             # copy the https://... URL into APP_BASE_URL in .env
python -m scripts.register_webhook https://<tunnel-host>/webhooks/paymongo
                                                            # prints the signing secret once -> PAYMONGO_WEBHOOK_SECRET in .env
curl -X POST localhost:8000/billing/checkout -H "X-API-Key: <key>"   # returns checkout_url; open it and pay
python -m scripts.send_test_webhook --tenant-id <tenant_uuid> --session-id <paid cs_id>
                                                            # signed simulated event for a real paid session; add --bad-signature to see the 400
```

Use test card `4343 4343 4343 4345` with any future expiry and any CVC in Checkout.
Never use live keys; the app refuses to start with one.

### Without Docker (local Python, database still in Docker)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
docker compose up -d db
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload        # API
python -m app.worker                 # worker, in a second terminal
pytest
```

## Try it

After `docker compose up --build -d` and the seed command, use a key the seed printed:

```bash
KEY=mk_test_...                                        # from the seed output
curl -s localhost:8000/plans
curl -s -X POST localhost:8000/generate -H "X-API-Key: $KEY" -H "Idempotency-Key: demo-1"   -H "Content-Type: application/json"   -d '{"prompt":"hi","tokens":{"input_tokens":10000,"cached_input_tokens":4000,"output_tokens":2000,"reasoning_tokens":1500}}'
                                                       # cost.total_micros = 12850; send it again: 200 + Idempotent-Replayed
curl -s localhost:8000/usage -H "X-API-Key: $KEY"      # used, limit, remaining, cost_micros, total_usd
```

The seeded **Boundary (Free)** tenant already has 999 of 1,000 API calls used: the next call is allowed,
the one after returns `402 upgrade_required`.

## Limitations

- **Pro is a prepaid 30-day period, not an auto-renewing subscription.** PayMongo's Subscriptions API
  needs a customer-facing page to capture a card, and this project has no frontend. Renewal means paying
  again; renewals stack after the running period. The Subscriptions API is a stretch goal.
- **The Pro price is a fixed PHP 1,650.00 per period**, a pinned approximation of the $29.00 base fee, not a
  live exchange rate. The ledger itself stays in micro-USD.
- **PayMongo behaviour is confirmed for one real flow** (test-mode Hosted Checkout with the test card).
  Whether PayMongo re-signs retried deliveries, and its exact retry schedule, are not verified.
- **Test mode only.** The app refuses to start with a key that does not begin with `sk_test_`.
- **No frontend, invoices, proration, or overage billing.** Token counts are supplied by the client; no
  AI model is called. The usage period is the calendar month in UTC.
- **Local webhooks need an HTTPS tunnel** (ngrok or cloudflared); the tunnel URL can change between runs.

## Design, proof and history

- [docs/design.md](docs/design.md): database schema, plans and quotas, the metering API contract, and the idempotency strategy.
- [EVIDENCE.md](EVIDENCE.md): one proof per requirement (test names, transcripts, hand calculations).
- [BUILDLOG.md](BUILDLOG.md): honest log of where AI helped and where it was wrong.
