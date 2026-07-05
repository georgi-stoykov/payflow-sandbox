"""
PayFlow Sandbox — a deliberately imperfect mock crypto-payments gateway.

A system-under-test for practicing AI-native QA:
  * REST API: quotes, payments, customers (KYC), webhook simulation
  * Web UI:   payment console (Playwright target)
  * CHAOS mode: mutates DOM selectors each boot -> exercises self-healing locators
  * BUGS  mode: seeded, realistic fintech defects -> exercises AI triage & test design

Run:
    uvicorn main:app --reload --port 8000

Env flags (set in the shell, or in a .env file next to this script;
a real environment variable wins over .env):
    BUGS=on|off     (default: on)   enable seeded defects
    CHAOS=on|off    (default: off)  randomize UI selectors (or visit /?chaos=1)

See SPOILERS.md for the seeded bug list. Try to find them blind first.
"""

import os
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

BUGS = os.getenv("BUGS", "on").lower() in ("on", "1", "true", "all")
CHAOS = os.getenv("CHAOS", "off").lower() in ("on", "1", "true")

app = FastAPI(title="PayFlow Sandbox", version="1.0.0")

# ---------------------------------------------------------------------------
# Domain data (in-memory, resets on restart)
# ---------------------------------------------------------------------------

RATES = {
    ("EUR", "USDC"): Decimal("1.0842"),
    ("EUR", "BTC"): Decimal("0.0000112"),
    ("EUR", "ETH"): Decimal("0.000305"),
    ("GBP", "USDC"): Decimal("1.2691"),
    ("USD", "USDC"): Decimal("0.9998"),
    ("USDC", "EUR"): Decimal("0.9212"),
}
FEE_RATE = Decimal("0.015")  # 1.5%
QUOTE_TTL_SECONDS = 120

CUSTOMERS = {
    "cus_001": {"id": "cus_001", "name": "Aurora Ltd", "kyc_status": "verified"},
    "cus_002": {"id": "cus_002", "name": "Borealis GmbH", "kyc_status": "pending"},
    "cus_003": {"id": "cus_003", "name": "Cygnus SA", "kyc_status": "rejected"},
    # Bug bait: legacy record migrated with capitalized status
    "cus_004": {"id": "cus_004", "name": "Draco OOD", "kyc_status": "Verified"},
}

QUOTES: dict = {}
PAYMENTS: dict = {}
PAYMENT_ORDER: list = []
IDEMPOTENCY_WINDOW: list = []  # (key, payment_id) — bug: bounded to last 10

def now() -> datetime:
    return datetime.now(timezone.utc)

def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class QuoteRequest(BaseModel):
    sell_currency: str = Field(examples=["EUR"])
    buy_currency: str = Field(examples=["USDC"])
    amount: float = Field(examples=[250.00], description="Amount in sell currency")

class PaymentRequest(BaseModel):
    quote_id: str
    customer_id: str
    idempotency_key: Optional[str] = None
    reference: Optional[str] = None

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "bugs": BUGS, "chaos": CHAOS, "time": now().isoformat()}

@app.get("/api/customers")
def list_customers():
    return list(CUSTOMERS.values())

@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: str):
    if customer_id not in CUSTOMERS:
        raise HTTPException(404, "customer not found")
    return CUSTOMERS[customer_id]

@app.post("/api/quotes", status_code=201)
def create_quote(req: QuoteRequest):
    pair = (req.sell_currency.upper(), req.buy_currency.upper())
    if pair not in RATES:
        raise HTTPException(422, f"unsupported currency pair {pair[0]}->{pair[1]}")

    # BUG #1 (validation): negative & zero amounts are accepted.
    if not BUGS and req.amount <= 0:
        raise HTTPException(422, "amount must be positive")

    rate = RATES[pair]

    if BUGS:
        # BUG #2 (precision): fee computed with float round() — binary float
        # representation + banker's rounding undercharges by a cent on many
        # amounts (try 1, 3, 5, 7, 11, 33, 67, 101 ...).
        fee = Decimal(str(round(req.amount * 0.015, 2)))
    else:
        fee = money(Decimal(str(req.amount)) * FEE_RATE)

    # BUG #3 (boundary): fee waived at >= 50,000 — an old promo flag never removed.
    if BUGS and req.amount >= 50000:
        fee = Decimal("0.00")

    amount_dec = Decimal(str(req.amount))
    # Net amount after fee, converted at the locked rate.
    amount_out = money((amount_dec - fee) * rate)

    quote = {
        "id": f"qt_{uuid.uuid4().hex[:12]}",
        "sell_currency": pair[0],
        "buy_currency": pair[1],
        "amount_in": float(amount_dec),
        "fee": float(fee),
        "rate": float(rate),
        "amount_out": float(amount_out),
        "created_at": now().isoformat(),
        "expires_at": (now() + timedelta(seconds=QUOTE_TTL_SECONDS)).isoformat(),
    }
    QUOTES[quote["id"]] = quote
    return quote

@app.post("/api/payments", status_code=201)
def create_payment(req: PaymentRequest):
    quote = QUOTES.get(req.quote_id)
    if not quote:
        raise HTTPException(404, "quote not found")

    expires = datetime.fromisoformat(quote["expires_at"])
    if BUGS and quote["buy_currency"] == "USDC":
        # BUG #4 (expiry): inverted guard for the most-used pair —
        # expired USDC quotes are still accepted.
        pass
    elif now() > expires:
        raise HTTPException(409, "quote expired")

    customer = CUSTOMERS.get(req.customer_id)
    if not customer:
        raise HTTPException(404, "customer not found")

    # BUG #5 (KYC): case-sensitive comparison — 'Verified' (cus_004) is
    # wrongly blocked, while the check only blocks lowercase 'rejected'.
    if BUGS:
        if customer["kyc_status"] != "verified":
            if customer["kyc_status"] == "pending":
                raise HTTPException(403, "customer KYC pending")
            if customer["kyc_status"] == "rejected":
                raise HTTPException(403, "customer KYC rejected")
            raise HTTPException(403, "customer KYC not verified")
    else:
        if customer["kyc_status"].strip().lower() != "verified":
            raise HTTPException(403, f"customer KYC {customer['kyc_status'].lower()}")

    # BUG #6 (idempotency): key lookup window holds only the last 10 payments,
    # so retries after volume create duplicates.
    if req.idempotency_key:
        window = IDEMPOTENCY_WINDOW[-10:] if BUGS else IDEMPOTENCY_WINDOW
        for key, pid in window:
            if key == req.idempotency_key:
                return JSONResponse(status_code=200, content=PAYMENTS[pid])

    payment = {
        "id": f"pay_{uuid.uuid4().hex[:12]}",
        "quote_id": quote["id"],
        "customer_id": req.customer_id,
        "reference": req.reference,
        "sell_currency": quote["sell_currency"],
        "buy_currency": quote["buy_currency"],
        "amount_in": quote["amount_in"],
        "fee": quote["fee"],
        "amount_out": quote["amount_out"],
        "status": "pending",
        "created_at": now().isoformat(),
        "updated_at": now().isoformat(),
    }
    PAYMENTS[payment["id"]] = payment
    PAYMENT_ORDER.append(payment["id"])
    if req.idempotency_key:
        IDEMPOTENCY_WINDOW.append((req.idempotency_key, payment["id"]))
    return payment

def _advance(payment: dict) -> dict:
    """Lazy state machine: pending -> processing (after 3s) -> completed (after 8s)."""
    age = (now() - datetime.fromisoformat(payment["created_at"])).total_seconds()
    new_status = payment["status"]
    if payment["status"] in ("pending", "processing"):
        if age >= 8:
            new_status = "completed"
        elif age >= 3:
            new_status = "processing"
    if new_status != payment["status"]:
        payment["status"] = new_status
        payment["updated_at"] = now().isoformat()
    return payment

@app.get("/api/payments/{payment_id}")
def get_payment(payment_id: str):
    if payment_id not in PAYMENTS:
        raise HTTPException(404, "payment not found")
    return _advance(PAYMENTS[payment_id])

@app.get("/api/payments")
def list_payments(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    ids = list(reversed(PAYMENT_ORDER))
    # BUG #7 (pagination): off-by-one — silently drops the last row of each page.
    end = offset + limit - 1 if BUGS else offset + limit
    page = [_advance(PAYMENTS[i]) for i in ids[offset:end]]
    return {"total": len(ids), "limit": limit, "offset": offset, "items": page}

@app.post("/api/webhooks/simulate/{payment_id}")
def simulate_webhook(payment_id: str, status: str = Query("completed")):
    """Force a payment into a terminal state, as a settlement webhook would."""
    if payment_id not in PAYMENTS:
        raise HTTPException(404, "payment not found")
    if status not in ("completed", "failed", "reversed"):
        raise HTTPException(422, "status must be completed|failed|reversed")
    PAYMENTS[payment_id]["status"] = status
    PAYMENTS[payment_id]["updated_at"] = now().isoformat()
    return PAYMENTS[payment_id]

@app.post("/api/admin/reset")
def reset():
    QUOTES.clear(); PAYMENTS.clear(); PAYMENT_ORDER.clear(); IDEMPOTENCY_WINDOW.clear()
    return {"status": "reset"}

# ---------------------------------------------------------------------------
# Web UI (Playwright target)
# ---------------------------------------------------------------------------

_chaos_suffix = "_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))

def sel(name: str, chaos: bool) -> str:
    """Selector factory. In chaos mode every id/testid gets a per-boot suffix."""
    return f"{name}{_chaos_suffix}" if chaos else name

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PayFlow Console</title>
<style>
  :root {{
    --bg: #0d1117; --panel: #161b22; --line: #2d333b;
    --text: #e6edf3; --dim: #8b949e; --accent: #3fb68b; --danger: #f85149;
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--bg); color: var(--text);
         font: 15px/1.5 "Segoe UI", system-ui, sans-serif; padding: 32px; }}
  h1 {{ font-size: 20px; letter-spacing: .04em; margin-bottom: 4px; }}
  h1 span {{ color: var(--accent); }}
  .sub {{ color: var(--dim); font-size: 13px; margin-bottom: 28px; }}
  .grid {{ display: grid; grid-template-columns: 360px 1fr; gap: 24px; align-items: start; }}
  .panel {{ background: var(--panel); border: 1px solid var(--line);
            border-radius: 8px; padding: 20px; }}
  .panel h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .1em;
               color: var(--dim); margin-bottom: 16px; }}
  label {{ display: block; font-size: 12px; color: var(--dim); margin: 12px 0 4px; }}
  input, select {{ width: 100%; padding: 8px 10px; background: var(--bg);
    border: 1px solid var(--line); border-radius: 6px; color: var(--text); font-size: 14px; }}
  button {{ margin-top: 16px; width: 100%; padding: 10px; border: 0; border-radius: 6px;
    background: var(--accent); color: #06251b; font-weight: 600; font-size: 14px; cursor: pointer; }}
  button.secondary {{ background: var(--line); color: var(--text); }}
  .quote-box {{ margin-top: 16px; padding: 12px; border: 1px dashed var(--line);
    border-radius: 6px; font-family: ui-monospace, monospace; font-size: 13px;
    white-space: pre-wrap; color: var(--dim); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }}
  th {{ color: var(--dim); font-weight: 500; text-transform: uppercase;
        font-size: 11px; letter-spacing: .08em; }}
  td.mono {{ font-family: ui-monospace, monospace; }}
  .badge {{ padding: 2px 8px; border-radius: 10px; font-size: 11px; }}
  .badge.pending    {{ background: #3a2d10; color: #d29922; }}
  .badge.processing {{ background: #10304a; color: #58a6ff; }}
  .badge.completed  {{ background: #12351f; color: #3fb950; }}
  .badge.failed, .badge.reversed {{ background: #3a1214; color: var(--danger); }}
  .error {{ color: var(--danger); font-size: 13px; margin-top: 10px; min-height: 18px; }}
</style>
</head>
<body>
  <h1>PayFlow <span>Console</span></h1>
  <p class="sub">Sandbox payments gateway &middot; not real money &middot; chaos: {chaos_flag}</p>
  <div class="grid">
    <section class="panel" aria-label="New payment">
      <h2>New payment</h2>
      <label for="{id_customer}">Customer</label>
      <select id="{id_customer}" data-testid="{t_customer}" aria-label="Customer"></select>
      <label for="{id_sell}">Sell currency</label>
      <select id="{id_sell}" data-testid="{t_sell}" aria-label="Sell currency">
        <option>EUR</option><option>GBP</option><option>USD</option><option>USDC</option>
      </select>
      <label for="{id_buy}">Buy currency</label>
      <select id="{id_buy}" data-testid="{t_buy}" aria-label="Buy currency">
        <option>USDC</option><option>BTC</option><option>ETH</option><option>EUR</option>
      </select>
      <label for="{id_amount}">Amount</label>
      <input id="{id_amount}" data-testid="{t_amount}" aria-label="Amount"
             type="number" step="0.01" placeholder="250.00">
      <button id="{id_quote_btn}" data-testid="{t_quote_btn}">Get quote</button>
      <div class="quote-box" id="{id_quote_box}" data-testid="{t_quote_box}">No quote yet.</div>
      <button class="secondary" id="{id_pay_btn}" data-testid="{t_pay_btn}" disabled>
        Create payment</button>
      <p class="error" id="{id_error}" data-testid="{t_error}" role="alert"></p>
    </section>
    <section class="panel" aria-label="Payments">
      <h2>Payments</h2>
      <table id="{id_table}" data-testid="{t_table}">
        <thead><tr>
          <th>ID</th><th>Customer</th><th>Pair</th><th>In</th><th>Fee</th>
          <th>Out</th><th>Status</th>
        </tr></thead>
        <tbody id="{id_tbody}"></tbody>
      </table>
    </section>
  </div>
<script>
  const $ = (id) => document.getElementById(id);
  const IDS = {ids_json};
  let currentQuote = null;

  async function loadCustomers() {{
    const res = await fetch('/api/customers');
    const list = await res.json();
    $(IDS.customer).innerHTML = list.map(
      c => `<option value="${{c.id}}">${{c.name}} (${{c.kyc_status}})</option>`).join('');
  }}

  async function getQuote() {{
    $(IDS.error).textContent = '';
    const body = {{
      sell_currency: $(IDS.sell).value,
      buy_currency: $(IDS.buy).value,
      amount: parseFloat($(IDS.amount).value)
    }};
    const res = await fetch('/api/quotes', {{ method: 'POST',
      headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(body) }});
    const data = await res.json();
    if (!res.ok) {{ $(IDS.error).textContent = data.detail || 'quote failed'; return; }}
    currentQuote = data;
    $(IDS.quoteBox).textContent =
      `quote ${{data.id}}\\nrate ${{data.rate}}  fee ${{data.fee}} ${{data.sell_currency}}` +
      `\\nyou receive ${{data.amount_out}} ${{data.buy_currency}}` +
      `\\nexpires ${{data.expires_at}}`;
    $(IDS.payBtn).disabled = false;
  }}

  async function createPayment() {{
    $(IDS.error).textContent = '';
    const res = await fetch('/api/payments', {{ method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ quote_id: currentQuote.id,
        customer_id: $(IDS.customer).value,
        idempotency_key: crypto.randomUUID() }}) }});
    const data = await res.json();
    if (!res.ok) {{ $(IDS.error).textContent = data.detail || 'payment failed'; return; }}
    $(IDS.payBtn).disabled = true;
    $(IDS.quoteBox).textContent = 'No quote yet.';
    refresh();
  }}

  async function refresh() {{
    const res = await fetch('/api/payments?limit=20');
    const data = await res.json();
    $(IDS.tbody).innerHTML = data.items.map(p => `
      <tr data-testid="{t_row}">
        <td class="mono">${{p.id}}</td><td>${{p.customer_id}}</td>
        <td class="mono">${{p.sell_currency}}&rarr;${{p.buy_currency}}</td>
        <td class="mono">${{p.amount_in.toFixed(2)}}</td>
        <td class="mono">${{p.fee.toFixed(2)}}</td>
        <td class="mono">${{p.amount_out.toFixed(2)}}</td>
        <td><span class="badge ${{p.status}}">${{p.status}}</span></td>
      </tr>`).join('');
  }}

  $(IDS.quoteBtn).addEventListener('click', getQuote);
  $(IDS.payBtn).addEventListener('click', createPayment);
  loadCustomers(); refresh(); setInterval(refresh, 2500);
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def console(request: Request, chaos: Optional[int] = None):
    chaos_on = CHAOS if chaos is None else bool(chaos)
    ids = {
        "customer": sel("customer-select", chaos_on),
        "sell": sel("sell-currency", chaos_on),
        "buy": sel("buy-currency", chaos_on),
        "amount": sel("amount-input", chaos_on),
        "quoteBtn": sel("get-quote-btn", chaos_on),
        "quoteBox": sel("quote-box", chaos_on),
        "payBtn": sel("create-payment-btn", chaos_on),
        "error": sel("error-msg", chaos_on),
        "table": sel("payments-table", chaos_on),
        "tbody": sel("payments-tbody", chaos_on),
    }
    import json as _json
    return PAGE.format(
        chaos_flag="on" if chaos_on else "off",
        ids_json=_json.dumps(ids),
        id_customer=ids["customer"], t_customer=sel("customer", chaos_on),
        id_sell=ids["sell"], t_sell=sel("sell-currency", chaos_on),
        id_buy=ids["buy"], t_buy=sel("buy-currency", chaos_on),
        id_amount=ids["amount"], t_amount=sel("amount", chaos_on),
        id_quote_btn=ids["quoteBtn"], t_quote_btn=sel("get-quote", chaos_on),
        id_quote_box=ids["quoteBox"], t_quote_box=sel("quote-result", chaos_on),
        id_pay_btn=ids["payBtn"], t_pay_btn=sel("create-payment", chaos_on),
        id_error=ids["error"], t_error=sel("error", chaos_on),
        id_table=ids["table"], t_table=sel("payments", chaos_on),
        id_tbody=ids["tbody"],
        t_row=sel("payment-row", chaos_on),
    )
