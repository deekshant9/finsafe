"""In-memory, single-tenant state: connector status + the normalized data each source produced.

Deliberately simple (build plan: no database). Run a single worker process."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from connectors.base import Connector
from connectors.netsuite import NetSuiteConnector
from connectors.odoo import OdooConnector
from connectors.zoho import ZohoConnector
from schema import NormalizedData

AUTH_LABELS = {"zoho": "OAuth 2.0", "netsuite": "OAuth 2.0", "odoo": "API key"}


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.connectors: dict[str, Connector] = {c.id: c for c in (ZohoConnector(), NetSuiteConnector(), OdooConnector())}
        self.status: dict[str, str] = {cid: "disconnected" for cid in self.connectors}
        self.errors: dict[str, Optional[str]] = {cid: None for cid in self.connectors}
        self.datasets: dict[str, NormalizedData] = {}
        self.synced_at: dict[str, datetime] = {}
        self.explain_cache: dict[str, tuple[dict, bool]] = {}

    # ── connector state ──
    def set_synced(self, cid: str, data: NormalizedData) -> None:
        with self.lock:
            self.datasets[cid] = data
            self.synced_at[cid] = datetime.now(timezone.utc)
            self.status[cid], self.errors[cid] = "connected", None

    def set_error(self, cid: str, message: str) -> None:
        with self.lock:
            self.status[cid], self.errors[cid] = "error", message
            self.datasets.pop(cid, None)

    def clear(self, cid: str) -> None:
        with self.lock:
            self.connectors[cid].disconnect()
            self.datasets.pop(cid, None)
            self.synced_at.pop(cid, None)
            self.status[cid], self.errors[cid] = "disconnected", None

    def describe(self) -> dict:
        with self.lock:
            items = []
            for cid, c in self.connectors.items():
                ds = self.datasets.get(cid)
                at = self.synced_at.get(cid)
                items.append({"id": cid, "name": c.name, "auth": AUTH_LABELS[cid], "status": self.status[cid],
                              "records_synced": ds.record_count if ds else 0,
                              "last_synced": at.isoformat() if at else None, "error": self.errors[cid]})
            return {"connectors": items, "ready_for_analysis": bool(self.datasets)}

    # ── what the engines see ──
    def active(self) -> Optional[NormalizedData]:
        """Everything the connected sources produced, merged. None until something is synced."""
        with self.lock:
            if self.datasets:
                parts = list(self.datasets.values())
                merged = NormalizedData(
                    invoices=[i for p in parts for i in p.invoices], bills=[b for p in parts for b in p.bills],
                    current_balance=sum(p.current_balance for p in parts), currency=parts[0].currency,
                    company=" + ".join(p.company or "" for p in parts if p.company) or None,
                    warnings=[w for p in parts for w in p.warnings])
                return merged
            return None


store = Store()
