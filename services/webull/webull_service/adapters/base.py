from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import (
    BalanceSnapshot,
    BrokerageAccount,
    BrokerProvider,
    CapabilityReport,
    CashActivity,
    OrderRecord,
    PositionSnapshot,
)


class BrokerAdapterError(RuntimeError):
    """A sanitized error returned by the read-only brokerage adapter."""


class WebullAdapterError(BrokerAdapterError):
    """Backward-compatible sanitized Webull adapter error."""


class AdapterConfigurationError(WebullAdapterError):
    """The adapter cannot run until required runtime configuration is supplied."""


class ReadOnlyBrokerAdapter(ABC):
    """Provider-neutral read surface. Trading and money movement are absent."""

    enforcement_mode = "unverified"
    provider = BrokerProvider.WEBULL

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


class ReadOnlyWebullAdapter(ReadOnlyBrokerAdapter):
    """Backward-compatible Webull specialization of the broker contract."""

    provider = BrokerProvider.WEBULL


def application_read_only_gate_enabled(*, configured: bool, adapter: object) -> bool:
    """Require an explicit opt-in on each concrete adapter implementation."""
    return bool(
        configured and type(adapter).__dict__.get("enforcement_mode") == "application"
    )
