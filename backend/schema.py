"""Unified schema every connector normalizes into."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

ConnectorId = Literal["zoho", "netsuite", "odoo"]
Status = Literal["disconnected", "connecting", "connected", "error"]
Severity = Literal["high", "medium", "low"]


class Invoice(BaseModel):
    id: str
    customer: str
    amount: float
    issue_date: date
    due_date: date
    paid_date: Optional[date] = None
    status: Literal["open", "paid", "void"] = "open"
    # Amount still owed. Equals `amount` for unpaid invoices, 0 for paid ones.
    balance: Optional[float] = None
    source: str

    @property
    def outstanding(self) -> float:
        if self.status != "open":
            return 0.0
        return self.amount if self.balance is None else self.balance


class Bill(BaseModel):
    id: str
    vendor: str
    amount: float
    issue_date: date
    due_date: date
    paid_date: Optional[date] = None
    status: Literal["open", "paid", "void"] = "open"
    balance: Optional[float] = None
    source: str

    @property
    def outstanding(self) -> float:
        if self.status != "open":
            return 0.0
        return self.amount if self.balance is None else self.balance


class NormalizedData(BaseModel):
    invoices: list[Invoice] = Field(default_factory=list)
    bills: list[Bill] = Field(default_factory=list)
    current_balance: float = 0.0
    currency: str = ""
    company: Optional[str] = None
    # False when no bank/cash balance could be read: current_balance is then "unknown", not zero,
    # and anything cash-based (runway, shortfall) must not be presented.
    cash_known: bool = True
    # Non-fatal problems while reading the source (e.g. cash balance unreadable).
    warnings: list[str] = Field(default_factory=list)

    @property
    def record_count(self) -> int:
        return len(self.invoices) + len(self.bills)


class Finding(BaseModel):
    id: str
    type: str
    severity: Severity
    title: str
    amount_at_risk: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)
    explanation: Optional[str] = None
    action: Optional[str] = None


# ── request bodies ──
class ConnectRequest(BaseModel):
    """Free-form credentials from the connect dialog (Odoo: url/database/apiKey/login,
    NetSuite: account)."""
    url: Optional[str] = None
    database: Optional[str] = None
    apiKey: Optional[str] = None
    login: Optional[str] = None
    account: Optional[str] = None


class WhatIfRequest(BaseModel):
    collect_invoice_ids: list[str] = Field(default_factory=list)
    delay_bill_ids: list[str] = Field(default_factory=list)
    delay_days: int = 30


class EarlyDiscountRequest(BaseModel):
    invoice_ids: list[str] = Field(min_length=1)
    discount_pct: float = Field(gt=0, lt=100)
    pay_within_days: int = Field(ge=0)


class PaymentTermsRequest(BaseModel):
    collect_days_faster: int = Field(default=0, ge=0)
    pay_days_later: int = Field(default=0, ge=0)


class CustomerDefaultRequest(BaseModel):
    customers: list[str] = Field(min_length=1)


class DraftEmailRequest(BaseModel):
    invoice_id: str


class ChatRequest(BaseModel):
    question: str
