"""FinSafe AI backend.  Run:  uvicorn main:app --reload --port 8000   (single worker: state is in-memory)"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

import analysis as analysis_engine
import config
from ai import gemini
from connectors.base import ConnectorError
from connectors.zoho import ZohoConnector
from engines import cash, insights, late_pay, whatif as whatif_engine
from schema import (ChatRequest, ConnectRequest, CustomerDefaultRequest, DraftEmailRequest, EarlyDiscountRequest,
                    NormalizedData, PaymentTermsRequest, WhatIfRequest)
from store import store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("finsafe")

app = FastAPI(title="FinSafe AI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def _connector(cid: str):
    if cid not in store.connectors:
        raise HTTPException(404, f"Unknown connector '{cid}'")
    return store.connectors[cid]


def _active() -> NormalizedData:
    data = store.active()
    if data is None:
        raise HTTPException(409, "No data yet - connect a platform first.")
    return data


def _sync(cid: str) -> dict:
    """Pull + normalize from one connector and record the result (or the failure)."""
    try:
        data = _connector(cid).fetch()
    except ConnectorError as e:
        store.set_error(cid, str(e))
        raise HTTPException(e.status_code, str(e))
    except Exception as e:
        log.exception("sync failed for %s", cid)
        store.set_error(cid, f"Unexpected error while syncing: {e}")
        raise HTTPException(502, f"Unexpected error while syncing {cid}: {e}")
    store.set_synced(cid, data)
    store.explain_cache.clear()
    return {"id": cid, "status": "connected", "records_synced": data.record_count,
            "last_synced": store.synced_at[cid].isoformat(), "warnings": data.warnings}


@app.get("/health")
def health():
    return {"ok": True, "gemini": gemini.enabled()}


# ── connectors ──
@app.get("/api/connectors")
def list_connectors():
    return store.describe()


@app.post("/api/connectors/{cid}/connect")
def connect(cid: str, body: ConnectRequest | None = None):
    connector = _connector(cid)
    try:
        outcome = connector.connect(body or ConnectRequest())
    except ConnectorError as e:
        store.set_error(cid, str(e))
        raise HTTPException(e.status_code, str(e))
    except Exception as e:
        log.exception("connect failed for %s", cid)
        store.set_error(cid, f"Could not connect: {e}")
        raise HTTPException(502, f"Could not connect to {connector.name}: {e}")
    if outcome.status == "redirect":
        # Browser leaves for the provider's login; nothing is "connecting" server-side until it returns.
        return {"id": cid, "status": "connecting", "redirect_url": outcome.redirect_url}
    return _sync(cid)


@app.post("/api/connectors/{cid}/sync")
def sync(cid: str):
    if store.status.get(cid) != "connected":
        raise HTTPException(409, f"{_connector(cid).name} isn't connected.")
    return _sync(cid)


@app.api_route("/api/connectors/{cid}/disconnect", methods=["POST", "DELETE"])
def disconnect(cid: str):
    _connector(cid)
    store.clear(cid)
    store.explain_cache.clear()
    return {"id": cid, "status": "disconnected"}


@app.get("/auth/zoho/callback")
def zoho_callback(code: str = "", state: str = "", error: str = "", accounts_server: str = Query("", alias="accounts-server")):
    """Browser lands here after Zoho's consent screen; we finish the exchange then bounce back to the UI."""
    def back(**params):
        return RedirectResponse(f"{config.FRONTEND_URL}/integrations?{urlencode(params)}")

    zoho: ZohoConnector = store.connectors["zoho"]  # type: ignore[assignment]
    try:
        if error or not code:
            raise ConnectorError(f"Zoho authorization failed: {error or 'no code returned'}")
        zoho.exchange_code(code, state, accounts_server)
        _sync("zoho")
    except (ConnectorError, HTTPException) as e:
        message = e.detail if isinstance(e, HTTPException) else str(e)
        if store.status["zoho"] != "connected":   # a stray/forged callback must not knock out a live connection
            store.set_error("zoho", message)
        return back(error=message, connector="zoho")
    return back(connected="zoho")


# ── analysis ──
@app.get("/api/analysis")
def get_analysis(explain: bool = Query(True, description="Add AI explanations (cached); false = instant, numbers only")):
    result = analysis_engine.run(_active())
    result["ai_enabled"] = gemini.enabled()
    result["explained"] = False
    if explain and result["findings"]:
        key = hashlib.sha256(json.dumps([result["findings"], result["currency"]], sort_keys=True, default=str).encode()).hexdigest()
        if key not in store.explain_cache:
            store.explain_cache[key] = gemini.explain(result["findings"], result["summary"], result["currency"])
        notes, ai_generated = store.explain_cache[key]
        for f in result["findings"]:
            f.update(notes.get(f["id"], {}))
        result["explained"], result["ai_generated"] = True, ai_generated
    return result


@app.get("/api/insights")
def get_insights():
    """Platform-independent business metrics from whatever is connected."""
    data = _active()
    metrics = insights.compute(data)
    return {"as_of": date.today().isoformat(), "company": data.company, "currency": data.currency,
            "sources": [cid for cid in store.datasets], "metrics": metrics,
            "available_count": sum(1 for m in metrics if m["available"])}


@app.post("/api/whatif")
def whatif(body: WhatIfRequest):
    data = _active()
    if not data.cash_known:
        raise HTTPException(409, "No bank balance was found in the connected data, so cash scenarios can't be simulated.")
    open_invoices = {i.id: i for i in data.invoices if i.outstanding > 0}
    open_bills = {b.id: b for b in data.bills if b.outstanding > 0}
    unknown = [x for x in body.collect_invoice_ids if x not in open_invoices] + \
              [x for x in body.delay_bill_ids if x not in open_bills]
    if unknown:
        raise HTTPException(422, f"Not open invoices/bills in the current data: {', '.join(unknown)}")

    before = cash.project(data)
    after = cash.project(data, collect_invoice_ids=set(body.collect_invoice_ids),
                         delay_bill_ids=set(body.delay_bill_ids), delay_days=body.delay_days)
    b, a = before["runway_day"], after["runway_day"]
    return {
        "before": before, "after": after,
        "runway_before": b, "runway_after": a,
        "runway_gain_days": (a - b) if (a is not None and b is not None) else None,
        "no_longer_runs_out": b is not None and a is None,
        "cash_collected": round(sum(open_invoices[i].outstanding for i in body.collect_invoice_ids), 2),
        "payments_delayed": round(sum(open_bills[i].outstanding for i in body.delay_bill_ids), 2),
        "lowest_balance_change": round(after["min_balance"] - before["min_balance"], 2),
    }


def _run_tool(fn, *args):
    try:
        return fn(_active(), *args)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/whatif/options")
def whatif_options():
    """Open invoices, bills and customers the scenario tools can work on."""
    return whatif_engine.options(_active())


@app.post("/api/whatif/early-discount")
def whatif_early_discount(body: EarlyDiscountRequest):
    return _run_tool(whatif_engine.early_discount, body.invoice_ids, body.discount_pct, body.pay_within_days)


@app.post("/api/whatif/payment-terms")
def whatif_payment_terms(body: PaymentTermsRequest):
    return _run_tool(whatif_engine.payment_terms, body.collect_days_faster, body.pay_days_later)


@app.post("/api/whatif/customer-default")
def whatif_customer_default(body: CustomerDefaultRequest):
    return _run_tool(whatif_engine.customer_default, body.customers)


@app.post("/api/draft-email")
def draft_email(body: DraftEmailRequest):
    data = _active()
    inv = next((i for i in data.invoices if i.id == body.invoice_id and i.outstanding > 0), None)
    if not inv:
        raise HTTPException(404, f"No open invoice '{body.invoice_id}'")
    stats = next((r for r in late_pay.rank(data) if r["customer"] == inv.customer), None)
    invoice = {"id": inv.id, "customer": inv.customer, "outstanding": inv.outstanding,
               "due_date": inv.due_date.isoformat(), "days_overdue": max((date.today() - inv.due_date).days, 0)}
    draft = gemini.draft_email(invoice, stats, data.company or "Our team", data.currency)
    # Drafts only: nothing is ever sent from here.
    return {**draft, "invoice": invoice, "customer_stats": stats, "sent": False}


@app.post("/api/chat")
def chat(body: ChatRequest):
    if not body.question.strip():
        raise HTTPException(422, "Ask a question.")
    data = _active()
    result = analysis_engine.run(data)
    result["runway"] = {k: v for k, v in result["runway"].items() if k != "series"}   # keep the prompt small
    return gemini.chat(body.question.strip(), result)
