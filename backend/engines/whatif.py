"""Finance scenario tools. Everything is derived from the unified invoices/bills, so any connected platform works.

Every tool takes the user's own inputs (nothing is preset) and returns computed results; invalid input raises
ValueError, which the API turns into a 422.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from engines import cash, insights
from schema import NormalizedData

DAYS_PER_YEAR = 365
OPTION_LIMIT = 50          # most invoices/bills offered as choices (largest first)
CUSTOMER_LIMIT = 30


def _open_invoices(data: NormalizedData):
    return [i for i in data.invoices if i.outstanding > 0]


def options(data: NormalizedData, today: Optional[date] = None) -> dict:
    """What the user can pick from in the tools: open invoices, open bills, customers with open balances."""
    today = today or date.today()
    invoices = sorted(_open_invoices(data), key=lambda i: i.outstanding, reverse=True)
    bills = sorted((b for b in data.bills if b.outstanding > 0), key=lambda b: b.outstanding, reverse=True)
    totals: dict[str, list] = defaultdict(lambda: [0.0, 0])
    for i in invoices:
        totals[i.customer][0] += i.outstanding
        totals[i.customer][1] += 1
    customers = sorted(({"name": n, "outstanding": round(v[0], 2), "invoice_count": v[1]} for n, v in totals.items()),
                       key=lambda c: c["outstanding"], reverse=True)
    return {
        "currency": data.currency, "cash_known": data.cash_known,
        "invoices": [{"id": i.id, "customer": i.customer, "outstanding": round(i.outstanding, 2),
                      "due_date": i.due_date.isoformat(), "days_overdue": max((today - i.due_date).days, 0)}
                     for i in invoices[:OPTION_LIMIT]],
        "bills": [{"id": b.id, "vendor": b.vendor, "outstanding": round(b.outstanding, 2),
                   "due_date": b.due_date.isoformat(), "days_overdue": max((today - b.due_date).days, 0)}
                  for b in bills[:OPTION_LIMIT]],
        "customers": customers[:CUSTOMER_LIMIT],
        "truncated": len(invoices) > OPTION_LIMIT or len(bills) > OPTION_LIMIT or len(customers) > CUSTOMER_LIMIT,
    }


def _runway_effect(data: NormalizedData, today: date, **scenario) -> Optional[dict]:
    """Runway before vs. after a scenario; None when the cash balance is unknown (no honest projection exists)."""
    if not data.cash_known:
        return None
    before = cash.project(data, today)
    after = cash.project(data, today, **scenario)
    b, a = before["runway_day"], after["runway_day"]
    return {
        "runway_before": b, "runway_after": a,
        "runway_gain_days": (a - b) if (a is not None and b is not None) else None,
        "no_longer_runs_out": b is not None and a is None,
        "newly_runs_out": b is None and a is not None,
        "lowest_balance_before": before["min_balance"], "lowest_balance_after": after["min_balance"],
        "before_series": before["series"], "after_series": after["series"],
    }


# ── 1. Early-payment discount ──
def early_discount(data: NormalizedData, invoice_ids: list[str], discount_pct: float, pay_within_days: int,
                   today: Optional[date] = None) -> dict:
    """Offer a discount to customers who pay within `pay_within_days`. What does it cost, annualized?

    A customer who would have paid sooner anyway still takes the discount, so that is a pure cost and is
    reported separately instead of being hidden."""
    today = today or date.today()
    if not invoice_ids:
        raise ValueError("Select at least one invoice.")
    if not 0 < discount_pct < 100:
        raise ValueError("The discount must be between 0 and 100 percent.")
    if pay_within_days < 0:
        raise ValueError("Days to pay can't be negative.")
    open_by_id = {i.id: i for i in _open_invoices(data)}
    unknown = [x for x in invoice_ids if x not in open_by_id]
    if unknown:
        raise ValueError(f"Not open invoices in the current data: {', '.join(unknown)}")

    d = discount_pct / 100
    delays = cash.customer_avg_delay(data)
    rows, overrides = [], {}
    for inv_id in dict.fromkeys(invoice_ids):
        inv = open_by_id[inv_id]
        baseline = cash.expected_receipt_day(inv, today, delays)
        accelerated = max(baseline - pay_within_days, 0)
        discount = inv.outstanding * d
        rows.append({
            "id": inv.id, "customer": inv.customer, "outstanding": round(inv.outstanding, 2),
            "discount": round(discount, 2), "cash_received": round(inv.outstanding - discount, 2),
            "expected_day_without_offer": baseline, "days_accelerated": accelerated,
            "annualized_cost_pct": round(d / (1 - d) * DAYS_PER_YEAR / accelerated * 100, 1) if accelerated else None,
            "pays_sooner_anyway": accelerated == 0,
        })
        overrides[inv.id] = (min(baseline, pay_within_days), 1 - d)

    sped_up = [r for r in rows if not r["pays_sooner_anyway"]]
    net_sped = sum(r["cash_received"] for r in sped_up)
    disc_sped = sum(r["discount"] for r in sped_up)
    weighted_days = sum(r["cash_received"] * r["days_accelerated"] for r in sped_up) / net_sped if net_sped else None
    return {
        "invoices": rows,
        "total_discount_cost": round(sum(r["discount"] for r in rows), 2),
        "discount_given_for_nothing": round(sum(r["discount"] for r in rows if r["pays_sooner_anyway"]), 2),
        "cash_pulled_forward": round(net_sped, 2),
        "avg_days_accelerated": round(weighted_days, 1) if weighted_days else None,
        # cost of the discount per year of money received early: (discount / cash received) * (365 / days earlier)
        "annualized_cost_pct": round(disc_sped / net_sped * DAYS_PER_YEAR / weighted_days * 100, 1) if net_sped and weighted_days else None,
        "runway": _runway_effect(data, today, invoice_overrides=overrides),
    }


# ── 2. Payment terms ──
def payment_terms(data: NormalizedData, collect_days_faster: int, pay_days_later: int,
                  today: Optional[date] = None) -> dict:
    """Cash freed one-off by collecting `collect_days_faster` sooner and paying vendors `pay_days_later` later."""
    today = today or date.today()
    if collect_days_faster < 0 or pay_days_later < 0:
        raise ValueError("Days can't be negative.")
    if collect_days_faster == 0 and pay_days_later == 0:
        raise ValueError("Enter how many days faster customers pay, or how many days later you pay vendors.")

    window = insights.RECENT_DAYS
    invoiced = sum(i.amount for i in data.invoices if i.status != "void" and 0 <= (today - i.issue_date).days < window)
    billed = sum(b.amount for b in data.bills if b.status != "void" and 0 <= (today - b.issue_date).days < window)
    daily_invoiced, daily_billed = invoiced / window, billed / window
    metrics = {m["id"]: m for m in insights.compute(data, today)}
    dso, dpo = metrics["dso"]["value"], metrics["dpo"]["value"]

    notes = []
    if collect_days_faster and daily_invoiced <= 0:
        notes.append(f"No invoices were issued in the last {window} days, so the collection saving can't be estimated.")
    if pay_days_later and daily_billed <= 0:
        notes.append(f"No bills were received in the last {window} days, so the payment saving can't be estimated.")
    freed_recv = round(daily_invoiced * collect_days_faster, 2) if daily_invoiced > 0 else None
    freed_pay = round(daily_billed * pay_days_later, 2) if daily_billed > 0 else None
    return {
        "basis_days": window, "daily_invoiced": round(daily_invoiced, 2), "daily_billed": round(daily_billed, 2),
        "cash_freed_from_receivables": freed_recv, "cash_freed_from_payables": freed_pay,
        "total_cash_freed": round((freed_recv or 0) + (freed_pay or 0), 2),
        "dso": None if dso is None else {"before": dso, "after": round(max(dso - collect_days_faster, 0), 1)},
        "dpo": None if dpo is None else {"before": dpo, "after": round(dpo + pay_days_later, 1)},
        "notes": notes,
        "runway": _runway_effect(data, today, receipt_shift_days=collect_days_faster, bill_shift_days=pay_days_later),
    }


# ── 3. Customer default stress test ──
def customer_default(data: NormalizedData, customers: list[str], today: Optional[date] = None) -> dict:
    """What if the chosen customers never pay what they currently owe?"""
    today = today or date.today()
    if not customers:
        raise ValueError("Select at least one customer.")
    owed: dict[str, list] = defaultdict(list)
    for inv in _open_invoices(data):
        owed[inv.customer].append(inv)
    unknown = [c for c in customers if c not in owed]
    if unknown:
        raise ValueError(f"No open invoices for: {', '.join(unknown)}")

    chosen = list(dict.fromkeys(customers))
    excluded = {inv.id for c in chosen for inv in owed[c]}
    total_open = sum(i.outstanding for i in _open_invoices(data))
    lost = sum(inv.outstanding for c in chosen for inv in owed[c])
    return {
        "customers": [{"name": c, "outstanding": round(sum(i.outstanding for i in owed[c]), 2), "invoices": len(owed[c]),
                       "overdue": round(sum(i.outstanding for i in owed[c] if i.due_date < today), 2)} for c in chosen],
        "receivables_lost": round(lost, 2),
        "share_of_open_receivables_pct": round(lost / total_open * 100, 1) if total_open else 0.0,
        "open_receivables_after": round(total_open - lost, 2),
        "runway": _runway_effect(data, today, exclude_invoice_ids=excluded),
    }
