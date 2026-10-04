# Evidence

One proof per requirement (test name + output, curl transcript, or log line).
Fill each section as the matching phase finishes.

## Metering

### A billable action creates exactly one usage event, even under retries
_TODO_

### Proof that double-counting cannot happen
_TODO_

## Quotas

### Usage is checked against the tenant's plan; over-limit requests are rejected
_TODO_

### Correct status codes (429 / 402) and a message explaining why
_TODO_

## Cost calculation

### Monthly usage rolls up into a cost figure per tenant
_TODO_

### Cached input, reasoning, and output token pricing
_TODO_

### Pricing constants pinned in config, with proof of correct totals
_TODO_

## Stripe integration

### Subscription checkout works end-to-end in test mode
_TODO_

### Webhooks verify signatures, ignore duplicates, update tenant plan/status
_TODO_

## Data model, tests and documentation

### Tenants, plans, subscriptions, usage events; data isolated per tenant
_TODO_

### README, architecture diagram, setup instructions, required files present
_TODO_

## Acceptance probes

### Probe 1 — same request twice, one idempotency key
_TODO_

### Probe 2 — exact quota boundary, then 429/402
_TODO_

### Probe 3 — Stripe test Checkout flips Free to Pro
_TODO_

### Probe 4 — forged webhook rejected; replayed event processed once
_TODO_

### Probe 5 — pinned pricing rules give exact totals
_TODO_

## Shared requirements

### Layered architecture
_TODO_

### Validation at the boundary
_TODO_

### Background job with retries and failure alert
_TODO_

### Real persistence (migrations, indexes, isolated tenants)
_TODO_

### Idempotency for metering and webhooks
_TODO_

### Secrets clean
_TODO_

### Cost tracked, quota as budget guard
_TODO_

## GitHub rules

### Phase history visible; no secret in Git history; stranger can run it
_TODO_
