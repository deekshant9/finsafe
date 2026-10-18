"""Vendor price spike: latest bill vs. the median of that vendor's earlier bills."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from schema import Finding, NormalizedData

SPIKE_THRESHOLD = 0.25
HIGH_THRESHOLD = 0.40
MIN_HISTORY = 2   # earlier bills needed before a "latest" can be judged


def detect(data: NormalizedData) -> list[Finding]:
    by_vendor = defaultdict(list)
    for b in data.bills:
        if b.status != "void" and b.amount > 0:
            by_vendor[b.vendor].append(b)

    out: list[Finding] = []
    for vendor, bills in by_vendor.items():
        bills.sort(key=lambda b: (b.issue_date, b.id))
        *history, latest = bills
        if len(history) < MIN_HISTORY:
            continue
        base = median(b.amount for b in history)
        if base <= 0:
            continue
        pct = (latest.amount - base) / base
        if pct <= SPIKE_THRESHOLD:
            continue
        closest = min(history, key=lambda b: abs(b.amount - base))
        out.append(Finding(
            id="", type="vendor_price_spike", severity="high" if pct >= HIGH_THRESHOLD else "medium",
            title=f"{vendor}: bill is {pct:.0%} above normal",
            amount_at_risk=round(latest.amount - base, 2),
            evidence=[latest.id, closest.id],
            detail={"vendor": vendor, "latest_amount": latest.amount, "median_previous": round(base, 2),
                    "increase_pct": round(pct * 100, 1), "bills_compared": len(history),
                    "latest_issue_date": latest.issue_date.isoformat(), "source": latest.source}))
    return out
