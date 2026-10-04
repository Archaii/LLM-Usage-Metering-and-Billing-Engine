# Evidence

One proof per requirement (test name + output, curl transcript, or log line).
Fill each section as the matching phase finishes.

## Metering

### A billable action creates exactly one usage event, even under retries
Tests (run with `docker compose run --rm api pytest`, 30 passed):

- `tests/test_idempotency.py::test_same_key_same_body_twice_creates_one_row` — `201` then `200`, one row.
- `tests/test_idempotency.py::test_20_concurrent_requests_with_one_key_create_one_row` — 20 parallel requests, one `201`, nineteen `200`, one row.
- `tests/test_idempotency.py::test_same_key_different_body_is_rejected` — `422 idempotency_key_reused`.


### Proof that double-counting cannot happen
See Probe 1 below for the transcript and the `SELECT count(*)` result (`1`). `tests/test_idempotency.py::test_replay_does_not_consume_quota_again` shows `GET /usage` counts the request once after a retry.


## Quotas

### Usage is checked against the tenant's plan; over-limit requests are rejected
- `tests/test_quota.py::test_999_plus_1_is_allowed_then_1000_plus_1_is_rejected_free`
- `tests/test_quota.py::test_token_request_crossing_the_limit_is_rejected_whole` — no partial row.
- `tests/test_quota.py::test_token_request_exactly_filling_the_limit_is_allowed`


### Correct status codes (429 / 402) and a message explaining why
Free: `402 upgrade_required` with `upgrade_url`. Pro: `429 quota_exceeded` with `Retry-After`. Past due: `402 payment_required`. See Probe 2 for the live `402` body, and `tests/test_quota.py` for the `429` and `payment_required` cases.


## Cost calculation

### Monthly usage rolls up into a cost figure per tenant
_TODO_

### Cached input, reasoning, and output token pricing
_TODO_

### Pricing constants pinned in config, with proof of correct totals
_TODO_

## Payment integration (PayMongo)

### Checkout works end-to-end in PayMongo test mode
See Probe 3 below: a real PayMongo test checkout (hosted page, test card) flipped Acme from Free to Pro through a verified webhook, and `POST /billing/checkout` then returns `409 already_pro`. Unit tests with a fake provider client: `tests/test_billing.py`.


### Webhooks verify signatures, ignore duplicates, update tenant plan
See Probe 4 above. Tests: `tests/test_webhooks.py` (signature, dedupe, grant, amount guard, spoofed-metadata, unknown tenant) and `tests/test_worker.py` (retry/backoff, failure alert, expiry).


## Data model, tests and documentation

### Tenants, plans, subscriptions, usage events; data isolated per tenant
Migration `migrations/versions/0001_initial_schema.py` creates all tables. Isolation: `tests/test_api.py::test_tenant_b_cannot_see_tenant_a_usage` and `tests/test_idempotency.py::test_same_key_from_two_tenants_makes_two_rows`.


### README, architecture diagram, setup instructions, required files present
_TODO_

## Acceptance probes

### Probe 1 — same request twice, one idempotency key
```text
Live system (Docker Compose), seeded Acme (Free) tenant. same request twice, Idempotency-Key: probe1-1791095841

$ curl -i -X POST localhost:8000/generate -H 'X-API-Key: mk_test_<redacted>' -H 'Idempotency-Key: probe1-1791095841' -d '<body>'  # request 1
HTTP/1.1 201 Created
{"event_id":"3f5db932-20c0-4f73-9a5a-128add0f24bb","tenant_id":"e773fce8-f075-4fc4-9fc7-09995e63a759","output":"[simulated] response to: Summarize this ticket","tokens":{"input_tokens":10000,"cached_input_tokens":4000,"output_tokens":2000,"reasoning_tokens":1500},"cost":{"api_call_micros":0,"fresh_input_micros":0,"cached_input_micros":0,"output_micros":0,"total_micros":0,"total_usd":"0.000000"},"quota":{"api_calls_used":1,"api_call_limit":1000,"tokens_used":13500,"token_limit":100000},"created_at":"2026-10-04T06:37:21Z"}

$ curl -i -X POST localhost:8000/generate -H 'X-API-Key: mk_test_<redacted>' -H 'Idempotency-Key: probe1-1791095841' -d '<body>'  # request 2
HTTP/1.1 200 OK
idempotent-replayed: true
{"cost":{"total_usd":"0.000000","total_micros":0,"output_micros":0,"api_call_micros":0,"fresh_input_micros":0,"cached_input_micros":0},"quota":{"token_limit":100000,"tokens_used":13500,"api_call_limit":1000,"api_calls_used":1},"output":"[simulated] response to: Summarize this ticket","tokens":{"input_tokens":10000,"output_tokens":2000,"reasoning_tokens":1500,"cached_input_tokens":4000},"event_id":"3f5db932-20c0-4f73-9a5a-128add0f24bb","tenant_id":"e773fce8-f075-4fc4-9fc7-09995e63a759","created_at":"2026-10-04T06:37:21Z"}

$ psql: SELECT count(*) FROM usage_events WHERE idempotency_key = 'probe1-1791095841';
 count 
-------
     1
(1 row)
```

Second response has the same `event_id` and the same content. Key order differs because the stored body is `JSONB`; the parsed JSON is equal (see BUILDLOG.md).


### Probe 2 — exact quota boundary, then 429/402
```text
Live system (Docker Compose), seeded tenant. Boundary tenant (Free, 999 of 1000 API calls used)

$ curl -i -X POST localhost:8000/generate -H 'X-API-Key: mk_test_<redacted>' -H 'Idempotency-Key: probe2-1' -d '<1-token body>'  # call number 1000
HTTP/1.1 201 Created
{"event_id":"46e43246-c848-4f6d-8d62-da60202a6a74","tenant_id":"e3e991e6-4c5e-4bbc-b459-dbee6d1a5912","output":"[simulated] response to: hi","tokens":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":0,"reasoning_tokens":0},"cost":{"api_call_micros":0,"fresh_input_micros":0,"cached_input_micros":0,"output_micros":0,"total_micros":0,"total_usd":"0.000000"},"quota":{"api_calls_used":1000,"api_call_limit":1000,"tokens_used":1,"token_limit":100000},"created_at":"2026-10-04T06:37:32Z"}

$ curl -i -X POST localhost:8000/generate -H 'X-API-Key: mk_test_<redacted>' -H 'Idempotency-Key: probe2-2' -d '<1-token body>'  # call number 1001
HTTP/1.1 402 Payment Required
{"error":"upgrade_required","message":"Free plan API call quota reached: 1,000 of 1,000 API calls used this month and this request needs 1. Upgrade to Pro for 50,000 API calls per month.","details":{"meter":"api_calls","used":1000,"requested":1,"limit":1000,"period_end":"2026-11-01T00:00:00Z"},"upgrade_url":"/billing/checkout"}
```

The call that makes exactly 1,000 of 1,000 is allowed (`201`). The next call is rejected with `402 upgrade_required` and a message naming the meter, used, requested, limit. A Pro tenant gets `429 quota_exceeded` with `Retry-After` (test `test_pro_over_quota_is_429_with_retry_after`).


### Probe 3 — PayMongo test Checkout flips Free to Pro
Real PayMongo test checkout (card `4343 4343 4343 4345`), live Compose stack behind an ngrok tunnel. Tenant: Acme (Free).

**Before** (`GET /usage`, from the terminal at the time):

```text
"plan":"free", "api_calls": {"used":1,"limit":1000,...}, "tokens": {"used":13500,"limit":100000,...}
```

**Deliveries seen by ngrok** (`POST /webhooks/paymongo`): the first real delivery was answered `400 invalid_payload`
(seven retries from PayMongo) because my parser expected a numeric event `created_at` and a paid payment inside
the body; the real body has `created_at: null` and `payments: []`. After the fix PayMongo's retry and the second
checkout's event were answered `200 OK`. The real body is kept as
`tests/fixtures/paymongo_checkout_session_payment_paid.json` (client keys redacted).

**Worker confirms with PayMongo, then grants** (`docker compose logs worker`):

```text
httpx HTTP Request: GET https://api.paymongo.com/v1/checkout_sessions/cs_6d63262025574de03a767a11 "HTTP/1.1 200 OK"
httpx HTTP Request: GET https://api.paymongo.com/v1/checkout_sessions/cs_817d4b34ea9a5365e72de1d1 "HTTP/1.1 200 OK"
```

**After** (database):

```text
 event_id                     | status    | attempts | last_error
------------------------------+-----------+----------+-----------
 evt_o7neiiESdA5PQbPEHgQPaUT9 | processed |        0 |
 evt_SgKMS96JmFM3mcKV99HK8U4i | processed |        0 |

 provider_payment_id          | checkout_session_id         | status | current_period_start   | current_period_end
------------------------------+-----------------------------+--------+------------------------+-----------------------
 pay_vmW3bqdjMYgiRFX2cBnQXCfe | cs_6d63262025574de03a767a11 | active | 2026-10-04 08:02:40+00 | 2026-11-03 08:02:40+00
 pay_NCpSaFH6myhdX9ssPtx9hvWX | cs_817d4b34ea9a5365e72de1d1 | active | 2026-10-04 08:23:09+00 | 2026-11-03 08:23:09+00

 name        | plan_code
-------------+-----------
 Acme (Free) | pro
```

```text
$ curl localhost:8000/usage -H "X-API-Key: <acme>"
{"plan": "pro", "billing_status": "ok", "api_call_limit": 50000, "token_limit": 5000000}
$ curl -X POST localhost:8000/billing/checkout -H "X-API-Key: <acme>"      # already Pro
HTTP 409  {"error":"already_pro", ...}
```

Notes, stated plainly:

- Acme made **two** real payments (the first checkout, then a second one made while the code was still being
  fixed), so it holds two `active` periods. That is correct behaviour for two paid payments.
- Both events were first marked `skipped` / `no_paid_payment` by a **stale worker container** that was still
  running the old code (the rebuild only refreshed the `api` image). I fixed `compose.yaml` so `api` and
  `worker` share one image, set the two rows back to `pending` in the dev database, and the new worker then
  processed them as shown. That reset was a manual database edit, not a product feature.


### Probe 4 — forged webhook rejected; replayed event processed once
**Simulated deliveries** (signed by `scripts/send_test_webhook.py`; not PayMongo-originated). *Captured before the worker started confirming payments with PayMongo; the signature and dedupe results still hold, but the grant step is re-run against a real paid session at Gate 3.*

```text
Live system (Docker Compose: api + worker + db). SIMULATED events built and signed by scripts/send_test_webhook.py, not sent by PayMongo.

$ GET /usage (Acme, before)
plan=free api_call_limit=1000 token_limit=100000

$ python -m scripts.send_test_webhook --tenant-id <acme> --bad-signature
HTTP 400 {"error":"invalid_signature","message":"The webhook signature does not match the request body."}
$ psql: SELECT count(*) FROM payment_events;  -- forged delivery wrote nothing
0

$ python -m scripts.send_test_webhook --tenant-id <acme> --event-id evt_probe4_1791098083   # delivery 1
HTTP 200 {"received":true}
$ python -m scripts.send_test_webhook --tenant-id <acme> --event-id evt_probe4_1791098083   # delivery 2 (replay)
HTTP 200 {"received":true,"duplicate":true}

$ psql: SELECT event_id, status, attempts FROM payment_events;
       event_id        |  status   | attempts 
-----------------------+-----------+----------
 evt_probe4_1791098083 | processed |        0
(1 row)

$ psql: SELECT status, current_period_end - current_period_start AS length FROM subscriptions;
 status | length  
--------+---------
 active | 30 days
(1 row)

$ GET /usage (Acme, after)
plan=pro api_call_limit=50000 token_limit=5000000
```

Tests: `tests/test_webhooks.py` (forged, missing, malformed, stale, tampered, live-only signatures, all `400` with no row; same event twice gives one row and one grant).

_Still to add at Gate 3:_ a real PayMongo delivery replayed from the dashboard or tunnel inspector.


### Probe 5 — pinned pricing rules give exact totals
_TODO_

## Shared requirements

### Layered architecture
`app/api/` (routes, schemas, errors, deps) calls `app/services/` (meter, quota, pricing, plan_sync), which call `app/repositories/` (SQL only). Services raise `app/core/errors.py` domain errors; `app/api/errors.py` maps them to statuses.


### Validation at the boundary
`tests/test_api.py::test_bad_input_is_422_never_500` (negative tokens, cached > input, empty or missing prompt, oversize tokens, missing tokens) and `test_malformed_json_is_422`.


### Background job with retries, failure alert, and Pro expiry
`tests/test_worker.py::test_failing_handler_retries_with_backoff_then_fails_with_an_alert` (2/4/8/16 s waits, fifth failure gives `failed` plus an `alerts` row), `test_one_failing_event_does_not_block_the_others_in_the_batch`, `test_expiry_returns_a_lapsed_tenant_to_free`. Live: the `worker` Compose service processed the simulated event in Probe 4 (`status = processed`).


### Real persistence (migrations, indexes, isolated tenants)
_TODO_

### Idempotency for metering and webhooks
Metering: Probe 1 and `tests/test_idempotency.py`. Webhooks: Probe 4 (`duplicate: true`, one row) and `tests/test_webhooks.py::test_two_different_events_for_one_payment_grant_once` (second layer: `subscriptions.provider_payment_id` is UNIQUE).



### Secrets clean
_TODO_

### Cost tracked, quota as budget guard
_TODO_

## GitHub rules

### Phase history visible; no secret in Git history; stranger can run it
_TODO_
