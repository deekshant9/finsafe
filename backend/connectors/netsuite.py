"""Oracle NetSuite connector (OAuth 2.0 client-credentials / M2M + SuiteQL).

Auth: sign a short-lived JWT (PS256 by default) with the private key whose public
certificate was uploaded under "OAuth 2.0 Client Credentials (M2M) Setup", then POST it
to the account's token endpoint as a client assertion. Data: SuiteQL over REST
(`POST /services/rest/query/v1/suiteql`, header `Prefer: transient`, paged by
limit/offset, `hasMore` in the response).

The account ID comes from the connect dialog; client id / certificate id / private key
are server-side secrets (see .env.example).
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import httpx
import jwt

import config
from schema import Bill, ConnectRequest, Invoice, NormalizedData
from .base import LOOKBACK_DAYS, ConnectOutcome, Connector, ConnectorError, parse_date, to_float

PAGE = 1000
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def account_host(account_id: str) -> str:
    """'1234567_SB1' -> '1234567-sb1' (NetSuite's URL form of sandbox account ids)."""
    return account_id.strip().lower().replace("_", "-")


class NetSuiteConnector(Connector):
    id = "netsuite"
    name = "Oracle NetSuite"

    def __init__(self) -> None:
        self.account_id = ""
        self.access_token = ""
        self.expires_at = 0.0
        self._client = httpx.Client(timeout=60)

    @property
    def _base(self) -> str:
        return f"https://{account_host(self.account_id)}.suitetalk.api.netsuite.com/services/rest"

    # ── connect / auth ──
    def connect(self, req: ConnectRequest) -> ConnectOutcome:
        account = (req.account or "").strip()
        if not account:
            raise ConnectorError("NetSuite Account ID is required.")
        missing = [n for n, v in (("NETSUITE_CLIENT_ID", config.NETSUITE_CLIENT_ID),
                                  ("NETSUITE_CERTIFICATE_ID", config.NETSUITE_CERTIFICATE_ID)) if not v]
        if missing or not Path(config.NETSUITE_PRIVATE_KEY_PATH).is_file():
            raise ConnectorError(
                "NetSuite isn't configured on the server. Set NETSUITE_CLIENT_ID, NETSUITE_CERTIFICATE_ID "
                "and NETSUITE_PRIVATE_KEY_PATH in backend/.env.", 503)
        self.account_id = account
        try:
            self._authenticate()
            self._suiteql("SELECT id FROM transaction", limit=1)   # proves the role can run SuiteQL
        except httpx.HTTPError as e:
            self.disconnect()
            raise ConnectorError(f"Could not reach NetSuite account {account}: {e}", 502)
        except ConnectorError:
            self.disconnect()
            raise
        return ConnectOutcome(status="connected")

    def _authenticate(self) -> None:
        token_url = f"{self._base}/auth/oauth2/v1/token"
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": config.NETSUITE_CLIENT_ID, "scope": ["rest_webservices"], "aud": token_url,
             "iat": now, "exp": now + 300},
            Path(config.NETSUITE_PRIVATE_KEY_PATH).read_text(),
            algorithm=config.NETSUITE_JWT_ALG,
            headers={"typ": "JWT", "kid": config.NETSUITE_CERTIFICATE_ID},
        )
        resp = self._client.post(token_url, data={
            "grant_type": "client_credentials",
            "client_assertion_type": JWT_BEARER,
            "client_assertion": assertion,
        })
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or "access_token" not in body:
            reason = body.get("error_description") or body.get("error") or resp.text[:200]
            raise ConnectorError(f"NetSuite rejected the credentials (HTTP {resp.status_code}): {reason}", 401)
        self.access_token = body["access_token"]
        self.expires_at = time.time() + int(body.get("expires_in", 3600)) - 60

    # ── SuiteQL ──
    def _suiteql(self, query: str, limit: int = PAGE, offset: int = 0) -> dict:
        if time.time() >= self.expires_at:
            self._authenticate()
        resp = self._client.post(
            f"{self._base}/query/v1/suiteql", params={"limit": limit, "offset": offset},
            headers={"Authorization": f"Bearer {self.access_token}", "Prefer": "transient",
                     "Content-Type": "application/json"},
            json={"q": query})
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("o:errorDetails", [{}])[0].get("detail") or resp.text[:200]
            except (ValueError, IndexError, AttributeError):
                detail = resp.text[:200]
            raise ConnectorError(f"NetSuite SuiteQL failed (HTTP {resp.status_code}): {detail}",
                                 401 if resp.status_code == 401 else 502)
        return resp.json()

    def _query_all(self, query: str, max_rows: int = 20000) -> list[dict]:
        rows: list[dict] = []
        while len(rows) < max_rows:
            page = self._suiteql(query, offset=len(rows))
            rows.extend(page.get("items", []))
            if not page.get("hasMore"):
                break
        return rows

    # ── fetch + map ──
    def fetch(self) -> NormalizedData:
        if not self.access_token:
            raise ConnectorError("NetSuite isn't connected.", 409)
        cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        warnings: list[str] = []

        def docs(tran_type: str) -> list[dict]:
            # status 'A' = Open, 'B' = Paid In Full for both CustInvc and VendBill.
            return self._query_all(
                "SELECT t.id, t.tranid, BUILTIN.DF(t.entity) AS counterparty, t.foreigntotal, "
                "t.foreignamountunpaid, t.trandate, t.duedate, t.status, t.closedate "
                f"FROM transaction t WHERE t.type = '{tran_type}' AND t.voided = 'F' "
                f"AND (t.status = 'A' OR t.trandate >= TO_DATE('{cutoff}', 'YYYY-MM-DD')) "
                "ORDER BY t.trandate DESC")

        invoices: list[Invoice] = []
        for r in docs("CustInvc"):
            common = _common(r)
            if common:
                invoices.append(Invoice(customer=r.get("counterparty") or "Unknown customer", source=self.id, **common))
        bills: list[Bill] = []
        for r in docs("VendBill"):
            common = _common(r)
            if common:
                bills.append(Bill(vendor=r.get("counterparty") or "Unknown vendor", source=self.id, **common))

        balance, cash_known = self._cash(warnings)
        return NormalizedData(invoices=invoices, bills=bills, current_balance=balance, cash_known=cash_known,
                              company=f"NetSuite {self.account_id}", warnings=warnings)

    def _cash(self, warnings: list[str]) -> tuple[float, bool]:
        try:
            rows = self._suiteql(
                "SELECT SUM(tal.amount) AS balance FROM TransactionAccountingLine tal "
                "JOIN account a ON a.id = tal.account "
                "WHERE a.accttype = 'Bank' AND tal.posting = 'T'").get("items", [])
            if rows and rows[0].get("balance") is not None:
                return round(to_float(rows[0]["balance"]), 2), True
            warnings.append("No bank account postings were found in NetSuite, so cash-based figures are unavailable.")
            return 0.0, False
        except ConnectorError as e:
            warnings.append(f"Could not read bank balances from NetSuite ({e}); cash-based figures are unavailable.")
            return 0.0, False

    def disconnect(self) -> None:
        self.account_id = self.access_token = ""
        self.expires_at = 0.0


def _common(r: dict) -> Optional[dict]:
    issue = parse_date(r.get("trandate"))
    if issue is None:
        return None
    status = "paid" if r.get("status") == "B" else "open"
    return dict(
        id=str(r.get("tranid") or r["id"]), amount=abs(to_float(r.get("foreigntotal"))),
        issue_date=issue, due_date=parse_date(r.get("duedate")) or issue, status=status,
        balance=abs(to_float(r.get("foreignamountunpaid"))),
        paid_date=parse_date(r.get("closedate")) if status == "paid" else None)
