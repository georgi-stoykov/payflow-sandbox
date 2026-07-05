# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PayFlow Sandbox — a deliberately imperfect mock crypto-payments gateway (single-file FastAPI app in `main.py`). It exists as the **system under test** for practicing AI-native QA: LLM test generation, self-healing locators, AI failure triage, synthetic data, and load testing. It is not a real payments system — no real money, no external calls, all state is in-memory and resets on restart.

This repo is a sibling of `../payflow-automation` (the black-box test suite) and `../SPOILERS.md` (the seeded bug list) and `../PLAN.md` (the practice plan) — see [`test-framework-conventions`] memory: the test suite must stay black-box and must never reference `SPOILERS.md` or the `BUGS` flag, and must never start this app itself.

## Commands

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

- Web console: http://localhost:8000
- OpenAPI spec: http://localhost:8000/docs (or `/openapi.json`)
- No test suite, linter, or build step lives in this repo — those live in `../payflow-automation`.

## Modes (env vars, shell overrides `.env`)

- `BUGS=on|off` (default `on`) — toggles the seeded defects below. Set once at startup; restart to change.
- `CHAOS=on|off` (default `off`) — randomizes every UI `id`/`data-testid` with a per-boot suffix to exercise self-healing locators. Also togglable per-request via `?chaos=1`. Accessible labels/roles/visible text stay stable in chaos mode by design, so locator strategies should prefer those over raw ids.

## Architecture

Everything lives in `main.py`: in-memory dicts (`CUSTOMERS`, `QUOTES`, `PAYMENTS`, `PAYMENT_ORDER`, `IDEMPOTENCY_WINDOW`) stand in for a database, FastAPI routes implement the REST API, and a single Python string (`PAGE`) template renders the web console — there's no separate frontend build.

Request flow: `POST /api/quotes` (rate lookup + fee calc) → `POST /api/payments` (validates quote expiry + customer KYC, applies idempotency) → payment status lazily advances `pending → processing (3s) → completed (8s)` on each `GET /api/payments/{id}` read (see `_advance`), or can be forced via `POST /api/webhooks/simulate/{id}`.

`sel(name, chaos)` in `main.py` is the selector factory: every element id and data-testid used by the console is generated through it, so **any new UI element must be wired through `sel()`** to stay chaos-mode-compatible.

## Seeded bugs (`BUGS=on`, the default)

Full detail lives in `../SPOILERS.md`. When editing `main.py`, each seeded bug is marked inline with a `# BUG #n (...)` comment — preserve these markers and the `if BUGS:` branching around them; the `BUGS=off` path is the "correct" reference implementation and must stay behaviorally correct. Don't fix a bug in the `BUGS=on` path without deliberate intent — that's the point of the sandbox.
