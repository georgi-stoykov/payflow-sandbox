# PayFlow Sandbox

A deliberately imperfect mock **crypto-payments gateway** — a system-under-test for
practicing AI-native QA: LLM test generation, self-healing locators, AI failure
triage, synthetic data generation, and K6 load testing.

Domain: currency quotes (EUR/GBP/USD → USDC/BTC/ETH), payments with a
`pending → processing → completed` lifecycle, KYC-gated customers, idempotency
keys, and webhook-simulated settlement. No real money, no external calls,
everything in memory.

## Quick start

```bash
pip install fastapi uvicorn
uvicorn main:app --reload --port 8000
```

- Web console: http://localhost:8000
- OpenAPI spec: http://localhost:8000/docs (feed `/openapi.json` to your LLM test generator)

## Modes

| Env var | Default | Effect |
|---|---|---|
| `BUGS=on\|off` | `on` | Seeded, realistic fintech defects (see `../SPOILERS.md` — try blind first) |
| `CHAOS=on\|off` | `off` | Every UI `id` and `data-testid` gets a random per-boot suffix |

Both can be set in the shell or in the `.env` file next to `main.py` (shell wins).
Values are read once at startup — restart the server after changing them.

Chaos can also be toggled per page load: `http://localhost:8000/?chaos=1`.
Labels, roles, and visible text stay stable in chaos mode — so a locator strategy
based on accessibility semantics (or an LLM heal step) can always recover.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | Shows active modes |
| GET | `/api/customers` | 4 customers with different KYC states |
| POST | `/api/quotes` | `{sell_currency, buy_currency, amount}` → rate, fee (1.5%), expiry (120s) |
| POST | `/api/payments` | `{quote_id, customer_id, idempotency_key?}` |
| GET | `/api/payments/{id}` | Status advances with age: pending → processing (3s) → completed (8s) |
| GET | `/api/payments?limit=&offset=` | Paginated list |
| POST | `/api/webhooks/simulate/{id}?status=` | Force `completed\|failed\|reversed` |
| POST | `/api/admin/reset` | Clear all state |

## Suggested workflow

1. Explore blind with `BUGS=on`. Log every anomaly you (or your AI tooling) find.
2. Check `../SPOILERS.md`. Which bugs did the AI find, which did it miss, and why?
3. Re-run everything with `BUGS=off` — your suite should go fully green.
   A suite that passes on the buggy build is the real lesson.
