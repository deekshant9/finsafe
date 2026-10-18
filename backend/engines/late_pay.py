"""Late-payment risk per customer."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from schema import Finding, NormalizedData

MIN_PAID_HISTORY = 2
LATE_GRACE_DAYS = 5   # paying within a few days of the due date isn't "late" for risk purposes
FINDING_RISK = 0.5


def rank(data: NormalizedData, today: Optional[date] = None) -> list[dict]:
    """risk = 0.6 * late_ratio + 0.4 * min(avg_days_late / 30, 1); highest first."""
    today = today or date.today()
    paid = defaultdict(list)
    open_ = defaultdict(list)
    for inv in data.invoices:
        if inv.status == "paid" and inv.paid_date:
            paid[inv.customer].append(inv)
        elif inv.outstanding > 0:
            open_[inv.customer].append(inv)

    rows = []
    for customer, invoices in paid.items():
        if len(invoices) < MIN_PAID_HISTORY:
            continue
        days_late = [max((i.paid_date - i.due_date).days, 0) for i in invoices]
        late_ratio = sum(1 for d in days_late if d > LATE_GRACE_DAYS) / len(invoices)
        avg_late = sum(days_late) / len(days_late)
        outstanding = sum(i.outstanding for i in open_.get(customer, []))
        rows.append({
            "customer": customer, "risk": round(0.6 * late_ratio + 0.4 * min(avg_late / 30, 1), 3),
            "late_ratio": round(late_ratio, 3), "avg_days_late": round(avg_late, 1),
            "paid_invoices": len(invoices), "outstanding": round(outstanding, 2),
            "open_invoice_ids": [i.id for i in open_.get(customer, [])],
        })
    return sorted(rows, key=lambda r: (r["risk"], r["outstanding"]), reverse=True)


def detect(data: NormalizedData, ranking: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    for r in ranking:
        if r["risk"] < FINDING_RISK or r["outstanding"] <= 0:
            continue
        out.append(Finding(
            id="", type="late_payer", severity="high" if r["risk"] >= 0.7 else "medium",
            title=f"{r['customer']} usually pays ~{r['avg_days_late']:.0f} days late",
            amount_at_risk=r["outstanding"], evidence=r["open_invoice_ids"],
            detail={k: r[k] for k in ("customer", "risk", "late_ratio", "avg_days_late", "paid_invoices", "outstanding")}))
    return out
