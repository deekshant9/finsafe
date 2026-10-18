"""Connector interface + helpers shared by every source system."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from schema import ConnectRequest, NormalizedData

# Only look back this far for settled documents; open ones are always kept.
LOOKBACK_DAYS = 456  # ~15 months


class ConnectorError(Exception):
    """A user-presentable failure (bad credentials, missing config, API error)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ConnectOutcome:
    status: str = "connected"            # "connected" | "redirect"
    redirect_url: Optional[str] = None   # set when the browser must continue elsewhere


class Connector(ABC):
    id: str
    name: str

    @abstractmethod
    def connect(self, req: ConnectRequest) -> ConnectOutcome:
        """Validate credentials and prepare for fetch(). Raises ConnectorError."""

    @abstractmethod
    def fetch(self) -> NormalizedData:
        """Pull invoices, bills and cash balance, normalized to our schema."""

    def disconnect(self) -> None:
        """Drop in-memory credentials/tokens."""


def parse_date(value) -> Optional[date]:
    """Tolerant date parser for the formats the three systems emit.

    ISO (YYYY-MM-DD[...]) first; then US-style M/D/YYYY, which is what NetSuite
    returns when the account date format is MM/DD/YYYY."""
    if value in (None, "", False):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.strptime(text.split(" ")[0], "%m/%d/%Y").date()
    except ValueError:
        return None


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
