from datetime import date, timedelta

import pytest

from engines import insights
from schema import Bill, Invoice, NormalizedData

TODAY = date(2026, 6, 15)
d = lambda n: TODAY + timedelta(days=n)


def inv(id, customer, amount, issue, due, paid=None, balance=None):
    return Invoice(id=id, customer=customer, amount=amount, issue_date=issue, due_date=due, paid_date=paid,
                   status="paid" if paid else "open", balance=balance, source="t")


def bill(id, vendor, amount, issue, due, paid=None):
    return Bill(id=id, vendor=vendor, amount=amount, issue_date=issue, due_date=due, paid_date=paid,
                status="paid" if paid else "open", source="t")


def by_id(data):
    return {m["id"]: m for m in insights.compute(data, TODAY)}


@pytest.fixture
def full():
    return NormalizedData(current_balance=500, invoices=[
        inv("I1", "Acme", 1000, d(-60), d(-30), paid=d(-25)),           # paid 5 days late, took 35 days
        inv("I2", "Acme", 1000, d(-40), d(-10), paid=d(-10)),           # on time, took 30 days
        inv("I3", "Beta", 2000, d(-20), d(-5)),                         # open, 5 days overdue
        inv("I4", "Beta", 1000, d(-10), d(20)),                         # open, not yet due
        inv("I5", "Acme", 400, d(-5), d(25), paid=d(-1)),               # on time, took 4 days
    ], bills=[
        bill("B1", "V1", 600, d(-50), d(-20), paid=d(-20)),
        bill("B2", "V1", 400, d(-10), d(20)),                           # open
        bill("B3", "V2", 1000, d(-30), d(0), paid=d(0)),
    ])


def test_every_metric_is_computed_from_the_records(full):
    m = by_id(full)
    assert len(m) == 10 and all(x["available"] for x in m.values())
    # open receivables 3000; invoiced in last 90d = 5400 -> 3000 / (5400/90)
    assert m["dso"]["value"] == 50.0
    # open payables 400; billed in last 90d = 2000
    assert m["dpo"]["value"] == 18.0
    assert "50 days" in m["dso"]["note"] and "18 days" in m["dso"]["note"]
    # 3 paid invoices, 1 paid late (5 days)
    assert m["on_time_rate"]["value"] == pytest.approx(66.7, abs=0.05)
    assert m["on_time_rate"]["detail"] == {"paid_invoices": 3, "on_time": 2, "late": 1, "avg_days_late": 5}
    assert m["days_to_paid"]["value"] == pytest.approx((35 + 30 + 4) / 3, abs=0.05)
    assert m["days_to_paid"]["detail"]["median"] == 30
    # overdue 2000 of 3000 open
    assert m["receivables_aging"]["value"] == pytest.approx(66.7, abs=0.05)
    buckets = {b["label"]: b["amount"] for b in m["receivables_aging"]["detail"]["buckets"]}
    assert buckets["Not yet due"] == 1000 and buckets["1-30 days"] == 2000 and buckets["Over 90 days"] == 0
    # revenue by customer: Acme 2400, Beta 3000 of 5400
    assert m["customer_concentration"]["value"] == pytest.approx(55.6, abs=0.05)
    assert m["customer_concentration"]["detail"]["top"][0]["name"] == "Beta"
    assert m["vendor_concentration"]["detail"]["top"][0] == {"name": "V1", "amount": 1000.0, "share": 50.0}
    # (500 cash + 3000 receivables) / 400 payables
    assert m["liquidity_ratio"]["value"] == 8.75 and m["liquidity_ratio"]["note"] is None


def test_trend_uses_last_full_month_and_flags_partial_current_month():
    data = NormalizedData(invoices=[
        inv("a", "A", 100, date(2026, 4, 10), date(2026, 5, 10)),
        inv("b", "A", 150, date(2026, 5, 10), date(2026, 6, 10)),
        inv("c", "A", 999, date(2026, 6, 2), date(2026, 7, 2)),
    ])
    t = by_id(data)["revenue_trend"]
    assert t["value"] == 150 and t["change_pct"] == 50.0           # May vs April; June excluded
    assert [s["month"] for s in t["detail"]["series"]][-2:] == ["2026-05", "2026-06"]
    assert t["detail"]["series"][-1]["partial"] is True and t["detail"]["series"][-1]["amount"] == 999


def test_sparse_data_explains_itself_instead_of_inventing_numbers():
    # Invoices only (no bills, no cash), like a freshly connected small account.
    data = NormalizedData(invoices=[inv("a", "A", 100, d(-5), d(25))])
    m = by_id(data)
    for mid in ("dpo", "vendor_concentration", "spend_trend", "liquidity_ratio", "on_time_rate", "days_to_paid"):
        assert m[mid]["available"] is False and m[mid]["value"] is None and m[mid]["reason"]
    assert m["dso"]["available"] and m["customer_concentration"]["value"] == 100.0
    assert m["receivables_aging"]["value"] == 0.0               # nothing overdue is a real answer, not "no data"


def test_empty_data_yields_all_unavailable_with_reasons():
    m = insights.compute(NormalizedData(), TODAY)
    assert len(m) == 10 and not any(x["available"] for x in m) and all(x["reason"] for x in m)


def test_liquidity_notes_missing_cash():
    data = NormalizedData(cash_known=False, invoices=[inv("a", "A", 300, d(-5), d(25))], bills=[bill("b", "V", 100, d(-5), d(25))])
    liq = by_id(data)["liquidity_ratio"]
    assert liq["value"] == 3.0 and "receivables only" in liq["note"]


def test_void_documents_are_ignored():
    voided = inv("v", "A", 9999, d(-5), d(25))
    voided.status = "void"
    data = NormalizedData(invoices=[voided, inv("a", "A", 100, d(-5), d(25))])
    assert by_id(data)["customer_concentration"]["detail"]["total"] == 100


def test_zero_month_trend_says_when_the_records_actually_end():
    data = NormalizedData(invoices=[inv("a", "A", 100, date(2026, 3, 10), date(2026, 4, 10))])
    t = by_id(data)["revenue_trend"]
    assert t["value"] == 0 and "2026-03-10" in t["note"]
