"""Connector tests run against mocked HTTP (httpx.MockTransport): no network, no real credentials."""
import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import config
from connectors.base import ConnectorError, parse_date
from connectors.netsuite import NetSuiteConnector, account_host
from connectors.odoo import OdooConnector
from connectors.zoho import ZohoConnector
from schema import ConnectRequest

TODAY = date.today()
d = lambda n: (TODAY + timedelta(days=n)).isoformat()


def mock(connector, handler):
    connector._client = httpx.Client(transport=httpx.MockTransport(handler))
    return connector


def test_parse_date_formats():
    assert parse_date("2026-03-04") == date(2026, 3, 4)
    assert parse_date("2026-03-04 10:00:00") == date(2026, 3, 4)
    assert parse_date("3/4/2026") == date(2026, 3, 4)
    assert parse_date(False) is None and parse_date("") is None


# ── Zoho ──
@pytest.fixture
def zoho_env(monkeypatch):
    for k, v in dict(ZOHO_CLIENT_ID="cid", ZOHO_CLIENT_SECRET="sec", ZOHO_ACCOUNTS_URL="https://accounts.zoho.in",
                     ZOHO_REDIRECT_URI="http://localhost:8000/auth/zoho/callback").items():
        monkeypatch.setattr(config, k, v)


def zoho_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/oauth/v2/token"):
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        assert form["client_id"] == "cid" and form["client_secret"] == "sec"
        if form["grant_type"] == "authorization_code":
            assert form["code"] == "the-code" and form["redirect_uri"].startswith("http%3A%2F%2Flocalhost")
            return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "api_domain": "https://www.zohoapis.eu"})
        assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "rt"
        return httpx.Response(200, json={"access_token": "at"})
    assert request.url.host == "www.zohoapis.eu"                      # data center comes from the token response
    assert request.headers["authorization"] == "Zoho-oauthtoken at"
    if path.endswith("/organizations"):
        return httpx.Response(200, json={"organizations": [{"organization_id": "42", "name": "Org X", "currency_code": "USD"}]})
    assert request.url.params["organization_id"] == "42"
    body = {
        "/invoices": {"invoices": [
            {"invoice_id": "1", "invoice_number": "INV-1", "customer_name": "Acme", "total": 1000, "balance": 1000,
             "date": d(-20), "due_date": d(10), "status": "sent"},
            {"invoice_id": "2", "invoice_number": "INV-2", "customer_name": "Acme", "total": 500, "balance": 0,
             "date": d(-90), "due_date": d(-60), "status": "paid"},
            {"invoice_id": "3", "invoice_number": "INV-3", "customer_name": "Acme", "total": 9, "balance": 9,
             "date": d(-1), "due_date": d(29), "status": "draft"}]},
        "/bills": {"bills": [{"bill_id": "9", "bill_number": "B-1", "vendor_name": "Vend", "total": 300, "balance": 300,
                              "date": d(-5), "due_date": d(25), "status": "open"}]},
        "/customerpayments": {"customerpayments": [{"date": d(-50), "invoice_numbers": "INV-2, INV-77"}]},
        "/vendorpayments": {"vendorpayments": []},
        "/bankaccounts": {"bankaccounts": [{"account_type": "bank", "bank_balance": 2500.5, "is_active": True},
                                           {"account_type": "cash", "bank_balance": 100, "is_active": True},
                                           {"account_type": "credit_card", "bank_balance": -900}]},
    }[path.removeprefix("/books/v3")]
    return httpx.Response(200, json={**body, "page_context": {"has_more_page": False}})


def zoho_login(z: ZohoConnector, accounts_server: str = ""):
    """What the browser does: follow the consent URL, come back with ?code&state."""
    url = z.connect(ConnectRequest()).redirect_url
    state = parse_qs(urlparse(url).query)["state"][0]
    z.exchange_code("the-code", state, accounts_server)
    return url


def test_zoho_connect_is_a_login_redirect_with_read_only_scopes(zoho_env):
    out = ZohoConnector().connect(ConnectRequest())
    q = parse_qs(urlparse(out.redirect_url).query)
    assert out.status == "redirect" and out.redirect_url.startswith("https://accounts.zoho.in/oauth/v2/auth?")
    assert q["access_type"] == ["offline"] and q["client_id"] == ["cid"] and "state" in q
    assert all(sc.endswith(".READ") for sc in q["scope"][0].split(","))      # never fullaccess/write


def test_zoho_login_then_fetch_maps_to_unified_schema(zoho_env):
    z = mock(ZohoConnector(), zoho_handler)
    zoho_login(z)
    data = z.fetch()
    assert [i.id for i in data.invoices] == ["INV-1", "INV-2"]                    # draft skipped
    assert data.invoices[0].status == "open" and data.invoices[0].outstanding == 1000
    assert data.invoices[1].status == "paid" and data.invoices[1].paid_date == TODAY - timedelta(days=50)
    assert data.bills[0].vendor == "Vend" and data.current_balance == 2600.5 and data.cash_known   # bank + cash counted, credit card ignored
    assert data.currency == "USD" and data.company == "Org X"


def test_zoho_unconfigured_is_a_clear_503(monkeypatch):
    monkeypatch.setattr(config, "ZOHO_CLIENT_ID", "")
    with pytest.raises(ConnectorError) as e:
        ZohoConnector().connect(ConnectRequest())
    assert e.value.status_code == 503


def test_zoho_callback_rejects_forged_expired_and_replayed_state(zoho_env):
    z = mock(ZohoConnector(), zoho_handler)
    z.connect(ConnectRequest())
    with pytest.raises(ConnectorError):
        z.exchange_code("the-code", "forged-state")
    z.connect(ConnectRequest())                       # state is single-use: the failed attempt consumed the old one
    state = z._oauth_state
    z._state_expires = 0                              # expired
    with pytest.raises(ConnectorError, match="expired"):
        z.exchange_code("the-code", state)


def test_zoho_only_trusts_real_zoho_account_hosts_for_the_token_exchange(zoho_env):
    hosts = []
    def handler(request):
        hosts.append(request.url.host) if request.url.path.endswith("/token") else None
        return zoho_handler(request)
    z = mock(ZohoConnector(), handler)
    zoho_login(z, accounts_server="https://evil.example.com")
    assert hosts[0] == "accounts.zoho.in"             # fell back to the configured host; secret not sent to evil.example.com
    z2 = mock(ZohoConnector(), handler)
    zoho_login(z2, accounts_server="https://accounts.zoho.eu")
    assert hosts[-1] == "accounts.zoho.eu"


# ── Odoo ──
MOVES = [
    {"id": 1, "name": "INV/2026/0001", "move_type": "out_invoice", "partner_id": [7, "Acme"], "amount_total": 1180,
     "amount_residual": 1180, "invoice_date": d(-10), "invoice_date_due": d(20), "payment_state": "not_paid",
     "invoice_payments_widget": False},
    {"id": 2, "name": "INV/2026/0002", "move_type": "out_invoice", "partner_id": [7, "Acme"], "amount_total": 500,
     "amount_residual": 0, "invoice_date": d(-80), "invoice_date_due": d(-50), "payment_state": "paid",
     "invoice_payments_widget": json.dumps({"content": [{"date": d(-40)}, {"date": d(-45)}]})},
    {"id": 3, "name": "BILL/2026/0001", "move_type": "in_invoice", "partner_id": [8, "Vend"], "amount_total": 700,
     "amount_residual": 700, "invoice_date": d(-3), "invoice_date_due": d(27), "payment_state": "not_paid",
     "invoice_payments_widget": False},
]


def odoo_json2(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "bearer KEY" and request.headers["x-odoo-database"] == "db1"
    model, method = request.url.path.removeprefix("/json/2/").split("/")
    if method == "search_count":
        return httpx.Response(200, json=1)
    body = json.loads(request.content)
    if model == "account.move":
        assert ["state", "=", "posted"] in body["domain"] or ("state", "=", "posted") in map(tuple, [x for x in body["domain"] if isinstance(x, list)])
        return httpx.Response(200, json=MOVES if body["offset"] == 0 else [])
    if model == "res.company":
        return httpx.Response(200, json=[{"name": "Odoo Co", "currency_id": [1, "INR"]}])
    if model == "account.move.line":
        return httpx.Response(200, json=[{"balance": 1000.0}, {"balance": 250.25}])
    return httpx.Response(404, json={"message": "no such model"})


def test_odoo_json2_connect_and_fetch():
    o = mock(OdooConnector(), odoo_json2)
    o.connect(ConnectRequest(url="shop.odoo.com", database="db1", apiKey="KEY"))
    assert o.mode == "json2" and o.url == "https://shop.odoo.com"
    data = o.fetch()
    assert len(data.invoices) == 2 and len(data.bills) == 1
    paid = next(i for i in data.invoices if i.status == "paid")
    assert paid.paid_date == TODAY - timedelta(days=40) and paid.customer == "Acme"
    assert data.bills[0].vendor == "Vend" and data.bills[0].outstanding == 700
    assert data.current_balance == 1250.25 and data.company == "Odoo Co"


def test_odoo_bad_key_is_a_401():
    o = mock(OdooConnector(), lambda r: httpx.Response(401, json={"message": "Invalid apikey"}))
    with pytest.raises(ConnectorError) as e:
        o.connect(ConnectRequest(url="https://x.odoo.com", database="db1", apiKey="bad"))
    assert e.value.status_code == 401


def test_odoo_legacy_jsonrpc_fallback_needs_login():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/json/2/"):
            return httpx.Response(404)
        params = json.loads(request.content)["params"]
        if params["service"] == "common":
            assert params["args"][:3] == ["db1", "me@x.com", "KEY"]
            return httpx.Response(200, json={"result": 9})
        db, uid, key, model, method, args, kwargs = params["args"]
        assert (db, uid, key) == ("db1", 9, "KEY") and method == "search_read"
        rows = {"account.move": MOVES, "res.company": [{"name": "Legacy Co", "currency_id": [1, "INR"]}],
                "account.move.line": [{"balance": 5.0}]}[model]
        return httpx.Response(200, json={"result": rows if kwargs["offset"] == 0 else []})

    o = mock(OdooConnector(), handler)
    with pytest.raises(ConnectorError, match="username"):
        o.connect(ConnectRequest(url="https://old.odoo.com", database="db1", apiKey="KEY"))
    o.connect(ConnectRequest(url="https://old.odoo.com", database="db1", apiKey="KEY", login="me@x.com"))
    assert o.mode == "jsonrpc" and o.fetch().company == "Legacy Co"


# ── NetSuite ──
@pytest.fixture
def netsuite_env(monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "key.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for k, v in dict(NETSUITE_CLIENT_ID="client-1", NETSUITE_CERTIFICATE_ID="cert-1",
                     NETSUITE_PRIVATE_KEY_PATH=str(pem), NETSUITE_JWT_ALG="PS256").items():
        monkeypatch.setattr(config, k, v)
    return key.public_key()


def test_account_host_for_sandbox_ids():
    assert account_host("1234567_SB1") == "1234567-sb1"


def test_netsuite_m2m_auth_and_suiteql_fetch(netsuite_env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "1234567-sb1.suitetalk.api.netsuite.com"
        if request.url.path.endswith("/auth/oauth2/v1/token"):
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            assert form["grant_type"] == "client_credentials"
            assert form["client_assertion_type"].endswith("jwt-bearer")
            assertion = form["client_assertion"]
            assert jwt.get_unverified_header(assertion) == {"alg": "PS256", "typ": "JWT", "kid": "cert-1"}
            claims = jwt.decode(assertion, netsuite_env, algorithms=["PS256"], audience=str(request.url).split("?")[0])
            assert claims["iss"] == "client-1" and claims["scope"] == ["rest_webservices"]
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.url.path.endswith("/query/v1/suiteql")
        assert request.headers["authorization"] == "Bearer tok" and request.headers["prefer"] == "transient"
        q = json.loads(request.content)["q"]
        seen[q.split("FROM")[1].split()[0]] = True
        if "CustInvc" in q:
            items = [{"id": "1", "tranid": "INV10", "counterparty": "Acme", "foreigntotal": "1000", "foreignamountunpaid": "1000",
                      "trandate": d(-10), "duedate": d(20), "status": "A", "closedate": None},
                     {"id": "2", "tranid": "INV11", "counterparty": "Acme", "foreigntotal": "400", "foreignamountunpaid": "0",
                      "trandate": "1/5/2026", "duedate": "2/4/2026", "status": "B", "closedate": "2/10/2026"}]
        elif "VendBill" in q:
            items = [{"id": "5", "tranid": "VB1", "counterparty": "Vend", "foreigntotal": "-700", "foreignamountunpaid": "-700",
                      "trandate": d(-2), "duedate": d(28), "status": "A", "closedate": None}]
        elif "TransactionAccountingLine" in q:
            items = [{"balance": "12345.5"}]
        else:
            items = [{"id": "1"}]
        return httpx.Response(200, json={"items": items, "hasMore": False})

    ns = mock(NetSuiteConnector(), handler)
    ns.connect(ConnectRequest(account="1234567_SB1"))
    data = ns.fetch()
    assert [i.id for i in data.invoices] == ["INV10", "INV11"]
    paid = data.invoices[1]
    assert paid.status == "paid" and paid.paid_date == date(2026, 2, 10) and paid.due_date == date(2026, 2, 4)
    assert data.bills[0].amount == 700 and data.bills[0].outstanding == 700          # negative bill amounts normalized
    assert data.current_balance == 12345.5


def test_netsuite_rejected_credentials(netsuite_env):
    ns = mock(NetSuiteConnector(), lambda r: httpx.Response(400, json={"error": "invalid_client", "error_description": "bad cert"}))
    with pytest.raises(ConnectorError, match="bad cert") as e:
        ns.connect(ConnectRequest(account="123"))
    assert e.value.status_code == 401


def test_netsuite_unconfigured_is_a_clear_503(monkeypatch):
    monkeypatch.setattr(config, "NETSUITE_CLIENT_ID", "")
    with pytest.raises(ConnectorError) as e:
        NetSuiteConnector().connect(ConnectRequest(account="123"))
    assert e.value.status_code == 503
