from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..models import (
    BalanceSnapshot,
    BrokerageAccount,
    CapabilityReport,
    CashActivity,
    OrderRecord,
    PositionSnapshot,
)
from .base import ReadOnlyWebullAdapter, WebullAdapterError


class FakeWebullAdapter(ReadOnlyWebullAdapter):
    """Deterministic adapter for tests and local contract validation."""

    def __init__(self, *, as_of: datetime | None = None) -> None:
        self.as_of = as_of or datetime(2026, 8, 20, 20, 0, tzinfo=UTC)
        self.fail_positions = False
        self.calls: list[tuple[str, str | None]] = []
        self.account = BrokerageAccount(
            account_id="fake-account-001",
            account_type="CASH",
            status="ACTIVE",
            currency="USD",
        )

    def advance(self, *, days: int = 0, hours: int = 0) -> None:
        self.as_of += timedelta(days=days, hours=hours)

    def probe(self, *, live: bool = False) -> CapabilityReport:
        self.calls.append(("probe", str(live)))
        return CapabilityReport(
            sdk_importable=True,
            credentials_configured=True,
            account_list=True,
            balances=True,
            positions=True,
            cash_activities=True,
            order_history=True,
            connected=live,
            message="Deterministic fake adapter",
        )

    def list_accounts(self) -> list[BrokerageAccount]:
        self.calls.append(("list_accounts", None))
        return [self.account]

    def get_balance(self, account_id: str) -> BalanceSnapshot:
        self._require_account(account_id)
        self.calls.append(("get_balance", account_id))
        return BalanceSnapshot(
            account_id=account_id,
            as_of=self.as_of,
            currency="USD",
            equity=Decimal("15000.00"),
            cash=Decimal("3000.00"),
            market_value=Decimal("12000.00"),
            buying_power=Decimal("3000.00"),
            day_profit_loss=Decimal("75.00"),
            unrealized_profit_loss=Decimal("900.00"),
        )

    def get_positions(self, account_id: str) -> list[PositionSnapshot]:
        self._require_account(account_id)
        self.calls.append(("get_positions", account_id))
        if self.fail_positions:
            raise WebullAdapterError("Fake position endpoint failure")
        return [
            PositionSnapshot(
                account_id=account_id,
                external_position_id="fake-pos-aapl",
                symbol="AAPL",
                instrument_type="EQUITY",
                currency="USD",
                quantity=Decimal(20),
                last_price=Decimal(250),
                market_value=Decimal(5000),
                average_cost=Decimal(220),
                cost_basis=Decimal(4400),
                unrealized_profit_loss=Decimal(600),
            ),
            PositionSnapshot(
                account_id=account_id,
                external_position_id="fake-pos-spy",
                symbol="SPY",
                instrument_type="ETF",
                currency="USD",
                quantity=Decimal(10),
                last_price=Decimal(700),
                market_value=Decimal(7000),
                average_cost=Decimal(670),
                cost_basis=Decimal(6700),
                unrealized_profit_loss=Decimal(300),
            ),
        ]

    def get_order_history(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[OrderRecord]:
        self._require_account(account_id)
        self.calls.append(("get_order_history", account_id))
        order = OrderRecord(
            account_id=account_id,
            external_order_id="fake-order-001",
            client_order_id="fake-client-order-001",
            symbol="AAPL",
            instrument_type="EQUITY",
            side="BUY",
            status="FILLED",
            order_type="LIMIT",
            total_quantity=Decimal(20),
            filled_quantity=Decimal(20),
            average_fill_price=Decimal(220),
            placed_at=self.as_of - timedelta(days=90, minutes=1),
            filled_at=self.as_of - timedelta(days=90),
            commission=Decimal(0),
            fees=Decimal("0.02"),
        )
        event_time = order.filled_at or order.placed_at
        return [
            order
            for _ in [0]
            if (since is None or (event_time is not None and event_time >= since))
            and (until is None or (event_time is not None and event_time <= until))
        ]

    def get_cash_activities(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[CashActivity]:
        self._require_account(account_id)
        self.calls.append(("get_cash_activities", account_id))
        values = [
            CashActivity(
                account_id=account_id,
                external_activity_id="fake-deposit-001",
                activity_type="DEPOSIT",
                occurred_at=self.as_of - timedelta(days=30),
                amount=Decimal(1000),
                currency="USD",
                status="COMPLETED",
                description="External cash deposit",
                is_external_flow=True,
            ),
            CashActivity(
                account_id=account_id,
                external_activity_id="fake-dividend-001",
                activity_type="DIVIDEND",
                occurred_at=self.as_of - timedelta(days=5),
                amount=Decimal("12.50"),
                currency="USD",
                status="COMPLETED",
                description="Cash dividend",
                is_external_flow=False,
            ),
        ]
        return [
            item
            for item in values
            if (since is None or item.occurred_at >= since)
            and (until is None or item.occurred_at <= until)
        ]

    def _require_account(self, account_id: str) -> None:
        if account_id != self.account.account_id:
            raise WebullAdapterError("Unknown fake account")
