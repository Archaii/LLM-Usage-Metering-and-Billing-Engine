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
