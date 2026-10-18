from datetime import date, timedelta

from analysis import run
from engines import cash, duplicates, late_pay, vendor_spike
from schema import Bill, Invoice, NormalizedData

TODAY = date(2026, 1, 1)


def inv(id, customer, amount, issue, due, paid=None, **kw):
    return Invoice(id=id, customer=customer, amount=amount, issue_date=issue, due_date=due, paid_date=paid,
                   status="paid" if paid else "open", source="t", **kw)


def bill(id, vendor, amount, issue, due, paid=None):
    return Bill(id=id, vendor=vendor, amount=amount, issue_date=issue, due_date=due, paid_date=paid,
                status="paid" if paid else "open", source="t")


def test_run_assembles_findings_ranked_and_scored():
    d = NormalizedData(
        current_balance=100,
        invoices=[inv("INV-1", "Acme", 500, TODAY, TODAY + timedelta(30)),
                  inv("INV-2", "Acme", 500, TODAY + timedelta(2), TODAY + timedelta(32))],
        bills=[bill("B-1", "V", 900, TODAY, TODAY + timedelta(5))])
    result = run(d, TODAY)
    types = [f["type"] for f in result["findings"]]
    assert "duplicate_invoice" in types and "cash_shortfall" in types
    assert [f["id"] for f in result["findings"]] == [f"f_{i:03d}" for i in range(1, len(types) + 1)]
    assert result["findings"][0]["severity"] == "high"
    assert 0 <= result["health"]["score"] < 100
    assert result["runway"]["runway_day"] == 5


def test_exact_duplicate_within_a_week_is_high_and_cites_both_ids():
    d = NormalizedData(invoices=[
        inv("INV-1", "Acme", 500, TODAY, TODAY + timedelta(30)),
        inv("INV-9", "Acme", 500, TODAY + timedelta(3), TODAY + timedelta(33)),
        inv("INV-2", "Acme", 500, TODAY + timedelta(40), TODAY + timedelta(70)),   # monthly repeat: not a duplicate
    ])
    found = duplicates.detect(d)
    assert len(found) == 1 and found[0].severity == "high" and found[0].evidence == ["INV-1", "INV-9"]


def test_vendor_spike_needs_history_and_over_25_percent():
    mk = lambda i, amt: bill(f"B{i}", "V", amt, TODAY + timedelta(30 * i), TODAY + timedelta(30 * i + 30))
    assert not vendor_spike.detect(NormalizedData(bills=[mk(0, 100), mk(1, 100), mk(2, 120)]))     # +20%
    assert not vendor_spike.detect(NormalizedData(bills=[mk(0, 100), mk(1, 140)]))                 # too little history
    found = vendor_spike.detect(NormalizedData(bills=[mk(0, 100), mk(1, 100), mk(2, 141)]))
    assert found and found[0].severity == "high" and found[0].amount_at_risk == 41


def test_late_payer_formula():
    d = NormalizedData(invoices=[
        inv(f"I{n}", "Slow", 100, TODAY, TODAY + timedelta(30), paid=TODAY + timedelta(30 + 30)) for n in range(3)])
    [row] = late_pay.rank(d, TODAY)
    assert row["late_ratio"] == 1.0 and row["avg_days_late"] == 30 and row["risk"] == 1.0   # 0.6*1 + 0.4*min(30/30,1)


def test_runway_crosses_zero_on_the_right_day_and_whatif_helps():
    d = NormalizedData(current_balance=100, invoices=[inv("I", "C", 1000, TODAY, TODAY + timedelta(40))],
                       bills=[bill("B", "V", 300, TODAY, TODAY + timedelta(10))])
    base = cash.project(d, TODAY)
    assert base["runway_day"] == 10                                     # 100 - 300 < 0 on the bill's due day
    helped = cash.project(d, TODAY, collect_invoice_ids={"I"})
    assert helped["runway_day"] is None
    delayed = cash.project(d, TODAY, delay_bill_ids={"B"}, delay_days=60)
    assert delayed["runway_day"] is None or delayed["runway_day"] > 10


def test_unknown_cash_is_not_presented_as_zero_cash():
    d = NormalizedData(cash_known=False, current_balance=0,
                       invoices=[inv("INV-1", "Acme", 500, TODAY, TODAY + timedelta(30))],
                       bills=[bill("B-1", "V", 900, TODAY, TODAY + timedelta(5))])
    result = run(d, TODAY)
    assert result["runway"]["cash_known"] is False and result["runway"]["runway_day"] is None
    assert result["runway"]["series"] == [] and result["runway"]["min_balance"] is None
    assert "cash_shortfall" not in [f["type"] for f in result["findings"]]
    assert result["health"]["score"] == 100          # no runway penalty from a balance we never saw
