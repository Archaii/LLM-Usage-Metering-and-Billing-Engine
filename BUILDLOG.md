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
