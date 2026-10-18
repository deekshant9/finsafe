"""Runs every engine over normalized data and assembles the analysis payload."""
from __future__ import annotations

from datetime import date
from typing import Optional

from engines import cash, duplicates, late_pay, score, vendor_spike
from schema import Finding, NormalizedData

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def run(data: NormalizedData, today: Optional[date] = None) -> dict:
    today = today or date.today()
    runway = cash.project(data, today)
    runway["cash_known"] = data.cash_known
    if not data.cash_known:
        # Unknown cash is not zero cash: don't present a runway, shortfall or balance curve built on it.
        runway.update(runway_day=None, series=[], balance_30=None, balance_60=None, balance_90=None, min_balance=None)
    ranking = late_pay.rank(data, today)

    findings: list[Finding] = (duplicates.detect(data) + vendor_spike.detect(data) + late_pay.detect(data, ranking))
    if runway["runway_day"] is not None:
        top_out = next((d for d in runway["drivers"] if d["kind"] == "bill"), None)
        findings.append(Finding(
            id="", type="cash_shortfall", severity="high" if runway["runway_day"] < 60 else "medium",
            title=f"Cash runs out in {runway['runway_day']} days",
            amount_at_risk=abs(runway["min_balance"]),
            evidence=[d["id"] for d in runway["drivers"][:4]],
            detail={"runway_day": runway["runway_day"], "lowest_balance": runway["min_balance"],
                    "largest_outflow": top_out}))

    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.amount_at_risk))
    for i, f in enumerate(findings, 1):
        f.id = f"f_{i:03d}"

    health = score.compute(data, runway["runway_day"], findings, today)
    return {
        "as_of": today.isoformat(),
        "company": data.company, "currency": data.currency,
        "health": health,
        "runway": runway,
        "findings": [f.model_dump() for f in findings],
        "late_payment_ranking": ranking[:10],
        "summary": {
            "cash_balance": data.current_balance,
            "open_receivables": round(sum(i.outstanding for i in data.invoices), 2),
            "overdue_receivables": round(sum(i.outstanding for i in data.invoices if i.due_date < today), 2),
            "open_payables": round(sum(b.outstanding for b in data.bills), 2),
            "invoice_count": len(data.invoices), "bill_count": len(data.bills),
            "total_amount_at_risk": round(sum(f.amount_at_risk for f in findings if f.type != "cash_shortfall"), 2),
        },
        "warnings": data.warnings,
    }
