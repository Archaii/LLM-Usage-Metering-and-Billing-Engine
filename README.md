# Usage Metering & Billing Engine

A backend service that answers three questions for every tenant of a SaaS
product: how much have they used, what does it cost, and have they reached their
plan limit? It meters usage exactly once under retries, enforces monthly quotas
with honest `402`/`429` responses, prices AI tokens with real-world rules, and
keeps plans in sync with PayMongo (test mode only).

> **Status:** Phases 0-2 are done (metering, quotas, idempotency, tests). Phase 3
> (PayMongo checkout and webhooks) is in progress; cost calculation is Phase 4.
> The commands below describe the target setup from [docs/spec.md](docs/spec.md);
> see [docs/tasks.md](docs/tasks.md) for what works today.
>
> **Payment provider:** the brief names Stripe, which does not onboard
> Philippines-registered businesses, so this project uses PayMongo. Pro is a
> prepaid 30-day period bought through hosted checkout, not an auto-renewing
> subscription (see the provider note in the spec).

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
python -m scripts.send_test_webhook --url http://localhost:8000/webhooks/paymongo --tenant-id <tenant_uuid> --session-id <cs_id>
                                                            # signed simulated event; add --bad-signature to see the 400
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

## Documentation

Refer to [spec.md](docs/spec.md) for data types and [tasks.md](docs/tasks.md) for roadmap.

The original capstone brief is in
[docs/Usage Metering Billing Engine Live Capstone.pdf](docs/Usage%20Metering%20Billing%20Engine%20Live%20Capstone.pdf).
