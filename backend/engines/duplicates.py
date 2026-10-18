"""Duplicate invoice / bill detection."""
from __future__ import annotations

import re
from itertools import combinations

from schema import Finding, NormalizedData

EXACT_WINDOW_DAYS = 7
FUZZY_WINDOW_DAYS = 14
FUZZY_AMOUNT_TOL = 0.01


def _split_number(doc_id: str) -> tuple[str, int | None]:
    m = re.match(r"^(.*?)(\d+)$", doc_id)
    return (m.group(1), int(m.group(2))) if m else (doc_id, None)


def _similar_numbering(a: str, b: str) -> bool:
    (pa, na), (pb, nb) = _split_number(a), _split_number(b)
    return pa == pb and na is not None and nb is not None and 0 < abs(na - nb) <= 5


def _pairs(docs, party_attr: str, kind: str) -> list[Finding]:
    out: list[Finding] = []
    by_party: dict[str, list] = {}
    for d in docs:
        if d.status != "void":
            by_party.setdefault(getattr(d, party_attr), []).append(d)
    for party, items in by_party.items():
        for a, b in combinations(sorted(items, key=lambda x: x.issue_date), 2):
            gap = abs((b.issue_date - a.issue_date).days)
            if a.amount <= 0 or b.amount <= 0:
                continue
            exact = abs(a.amount - b.amount) < 0.005 and gap <= EXACT_WINDOW_DAYS
            fuzzy = (not exact and abs(a.amount - b.amount) / max(a.amount, b.amount) <= FUZZY_AMOUNT_TOL
                     and gap <= FUZZY_WINDOW_DAYS and _similar_numbering(a.id, b.id))
            if not (exact or fuzzy):
                continue
            out.append(Finding(
                id="", type=f"duplicate_{kind}", severity="high" if exact else "medium",
                title=f"Possible duplicate {kind}",
                amount_at_risk=round(min(a.amount, b.amount), 2),
                evidence=[a.id, b.id],
                detail={"party": party, "amounts": [a.amount, b.amount], "issue_dates": [a.issue_date.isoformat(), b.issue_date.isoformat()],
                        "days_apart": gap, "match": "exact" if exact else "near-identical amount and adjacent numbering",
                        "sources": sorted({a.source, b.source})}))
    return out


def detect(data: NormalizedData) -> list[Finding]:
    return _pairs(data.invoices, "customer", "invoice") + _pairs(data.bills, "vendor", "bill")
