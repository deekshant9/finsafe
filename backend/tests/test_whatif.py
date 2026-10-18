from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from engines import cash, whatif
from schema import Bill, Invoice, NormalizedData

TODAY = date(2026, 6, 15)
d = lambda n: TODAY + timedelta(days=n)


def inv(id, customer, amount, issue, due):
    return Invoice(id=id, customer=customer, amount=amount, issue_date=issue, due_date=due, status="open", source="t")


def bill(id, vendor, amount, issue, due):
    return Bill(id=id, vendor=vendor, amount=amount, issue_date=issue, due_date=due, status="open", source="t")


# ── cash projection knobs ──
def test_projection_knobs_default_to_no_change_and_each_does_what_it_says():
    data = NormalizedData(current_balance=100, invoices=[inv("I", "C", 1000, d(-5), d(40))],
                          bills=[bill("B", "V", 500, d(-5), d(20))])
    assert cash.project(data, TODAY)["runway_day"] == 20
    assert cash.project(data, TODAY, exclude_invoice_ids={"I"})["runway_day"] == 20           # never paid: still short
    assert cash.project(data, TODAY, receipt_shift_days=25)["runway_day"] is None             # paid on day 15, before the bill
    ov = cash.project(data, TODAY, invoice_overrides={"I": (10, 0.5)})                        # +500 on day 10
    assert ov["runway_day"] is None and ov["series"][10]["balance"] == 600

    small = NormalizedData(current_balance=100, invoices=[inv("I", "C", 300, d(-5), d(40))],
                           bills=[bill("B", "V", 500, d(-5), d(20))])
    assert cash.project(small, TODAY)["runway_day"] == 20
    assert cash.project(small, TODAY, bill_shift_days=30)["runway_day"] == 50                 # +300 on day 40, bill on day 50


# ── early-payment discount ──
@pytest.fixture
def discount_data():
    return NormalizedData(current_balance=100, invoices=[
        inv("I1", "Acme", 1000, d(-5), d(40)),     # expected day 40
        inv("I2", "Beta", 2000, d(-5), d(10)),     # expected day 10
    ], bills=[bill("B", "V", 500, d(-5), d(20))])


def test_early_discount_annualized_cost_and_runway(discount_data):
    r = whatif.early_discount(discount_data, ["I1"], 2, 10, TODAY)
    row = r["invoices"][0]
    assert row["days_accelerated"] == 30 and row["discount"] == 20 and row["cash_received"] == 980
    # (20 / 980) * (365 / 30) = 24.8% a year
    assert row["annualized_cost_pct"] == 24.8 and r["annualized_cost_pct"] == 24.8
    assert r["cash_pulled_forward"] == 980 and r["avg_days_accelerated"] == 30 and r["total_discount_cost"] == 20
    # With only I1 outstanding: 100 - 500 < 0 on day 20 before; +980 on day 10 keeps cash positive after.
    only_i1 = NormalizedData(current_balance=100, invoices=[inv("I1", "Acme", 1000, d(-5), d(40))],
                             bills=[bill("B", "V", 500, d(-5), d(20))])
    runway = whatif.early_discount(only_i1, ["I1"], 2, 10, TODAY)["runway"]
    assert runway["runway_before"] == 20 and runway["no_longer_runs_out"] is True


def test_discount_to_a_customer_who_pays_sooner_anyway_is_reported_as_pure_cost(discount_data):
    r = whatif.early_discount(discount_data, ["I2"], 2, 10, TODAY)
    assert r["invoices"][0]["pays_sooner_anyway"] is True and r["invoices"][0]["annualized_cost_pct"] is None
    assert r["discount_given_for_nothing"] == 40 and r["cash_pulled_forward"] == 0 and r["annualized_cost_pct"] is None


@pytest.mark.parametrize("ids,pct,days,msg", [([], 2, 10, "at least one"), (["I1"], 0, 10, "between 0 and 100"),
                                              (["I1"], 100, 10, "between 0 and 100"), (["I1"], 2, -1, "negative"),
                                              (["NOPE"], 2, 10, "NOPE")])
def test_early_discount_rejects_bad_input(discount_data, ids, pct, days, msg):
    with pytest.raises(ValueError, match=msg):
        whatif.early_discount(discount_data, ids, pct, days, TODAY)


# ── payment terms ──
@pytest.fixture
def terms_data():
    return NormalizedData(current_balance=100, invoices=[inv("I", "C", 1800, d(-30), d(30))],
                          bills=[bill("B", "V", 900, d(-30), d(20))])


def test_payment_terms_cash_freed_and_days(terms_data):
    r = whatif.payment_terms(terms_data, 5, 7, TODAY)
    assert (r["daily_invoiced"], r["daily_billed"]) == (20, 10)                  # 1800/90, 900/90
    assert r["cash_freed_from_receivables"] == 100 and r["cash_freed_from_payables"] == 70 and r["total_cash_freed"] == 170
    assert r["dso"] == {"before": 90.0, "after": 85.0} and r["dpo"] == {"before": 90.0, "after": 97.0}
    assert r["runway"]["runway_before"] == 20 and r["runway"]["no_longer_runs_out"] is True


def test_payment_terms_each_lever_alone(terms_data):
    only_collect = whatif.payment_terms(terms_data, 5, 0, TODAY)["runway"]
    assert only_collect["runway_after"] == 20                                    # bill still lands before the money
    only_pay = whatif.payment_terms(terms_data, 0, 7, TODAY)["runway"]
    assert only_pay["runway_after"] == 27 and only_pay["runway_gain_days"] == 7


def test_payment_terms_input_and_sparse_data():
    with pytest.raises(ValueError):
        whatif.payment_terms(NormalizedData(), 0, 0, TODAY)
    with pytest.raises(ValueError, match="negative"):
        whatif.payment_terms(NormalizedData(), -1, 0, TODAY)
    r = whatif.payment_terms(NormalizedData(invoices=[inv("I", "C", 100, d(-200), d(-170))]), 5, 0, TODAY)
    assert r["cash_freed_from_receivables"] is None and r["notes"]               # no invoices in the last 90 days


# ── customer default ──
@pytest.fixture
def default_data():
    return NormalizedData(current_balance=100, invoices=[
        inv("I1", "Acme", 1000, d(-5), d(40)), inv("I2", "Acme", 500, d(-5), d(5)), inv("I3", "Beta", 2000, d(-5), d(10)),
    ], bills=[bill("B", "V", 1500, d(-5), d(20))])


def test_customer_default_stress_test(default_data):
    r = whatif.customer_default(default_data, ["Beta"], TODAY)
    assert r["receivables_lost"] == 2000 and r["share_of_open_receivables_pct"] == 57.1 and r["open_receivables_after"] == 1500
    assert r["runway"]["runway_before"] is None and r["runway"]["runway_after"] == 20 and r["runway"]["newly_runs_out"] is True
    both = whatif.customer_default(default_data, ["Acme", "Beta", "Acme"], TODAY)
    assert both["receivables_lost"] == 3500 and len(both["customers"]) == 2     # duplicate name counted once


def test_customer_default_rejects_unknown_or_empty(default_data):
    with pytest.raises(ValueError, match="Nobody"):
        whatif.customer_default(default_data, ["Nobody"], TODAY)
    with pytest.raises(ValueError):
        whatif.customer_default(default_data, [], TODAY)


def test_unknown_cash_gives_amounts_but_no_runway(default_data):
    default_data.cash_known = False
    assert whatif.customer_default(default_data, ["Beta"], TODAY)["runway"] is None
    assert whatif.early_discount(default_data, ["I1"], 2, 10, TODAY)["runway"] is None


def test_options_are_sorted_largest_first(default_data):
    o = whatif.options(default_data, TODAY)
    assert [i["id"] for i in o["invoices"]] == ["I3", "I1", "I2"]
    assert o["customers"][0] == {"name": "Beta", "outstanding": 2000, "invoice_count": 1}
    assert o["bills"][0]["id"] == "B" and o["cash_known"] is True


# ── HTTP layer ──
@pytest.fixture
def client(default_data):
    from main import app
    from store import store
    store.datasets["t"] = default_data
    yield TestClient(app)
    store.datasets.pop("t", None)


def test_endpoints_validate_and_compute(client):
    ok = client.post("/api/whatif/customer-default", json={"customers": ["Beta"]})
    assert ok.status_code == 200 and ok.json()["receivables_lost"] == 2000
    assert client.post("/api/whatif/customer-default", json={"customers": ["Nobody"]}).status_code == 422
    assert client.post("/api/whatif/early-discount", json={"invoice_ids": ["I1"], "discount_pct": 150, "pay_within_days": 5}).status_code == 422
    assert client.post("/api/whatif/payment-terms", json={"collect_days_faster": 0, "pay_days_later": 0}).status_code == 422
    assert client.post("/api/whatif/payment-terms", json={"collect_days_faster": 3}).status_code == 200
    assert client.get("/api/whatif/options").json()["customers"][0]["name"] == "Beta"


def test_endpoints_need_data():
    from main import app
    from store import store
    assert not store.datasets
    assert TestClient(app).get("/api/whatif/options").status_code == 409
