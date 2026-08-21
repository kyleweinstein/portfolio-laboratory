from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import (
    BalanceSnapshot,
    BrokerageAccount,
    CapabilityReport,
    CashActivity,
    OrderRecord,
    PositionSnapshot,
)


class WebullAdapterError(RuntimeError):
    """A sanitized error returned by the read-only brokerage adapter."""


class AdapterConfigurationError(WebullAdapterError):
    """The adapter cannot run until required runtime configuration is supplied."""


class ReadOnlyWebullAdapter(ABC):
    """The complete brokerage surface. Trading methods are intentionally absent."""

    @abstractmethod
    def probe(self, *, live: bool = False) -> CapabilityReport:
        raise NotImplementedError

    @abstractmethod
    def list_accounts(self) -> list[BrokerageAccount]:
        raise NotImplementedError

    @abstractmethod
    def get_balance(self, account_id: str) -> BalanceSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self, account_id: str) -> list[PositionSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_cash_activities(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[CashActivity]:
        raise NotImplementedError

    @abstractmethod
    def get_order_history(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[OrderRecord]:
        raise NotImplementedError
