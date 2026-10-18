"""Cash runway: deterministic 90-day day-by-day projection."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Optional

from schema import NormalizedData

HORIZON_DAYS = 90
OVERDUE_RECEIPT_GRACE = 7   # an already-overdue invoice is assumed to land this many days out
OPEX_LOOKBACK = 90


def customer_avg_delay(data: NormalizedData) -> dict[str, float]:
    """Average days past due at payment, per customer (paid invoices only, never negative)."""
    delays: dict[str, list[int]] = defaultdict(list)
    for inv in data.invoices:
        if inv.status == "paid" and inv.paid_date:
            delays[inv.customer].append(max((inv.paid_date - inv.due_date).days, 0))
    return {c: mean(v) for c, v in delays.items()}


def daily_operating_spend(data: NormalizedData, today: date) -> float:
    """Average daily spend from bills paid in the last 90 days.

    Vendors that already have an open bill are excluded: that bill is projected
    explicitly, so counting their history too would double-count the same spend."""
    vendors_with_open = {b.vendor for b in data.bills if b.status == "open"}
    since = today - timedelta(days=OPEX_LOOKBACK)
    total = sum(b.amount for b in data.bills
                if b.status == "paid" and b.paid_date and b.paid_date >= since and b.vendor not in vendors_with_open)
    return total / OPEX_LOOKBACK


def expected_receipt_day(inv, today: date, delays: dict[str, float]) -> int:
    """Days from today until this invoice is expected to be paid: due date plus the customer's usual delay;
    already-late invoices are assumed to land OVERDUE_RECEIPT_GRACE days out."""
    expected = inv.due_date + timedelta(days=round(delays.get(inv.customer, 0)))
    if expected < today:
        expected = today + timedelta(days=OVERDUE_RECEIPT_GRACE)
    return (expected - today).days


def project(data: NormalizedData, today: Optional[date] = None,
            collect_invoice_ids: Optional[set[str]] = None,
            delay_bill_ids: Optional[set[str]] = None, delay_days: int = 30,
            exclude_invoice_ids: Optional[set[str]] = None,
            invoice_overrides: Optional[dict[str, tuple[int, float]]] = None,
            receipt_shift_days: int = 0, bill_shift_days: int = 0) -> dict:
    """90-day projection. Scenario knobs (all default to "no change"):
      collect_invoice_ids   invoices received today
      delay_bill_ids        bills pushed delay_days past their due date
      exclude_invoice_ids   invoices that are never paid
      invoice_overrides     {invoice id: (day received, share of the amount received)}
      receipt_shift_days    every expected receipt arrives this many days sooner
      bill_shift_days       every bill is paid this many days later"""
    today = today or date.today()
    collect_invoice_ids = collect_invoice_ids or set()
    delay_bill_ids = delay_bill_ids or set()
    exclude_invoice_ids = exclude_invoice_ids or set()
    invoice_overrides = invoice_overrides or {}
    delays = customer_avg_delay(data)
    opex = daily_operating_spend(data, today)

    inflow: dict[int, float] = defaultdict(float)
    outflow: dict[int, float] = defaultdict(float)
    events: list[dict] = []

    for inv in data.invoices:
        if inv.outstanding <= 0 or inv.id in exclude_invoice_ids:
            continue
        amount = inv.outstanding
        if inv.id in invoice_overrides:
            day, share = invoice_overrides[inv.id]
            amount *= share
        elif inv.id in collect_invoice_ids:
            day = 0
        else:
            day = max(expected_receipt_day(inv, today, delays) - receipt_shift_days, 0)
        if day <= HORIZON_DAYS:
            inflow[day] += amount
            events.append({"kind": "invoice", "id": inv.id, "party": inv.customer, "amount": amount,
                           "date": (today + timedelta(days=day)).isoformat(), "day": day})

    for bill in data.bills:
        if bill.outstanding <= 0:
            continue
        due = bill.due_date
        if bill.id in delay_bill_ids:
            due = max(due, today) + timedelta(days=delay_days)
        if due < today:
            due = today + timedelta(days=1)   # overdue payable: assume it goes out immediately
        due += timedelta(days=bill_shift_days)
        day = (due - today).days
        if day <= HORIZON_DAYS:
            outflow[day] += bill.outstanding
            events.append({"kind": "bill", "id": bill.id, "party": bill.vendor,
                           "amount": -bill.outstanding, "date": due.isoformat(), "day": day})
    balance = data.current_balance
    series, runway_day = [], None
    for d in range(HORIZON_DAYS + 1):
        if d > 0:
            balance += inflow[d] - outflow[d] - opex
        elif inflow[0] or outflow[0]:
            balance += inflow[0] - outflow[0]
        balance = round(balance, 2)
        series.append({"day": d, "date": (today + timedelta(days=d)).isoformat(), "balance": balance})
        if runway_day is None and balance < 0:
            runway_day = d

    drivers = sorted(events, key=lambda e: abs(e["amount"]), reverse=True)[:6]
    at = lambda d: series[min(d, HORIZON_DAYS)]["balance"]
    return {
        "starting_balance": data.current_balance,
        "daily_operating_spend": round(opex, 2),
        "runway_day": runway_day,                   # None => stays positive for the whole horizon
        "horizon_days": HORIZON_DAYS,
        "balance_30": at(30), "balance_60": at(60), "balance_90": at(90),
        "min_balance": min(p["balance"] for p in series),
        "series": series,
        "drivers": drivers,
    }
