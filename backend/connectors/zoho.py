"""Zoho Books connector (live OAuth 2.0, "Server-based Application" in the Zoho API Console).

Same model as dashboard-vezras: ONE client id/secret for the whole platform lives in server config;
the user never enters credentials, they just click Connect, log in on Zoho's consent screen and
approve. connect() returns the consent URL; Zoho redirects the browser to /auth/zoho/callback which
calls exchange_code(). We then use the `api_domain` Zoho returns, so the data center is automatic.

Unlike dashboard-vezras (ZohoBooks.fullaccess.all) we request READ-ONLY scopes only, because FinSafe
promises never to modify the books. List endpoints, page_context paging, 401 -> refresh and 429
back-off follow vezras-app/zoho_books_sync.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

import config
from schema import Bill, ConnectRequest, Invoice, NormalizedData
from .base import LOOKBACK_DAYS, ConnectOutcome, Connector, ConnectorError, parse_date, to_float

SCOPES = ",".join([
    "ZohoBooks.settings.READ", "ZohoBooks.contacts.READ", "ZohoBooks.invoices.READ", "ZohoBooks.bills.READ",
    "ZohoBooks.customerpayments.READ", "ZohoBooks.vendorpayments.READ", "ZohoBooks.banking.READ",
])
STATE_TTL_SECONDS = 600
# Zoho appends `accounts-server` (the user's data-center login host) to the callback. We send our
# client secret there, so only ever trust genuine Zoho account hosts.
ZOHO_ACCOUNTS_HOST = re.compile(r"^https://accounts\.zoho\.(com|in|eu|com\.au|jp|com\.cn|sa|ca)$")
log = logging.getLogger(__name__)
# Zoho's /bankaccounts also lists petty-cash accounts as type "cash"; credit cards etc. are not cash.
CASH_ACCOUNT_TYPES = {"bank", "cash"}
MAX_PAGES = 30  # 30 x 200 = 6,000 rows per entity is plenty for a risk snapshot


class ZohoConnector(Connector):
    id = "zoho"
    name = "Zoho Books"

    def __init__(self) -> None:
        self.refresh_token = ""
        self.access_token = ""
        self.accounts_url = config.ZOHO_ACCOUNTS_URL
        self.api_url = ""          # {api_domain}/books/v3, learned from the token response
        self.org_id = ""
        self.org_name = ""
        self.currency = ""
        self._oauth_state: Optional[str] = None
        self._state_expires = 0.0
        self._client = httpx.Client(timeout=30)

    # ── connect / OAuth ──
    def connect(self, req: ConnectRequest) -> ConnectOutcome:
        """Start the login: hand back Zoho's consent URL for the browser to follow."""
        if not (config.ZOHO_CLIENT_ID and config.ZOHO_CLIENT_SECRET):
            raise ConnectorError(
                "Zoho Books isn't set up on the server yet. Register a Server-based Application in the Zoho API "
                "Console and set ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET and ZOHO_REDIRECT_URI in backend/.env.", 503)
        self._oauth_state = secrets.token_urlsafe(24)
        self._state_expires = time.time() + STATE_TTL_SECONDS
        query = urlencode({
            "scope": SCOPES, "client_id": config.ZOHO_CLIENT_ID, "response_type": "code",
            "access_type": "offline", "prompt": "consent",
            "redirect_uri": config.ZOHO_REDIRECT_URI, "state": self._oauth_state,
        })
        return ConnectOutcome(status="redirect", redirect_url=f"{config.ZOHO_ACCOUNTS_URL}/oauth/v2/auth?{query}")

    def exchange_code(self, code: str, state: str, accounts_server: str = "") -> None:
        """Finish the login after Zoho redirects back: code -> tokens, then pick the organization."""
        expected, self._oauth_state = self._oauth_state, None
        if not expected or time.time() > self._state_expires or not secrets.compare_digest(state or "", expected):
            raise ConnectorError("This Zoho login link expired or didn't match - click Connect again.", 400)
        accounts = accounts_server.rstrip("/") if ZOHO_ACCOUNTS_HOST.match(accounts_server.rstrip("/")) else config.ZOHO_ACCOUNTS_URL
        resp = self._client.post(f"{accounts}/oauth/v2/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": config.ZOHO_CLIENT_ID, "client_secret": config.ZOHO_CLIENT_SECRET,
            "redirect_uri": config.ZOHO_REDIRECT_URI,
        })
        payload = self._json(resp, "token exchange")
        if not payload.get("access_token") or not payload.get("refresh_token"):
            # Never echo the payload: it may hold one live token.
            raise ConnectorError(f"Zoho didn't grant offline access ({payload.get('error', 'no refresh token returned')}). Click Connect again.")
        self.refresh_token, self.access_token, self.accounts_url = payload["refresh_token"], payload["access_token"], accounts
        self.api_url = f"{payload.get('api_domain', 'https://www.zohoapis.com').rstrip('/')}/books/v3"
        self._resolve_org()

    def _refresh_access_token(self) -> None:
        resp = self._client.post(f"{self.accounts_url}/oauth/v2/token", data={
            "grant_type": "refresh_token", "client_id": config.ZOHO_CLIENT_ID,
            "client_secret": config.ZOHO_CLIENT_SECRET, "refresh_token": self.refresh_token,
        })
        token = self._json(resp, "token refresh").get("access_token")
        if not token:
            raise ConnectorError("Zoho rejected the saved login - click Connect again.", 401)
        self.access_token = token

    def _resolve_org(self) -> None:
        """Auto-select the first Books organization (same MVP rule as dashboard-vezras); keep its name + currency."""
        orgs = self._get("/organizations", {}).get("organizations", [])
        if not orgs:
            raise ConnectorError("No Zoho Books organization found for this Zoho account.")
        self.org_id = str(orgs[0]["organization_id"])
        self.org_name = orgs[0].get("name", "")
        self.currency = orgs[0].get("currency_code", "")

    # ── HTTP ──
    @staticmethod
    def _json(resp: httpx.Response, what: str) -> dict:
        try:
            body = resp.json()
        except ValueError:
            raise ConnectorError(f"Zoho {what} returned a non-JSON response (HTTP {resp.status_code}).", 502)
        if resp.status_code >= 400:
            msg = body.get("message") or body.get("error") or resp.text[:200]
            raise ConnectorError(f"Zoho {what} failed (HTTP {resp.status_code}): {msg}", 502 if resp.status_code >= 500 else 400)
        return body

    def _get(self, endpoint: str, params: dict) -> dict:
        if self.org_id and endpoint != "/organizations":
            params = {"organization_id": self.org_id, **params}
        refreshed = False
        for attempt in range(4):
            resp = self._client.get(
                f"{self.api_url}{endpoint}",
                params=params,
                headers={"Authorization": f"Zoho-oauthtoken {self.access_token}"},
            )
            if resp.status_code == 401 and not refreshed:
                self._refresh_access_token()
                refreshed = True
                continue
            if resp.status_code == 429:
                time.sleep(min(int(resp.headers.get("Retry-After", 5 * (attempt + 1))), 30))
                continue
            return self._json(resp, endpoint)
        raise ConnectorError("Zoho is rate-limiting requests; try again in a minute.", 429)

    def _paged(self, endpoint: str, list_key: str, params: Optional[dict] = None) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            data = self._get(endpoint, {"per_page": 200, "page": page, **(params or {})})
            rows.extend(data.get(list_key, []))
            if not data.get("page_context", {}).get("has_more_page"):
                break
        return rows

    # ── fetch + map ──
    def fetch(self) -> NormalizedData:
        if not self.access_token:
            raise ConnectorError("Zoho Books isn't connected.", 409)
        cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
        warnings: list[str] = []

        paid_on_invoice = self._payment_dates("/customerpayments", "customerpayments", "invoice_numbers", warnings)
        paid_on_bill = self._payment_dates("/vendorpayments", "vendorpayments", "bill_numbers", warnings)

        invoices: list[Invoice] = []
        for r in self._paged("/invoices", "invoices", {"sort_column": "date", "sort_order": "D"}):
            status = _status(r.get("status"))
            issue = parse_date(r.get("date"))
            if status is None or issue is None or (status != "open" and issue < cutoff):
                continue
            number = r.get("invoice_number") or r["invoice_id"]
            invoices.append(Invoice(
                id=str(number), customer=r.get("customer_name") or "Unknown customer",
                amount=to_float(r.get("total")), issue_date=issue,
                due_date=parse_date(r.get("due_date")) or issue, status=status,
                paid_date=(paid_on_invoice.get(str(number)) or parse_date(r.get("last_payment_date"))) if status == "paid" else None,
                balance=to_float(r.get("balance")), source=self.id,
            ))

        bills: list[Bill] = []
        for r in self._paged("/bills", "bills", {"sort_column": "date", "sort_order": "D"}):
            status = _status(r.get("status"))
            issue = parse_date(r.get("date"))
            if status is None or issue is None or (status != "open" and issue < cutoff):
                continue
            number = r.get("bill_number") or r["bill_id"]
            bills.append(Bill(
                id=str(number), vendor=r.get("vendor_name") or "Unknown vendor",
                amount=to_float(r.get("total")), issue_date=issue,
                due_date=parse_date(r.get("due_date")) or issue, status=status,
                paid_date=(paid_on_bill.get(str(number)) or parse_date(r.get("last_payment_date"))) if status == "paid" else None,
                balance=to_float(r.get("balance")), source=self.id,
            ))

        balance, banks = 0.0, 0
        try:
            rows = self._paged("/bankaccounts", "bankaccounts")
            # Shape only (no amounts): helps diagnose field-name mismatches on a new account.
            log.info("Zoho bankaccounts: %d rows, types=%s, balance fields=%s", len(rows),
                     sorted({str(r.get("account_type")) for r in rows}),
                     sorted({k for r in rows for k in r if "balance" in k}))
            for acct in rows:
                raw = acct.get("bank_balance", acct.get("balance"))
                if acct.get("account_type") in CASH_ACCOUNT_TYPES and acct.get("is_active", True) and raw is not None:
                    balance += to_float(raw)
                    banks += 1
            if not banks:
                warnings.append("No active bank account with a balance was found in Zoho Books, so cash-based figures "
                                "(cash runway, liquidity) are unavailable.")
        except ConnectorError as e:
            warnings.append(f"Could not read bank balances ({e}); cash-based figures are unavailable.")

        return NormalizedData(invoices=invoices, bills=bills, current_balance=balance, cash_known=banks > 0,
                              currency=self.currency, company=self.org_name or None, warnings=warnings)

    def _payment_dates(self, endpoint: str, list_key: str, numbers_field: str, warnings: list[str]) -> dict[str, date]:
        """Map document number -> latest payment date, from the payments lists."""
        out: dict[str, date] = {}
        try:
            for p in self._paged(endpoint, list_key):
                d = parse_date(p.get("date"))
                if not d:
                    continue
                for number in str(p.get(numbers_field) or "").split(","):
                    number = number.strip()
                    if number and (number not in out or d > out[number]):
                        out[number] = d
        except ConnectorError as e:
            warnings.append(f"Payment dates unavailable from {endpoint} ({e}); late-payment analysis is limited.")
        return out

    def disconnect(self) -> None:
        self.refresh_token = self.access_token = self.api_url = self.org_id = ""
        self.org_name = self.currency = ""
        self.accounts_url = config.ZOHO_ACCOUNTS_URL
        self._oauth_state = None


def _status(zoho_status: Optional[str]) -> Optional[str]:
    """Zoho status -> our status. None = draft, not a real receivable/payable yet."""
    s = (zoho_status or "").lower()
    if s == "draft":
        return None
    if s in ("paid", "void"):
        return s
    return "open"  # sent, overdue, partially_paid, unpaid, viewed, open ...
