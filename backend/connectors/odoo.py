"""Odoo connector (API key).

Odoo 19+ exposes the "External JSON-2 API": POST {url}/json/2/{model}/{method} with
`Authorization: bearer <API key>` and `X-Odoo-Database: <db>`; the JSON body holds the
method's keyword arguments and the response is the bare result.

Odoo <= 18 only has the legacy /jsonrpc endpoint (service "object", method "execute_kw"),
which needs a login (user) + the API key used as the password. We try JSON-2 first and
fall back to /jsonrpc when the server answers 404; the fallback requires the optional
`login` field from the connect dialog.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

import httpx

from schema import Bill, ConnectRequest, Invoice, NormalizedData
from .base import LOOKBACK_DAYS, ConnectOutcome, Connector, ConnectorError, parse_date, to_float

PAGE = 500
MOVE_FIELDS = ["name", "move_type", "partner_id", "amount_total", "amount_residual",
               "invoice_date", "invoice_date_due", "payment_state", "invoice_payments_widget"]


class OdooConnector(Connector):
    id = "odoo"
    name = "Odoo"

    def __init__(self) -> None:
        self.url = ""
        self.database = ""
        self.api_key = ""
        self.login = ""
        self.mode = ""      # "json2" | "jsonrpc"
        self.uid: Optional[int] = None
        self._client = httpx.Client(timeout=60)

    # ── connect ──
    def connect(self, req: ConnectRequest) -> ConnectOutcome:
        url = (req.url or "").strip().rstrip("/")
        if not (url and req.database and req.apiKey):
            raise ConnectorError("Odoo URL, database and API key are all required.")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.url, self.database, self.api_key = url, req.database.strip(), req.apiKey.strip()
        self.login = (req.login or "").strip()
        self.mode, self.uid = "", None

        try:
            self._probe()
        except httpx.HTTPError as e:
            self.disconnect()
            raise ConnectorError(f"Could not reach {url}: {e}", 502)
        except ConnectorError:
            self.disconnect()
            raise
        return ConnectOutcome(status="connected")

    def _probe(self) -> None:
        resp = self._client.post(f"{self.url}/json/2/account.move/search_count",
                                 headers=self._json2_headers(), json={"domain": []})
        if resp.status_code == 200:
            self.mode = "json2"
            return
        if resp.status_code in (401, 403):
            raise ConnectorError("Odoo rejected the API key (check the key, database name and that the user can read Accounting).", 401)
        if resp.status_code != 404:
            raise ConnectorError(f"Odoo JSON-2 probe failed (HTTP {resp.status_code}): {resp.text[:200]}", 502)
        # 404 -> pre-19 server: legacy JSON-RPC
        if not self.login:
            raise ConnectorError("This Odoo version (18 or older) needs the username/email the API key belongs to.", 400)
        uid = self._rpc("common", "authenticate", [self.database, self.login, self.api_key, {}])
        if not uid:
            raise ConnectorError("Odoo rejected the login/API key/database combination.", 401)
        self.uid, self.mode = int(uid), "jsonrpc"

    # ── transport ──
    def _json2_headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self.api_key}", "X-Odoo-Database": self.database,
                "Content-Type": "application/json"}

    def _rpc(self, service: str, method: str, args: list) -> Any:
        resp = self._client.post(f"{self.url}/jsonrpc", json={
            "jsonrpc": "2.0", "method": "call", "id": 1,
            "params": {"service": service, "method": method, "args": args}})
        try:
            body = resp.json()
        except ValueError:
            raise ConnectorError(f"Odoo returned a non-JSON response (HTTP {resp.status_code}).", 502)
        if "error" in body:
            err = body["error"].get("data", {}).get("message") or body["error"].get("message", "unknown error")
            raise ConnectorError(f"Odoo error: {err}", 400)
        return body.get("result")

    def _call(self, model: str, method: str, domain: Optional[list] = None, **kwargs: Any) -> Any:
        """search_read / search_count style call in whichever protocol connect() settled on."""
        domain = domain or []
        if self.mode == "json2":
            resp = self._client.post(f"{self.url}/json/2/{model}/{method}",
                                     headers=self._json2_headers(), json={"domain": domain, **kwargs})
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("message", resp.text[:200])
                except ValueError:
                    msg = resp.text[:200]
                raise ConnectorError(f"Odoo {model}.{method} failed (HTTP {resp.status_code}): {msg}", 502)
            return resp.json()
        if self.mode == "jsonrpc":
            return self._rpc("object", "execute_kw",
                             [self.database, self.uid, self.api_key, model, method, [domain], kwargs])
        raise ConnectorError("Odoo isn't connected.", 409)

    def _search_read(self, model: str, domain: list, fields: list[str], max_rows: int = 20000) -> list[dict]:
        rows: list[dict] = []
        while len(rows) < max_rows:
            batch = self._call(model, "search_read", domain, fields=fields, limit=PAGE, offset=len(rows), order="id")
            rows.extend(batch)
            if len(batch) < PAGE:
                break
        return rows

    # ── fetch + map ──
    def fetch(self) -> NormalizedData:
        cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        warnings: list[str] = []
        domain = [("move_type", "in", ["out_invoice", "in_invoice"]), ("state", "=", "posted"),
                  "|", ("invoice_date", ">=", cutoff), ("payment_state", "in", ["not_paid", "partial"])]
        moves = self._search_read("account.move", domain, MOVE_FIELDS)

        invoices: list[Invoice] = []
        bills: list[Bill] = []
        for m in moves:
            issue = parse_date(m.get("invoice_date"))
            if issue is None:
                continue
            pay_state = m.get("payment_state")
            status = "paid" if pay_state in ("paid", "in_payment") else "open"
            if pay_state == "reversed":
                status = "void"
            common = dict(
                id=str(m.get("name") or m["id"]), amount=abs(to_float(m.get("amount_total"))),
                issue_date=issue, due_date=parse_date(m.get("invoice_date_due")) or issue,
                status=status, balance=abs(to_float(m.get("amount_residual"))),
                paid_date=_last_payment_date(m.get("invoice_payments_widget")) if status == "paid" else None,
                source=self.id)
            partner = m["partner_id"][1] if m.get("partner_id") else "Unknown"
            if m["move_type"] == "out_invoice":
                invoices.append(Invoice(customer=partner, **common))
            else:
                bills.append(Bill(vendor=partner, **common))

        company, currency = None, ""
        try:
            co = self._search_read("res.company", [], ["name", "currency_id"], max_rows=1)
            if co:
                company = co[0]["name"]
                currency = co[0]["currency_id"][1] if co[0].get("currency_id") else currency
        except ConnectorError as e:
            warnings.append(f"Could not read company details ({e}).")

        balance, cash_known = self._cash(warnings)
        return NormalizedData(invoices=invoices, bills=bills, current_balance=balance, cash_known=cash_known,
                              currency=currency, company=company, warnings=warnings)

    def _cash(self, warnings: list[str]) -> tuple[float, bool]:
        # Odoo 17+: account_type='asset_cash'; Odoo <=16: user_type_id.type='liquidity'.
        for leaf in (("account_id.account_type", "=", "asset_cash"),
                     ("account_id.user_type_id.type", "=", "liquidity")):
            try:
                lines = self._search_read("account.move.line", [leaf, ("parent_state", "=", "posted")],
                                          ["balance"], max_rows=200000)
                if lines:
                    return round(sum(to_float(l.get("balance")) for l in lines), 2), True
                warnings.append("No posted bank/cash journal entries were found in Odoo, so cash-based figures are unavailable.")
                return 0.0, False
            except ConnectorError:
                continue
        warnings.append("Could not read bank/cash balances from Odoo; cash-based figures are unavailable.")
        return 0.0, False

    def disconnect(self) -> None:
        self.url = self.database = self.api_key = self.login = self.mode = ""
        self.uid = None


def _last_payment_date(widget: Any) -> Optional[date]:
    """`invoice_payments_widget` is a JSON blob: {"content": [{"date": "...", ...}]}."""
    if not widget:
        return None
    try:
        data = json.loads(widget) if isinstance(widget, str) else widget
        dates = [parse_date(p.get("date")) for p in (data or {}).get("content", [])]
    except (ValueError, AttributeError, TypeError):
        return None
    dates = [d for d in dates if d]
    return max(dates) if dates else None
