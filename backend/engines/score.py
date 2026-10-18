"""Financial health score, 0-100, with every deduction itemised."""
from __future__ import annotations

from datetime import date
from typing import Optional

from schema import Finding, NormalizedData


def overdue_ratio(data: NormalizedData, today: date) -> float:
    open_total = sum(i.outstanding for i in data.invoices)
    if open_total <= 0:
        return 0.0
    return sum(i.outstanding for i in data.invoices if i.due_date < today) / open_total


def compute(data: NormalizedData, runway_day: Optional[int], findings: list[Finding],
            today: Optional[date] = None) -> dict:
    today = today or date.today()
    deductions: list[dict] = []

    def deduct(points: int, reason: str) -> None:
        if points:
            deductions.append({"points": -points, "reason": reason})

    if runway_day is not None:
        if runway_day < 30:
            deduct(35, f"Cash runs out in {runway_day} days (under 30)")
        elif runway_day < 60:
            deduct(20, f"Cash runs out in {runway_day} days (under 60)")
        else:
            deduct(10, f"Cash runs out in {runway_day} days (under 90)")

    # The cash shortfall is already penalised through the runway above; don't count it twice.
    scored = [f for f in findings if f.type != "cash_shortfall"]
    high = sum(1 for f in scored if f.severity == "high")
    medium = sum(1 for f in scored if f.severity == "medium")
    if high:
        deduct(min(high * 6, 30), f"{high} high-severity finding(s)")
    if medium:
        deduct(min(medium * 2, 10), f"{medium} medium-severity finding(s)")

    ratio = overdue_ratio(data, today)
    if ratio > 0.4:
        deduct(15, f"{ratio:.0%} of receivables are overdue")
    elif ratio > 0.2:
        deduct(8, f"{ratio:.0%} of receivables are overdue")

    score = max(0, min(100, 100 + sum(d["points"] for d in deductions)))
    label = "Healthy" if score >= 80 else "Watch" if score >= 60 else "At risk"
    return {"score": score, "label": label, "deductions": deductions, "overdue_ratio": round(ratio, 3)}
