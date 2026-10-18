"""Platform-independent business metrics.

Everything here reads only the unified schema (NormalizedData), so it works the same whether the data
came from Zoho Books, NetSuite, Odoo, or any mix: a metric simply reports "not enough data" when the
connected sources don't have the records it needs. Nothing is estimated or defaulted: a metric either
has a value computed from the records, or it explains what is missing.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median
from typing import Optional

from schema import NormalizedData

RECENT_DAYS = 90        # window for turnover-style metrics (DSO / DPO)
YEAR_DAYS = 365         # window for behaviour/concentration metrics
TREND_MONTHS = 6
AGING_BUCKETS = [("Not yet due", None, 0), ("1-30 days", 1, 30), ("31-60 days", 31, 60),
                 ("61-90 days", 61, 90), ("Over 90 days", 91, None)]


def _metric(id: str, title: str, unit: str, value: Optional[float], description: str, formula: str, *,
            reason: Optional[str] = None, change_pct: Optional[float] = None, note: Optional[str] = None,
            detail: Optional[dict] = None) -> dict:
    return {"id": id, "title": title, "unit": unit, "available": value is not None, "value": value,
            "change_pct": change_pct, "description": description, "formula": formula,
            "note": note, "reason": None if value is not None else reason, "detail": detail or {}}


def _age(d: date, today: date) -> int:
    return (today - d).days


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _shift_month(d: date, delta: int) -> date:
    m = d.month - 1 + delta
    return date(d.year + m // 12, m % 12 + 1, 1)


def _pct_change(new: float, old: float) -> Optional[float]:
    return None if old <= 0 else round((new - old) / old * 100, 1)


def compute(data: NormalizedData, today: Optional[date] = None) -> list[dict]:
    today = today or date.today()
    invoices = [i for i in data.invoices if i.status != "void"]
    bills = [b for b in data.bills if b.status != "void"]
    open_recv = sum(i.outstanding for i in invoices)
    open_pay = sum(b.outstanding for b in bills)

    metrics = [
        _dso(invoices, open_recv, today),
        _dpo(bills, open_pay, today),
        _on_time(invoices, today),
        _days_to_get_paid(invoices, today),
        _aging(invoices, open_recv, today),
        _concentration("customer_concentration", "Customer concentration", invoices, "customer", "invoices", "revenue", today),
        _concentration("vendor_concentration", "Vendor concentration", bills, "vendor", "bills", "spend", today),
        _trend("revenue_trend", "Monthly revenue", invoices, "invoices", "invoiced", today),
        _trend("spend_trend", "Monthly spend", bills, "bills", "billed", today),
        _liquidity(data, open_recv, open_pay),
    ]

    by_id = {m["id"]: m for m in metrics}
    dso, dpo = by_id["dso"], by_id["dpo"]
    if dso["available"] and dpo["available"]:
        gap = round(dso["value"] - dpo["value"], 1)
        text = (f"Customers take {dso['value']:.0f} days to pay you, while you pay vendors in {dpo['value']:.0f} days"
                + (f": your cash is tied up {gap:.0f} days longer than your suppliers' is." if gap > 0
                   else ": you collect faster than you pay." if gap < 0 else "."))
        dso["note"] = dpo["note"] = text
    return metrics


def _dso(invoices, open_recv, today) -> dict:
    invoiced = sum(i.amount for i in invoices if 0 <= _age(i.issue_date, today) < RECENT_DAYS)
    kw = dict(description="Average days it takes customers to pay you. Lower means cash arrives sooner.",
              formula=f"open receivables / (invoiced in the last {RECENT_DAYS} days / {RECENT_DAYS})")
    if invoiced <= 0:
        return _metric("dso", "Days sales outstanding", "days", None, reason=f"No invoices issued in the last {RECENT_DAYS} days.", **kw)
    return _metric("dso", "Days sales outstanding", "days", round(open_recv / (invoiced / RECENT_DAYS), 1),
                   detail={"open_receivables": round(open_recv, 2), "invoiced_recent": round(invoiced, 2)}, **kw)


def _dpo(bills, open_pay, today) -> dict:
    billed = sum(b.amount for b in bills if 0 <= _age(b.issue_date, today) < RECENT_DAYS)
    kw = dict(description="Average days you take to pay vendors. Higher keeps cash longer, but too high strains suppliers.",
              formula=f"open payables / (billed in the last {RECENT_DAYS} days / {RECENT_DAYS})")
    if billed <= 0:
        return _metric("dpo", "Days payables outstanding", "days", None, reason=f"No bills in the last {RECENT_DAYS} days.", **kw)
    return _metric("dpo", "Days payables outstanding", "days", round(open_pay / (billed / RECENT_DAYS), 1),
                   detail={"open_payables": round(open_pay, 2), "billed_recent": round(billed, 2)}, **kw)


def _paid_recently(invoices, today):
    return [i for i in invoices if i.status == "paid" and i.paid_date and 0 <= _age(i.paid_date, today) <= YEAR_DAYS]


def _on_time(invoices, today) -> dict:
    paid = _paid_recently(invoices, today)
    kw = dict(description="Share of invoices customers paid on or before the due date.",
              formula="paid on/before due date / all invoices paid in the last 12 months")
    if not paid:
        return _metric("on_time_rate", "On-time payment rate", "percent", None,
                       reason="No paid invoices with a payment date in the last 12 months.", **kw)
    late = [(i.paid_date - i.due_date).days for i in paid if i.paid_date > i.due_date]
    return _metric("on_time_rate", "On-time payment rate", "percent", round((len(paid) - len(late)) / len(paid) * 100, 1),
                   detail={"paid_invoices": len(paid), "on_time": len(paid) - len(late), "late": len(late),
                           "avg_days_late": round(mean(late), 1) if late else 0}, **kw)


def _days_to_get_paid(invoices, today) -> dict:
    paid = _paid_recently(invoices, today)
    kw = dict(description="Average days from sending an invoice to receiving the money.",
              formula="mean(payment date - issue date) over invoices paid in the last 12 months")
    if not paid:
        return _metric("days_to_paid", "Average days to get paid", "days", None,
                       reason="No paid invoices with a payment date in the last 12 months.", **kw)
    days = [max((i.paid_date - i.issue_date).days, 0) for i in paid]
    return _metric("days_to_paid", "Average days to get paid", "days", round(mean(days), 1),
                   detail={"median": median(days), "fastest": min(days), "slowest": max(days), "paid_invoices": len(days)}, **kw)


def _aging(invoices, open_recv, today) -> dict:
    kw = dict(description="How much of what customers owe is already past its due date, and for how long.",
              formula="overdue receivables / open receivables, bucketed by days past due")
    if not invoices:
        return _metric("receivables_aging", "Overdue receivables", "percent", None, reason="No invoices found.", **kw)
    buckets = []
    for label, lo, hi in AGING_BUCKETS:
        rows = [i for i in invoices if i.outstanding > 0 and
                (lo is None or _age(i.due_date, today) >= lo) and (hi is None or _age(i.due_date, today) <= hi)]
        buckets.append({"label": label, "amount": round(sum(i.outstanding for i in rows), 2), "count": len(rows)})
    overdue = sum(b["amount"] for b in buckets[1:])
    return _metric("receivables_aging", "Overdue receivables", "percent",
                   round(overdue / open_recv * 100, 1) if open_recv > 0 else 0.0,
                   detail={"buckets": buckets, "overdue_amount": round(overdue, 2), "open_receivables": round(open_recv, 2)}, **kw)


def _concentration(id, title, docs, party_attr, noun, flow, today) -> dict:
    kw = dict(description=f"How dependent you are on a few {party_attr}s. A high top share means losing one hurts.",
              formula=f"largest {party_attr}'s share of {noun} issued in the last 12 months")
    totals: dict[str, float] = defaultdict(float)
    for d in docs:
        if 0 <= _age(d.issue_date, today) <= YEAR_DAYS:
            totals[getattr(d, party_attr)] += d.amount
    total = sum(totals.values())
    if total <= 0:
        return _metric(id, title, "percent", None, reason=f"No {noun} in the last 12 months.", **kw)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    top = [{"name": n, "amount": round(a, 2), "share": round(a / total * 100, 1)} for n, a in ranked[:5]]
    return _metric(id, title, "percent", top[0]["share"],
                   note=f"Top 3 account for {sum(t['share'] for t in top[:3]):.0f}% of {flow}." if len(ranked) > 1 else f"All {flow} is with one {party_attr}.",
                   detail={"top": top, "parties": len(ranked), "total": round(total, 2)}, **kw)


def _trend(id, title, docs, noun, verb, today) -> dict:
    kw = dict(description=f"Amount {verb} per calendar month over the last {TREND_MONTHS} months, and how the last full month compares with the one before.",
              formula=f"sum of {noun} by issue month; change = last full month vs the month before")
    this_month = _month_start(today)
    months = [_shift_month(this_month, -k) for k in range(TREND_MONTHS - 1, -1, -1)]
    amounts = {m: 0.0 for m in months}
    for d in docs:
        key = _month_start(d.issue_date)
        if key in amounts:
            amounts[key] += d.amount
    if not any(amounts.values()):
        return _metric(id, title, "currency", None, reason=f"No {noun} in the last {TREND_MONTHS} months.", **kw)
    series = [{"month": m.strftime("%Y-%m"), "label": m.strftime("%b"), "amount": round(amounts[m], 2), "partial": m == this_month}
              for m in months]
    last_full, prev_full = series[-2], series[-3] if len(series) > 2 else None
    note = f"{last_full['label']} is the last full month; {series[-1]['label']} is still in progress."
    if last_full["amount"] == 0 and docs:
        note += f" The most recent entry in your records is dated {max(d.issue_date for d in docs).isoformat()}."
    return _metric(id, title, "currency", last_full["amount"],
                   change_pct=_pct_change(last_full["amount"], prev_full["amount"]) if prev_full else None,
                   note=note,
                   detail={"series": series, "average_per_month": round(mean(s["amount"] for s in series[:-1]), 2)}, **kw)


def _liquidity(data, open_recv, open_pay) -> dict:
    kw = dict(description="Can cash plus money owed to you cover what you owe? Below 1 means short-term obligations exceed liquid assets.",
              formula="(cash balance + open receivables) / open payables")
    if open_pay <= 0:
        return _metric("liquidity_ratio", "Liquidity ratio", "ratio", None, reason="No open payables to cover.", **kw)
    return _metric("liquidity_ratio", "Liquidity ratio", "ratio", round((data.current_balance + open_recv) / open_pay, 2),
                   note=None if data.cash_known else "No bank balance was found, so this counts receivables only.",
                   detail={"cash": round(data.current_balance, 2), "open_receivables": round(open_recv, 2),
                           "open_payables": round(open_pay, 2),
                           "cash_coverage": round(data.current_balance / open_pay, 2)}, **kw)
