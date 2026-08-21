from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _camel_case(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class NormalizedModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class BrokerageAccount(NormalizedModel):
    account_id: str = Field(min_length=1, max_length=128)
    account_type: str = Field(default="UNKNOWN", max_length=64)
    status: str = Field(default="ACTIVE", max_length=64)
    currency: str = Field(default="USD", min_length=3, max_length=8)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class BalanceSnapshot(NormalizedModel):
    account_id: str
    as_of: datetime
    currency: str = "USD"
    equity: Decimal
    cash: Decimal
    market_value: Decimal
    buying_power: Decimal | None = None
    day_profit_loss: Decimal | None = None
    unrealized_profit_loss: Decimal | None = None

    @field_validator("as_of")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class PositionSnapshot(NormalizedModel):
    account_id: str
    external_position_id: str
    symbol: str
    instrument_type: str = "EQUITY"
    currency: str = "USD"
    quantity: Decimal
    last_price: Decimal
    market_value: Decimal
    average_cost: Decimal | None = None
    cost_basis: Decimal | None = None
    unrealized_profit_loss: Decimal | None = None

    @field_validator("symbol", "instrument_type", "currency")
    @classmethod
    def uppercase_identifiers(cls, value: str) -> str:
        return value.upper()


class CashActivity(NormalizedModel):
    account_id: str
    external_activity_id: str
    activity_type: str
    occurred_at: datetime
    amount: Decimal
    currency: str = "USD"
    status: str | None = None
    description: str | None = None
    is_external_flow: bool = False

    @field_validator("occurred_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("activity_type", "currency")
    @classmethod
    def uppercase_identifiers(cls, value: str) -> str:
        return value.upper()


class OrderRecord(NormalizedModel):
    """Read-only aggregate order/fill record; never an order instruction."""

    account_id: str
    external_order_id: str
    client_order_id: str | None = None
    symbol: str
    instrument_type: str
    side: str
    status: str
    order_type: str | None = None
    total_quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal | None = None
    placed_at: datetime | None = None
    filled_at: datetime | None = None
    commission: Decimal | None = None
    fees: Decimal | None = None

    @field_validator("symbol", "instrument_type", "side", "status")
    @classmethod
    def uppercase_identifiers(cls, value: str) -> str:
        return value.upper()

    @field_validator("placed_at", "filled_at")
    @classmethod
    def normalize_optional_time(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class StatementAnchor(NormalizedModel):
    """Validated statement total supplied by a caller; no PDF parsing is performed."""

    account_id: str
    external_statement_id: str
    statement_date: datetime
    ending_equity: Decimal
    currency: str = "USD"
    ending_cash: Decimal | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("statement_date")
    @classmethod
    def normalize_statement_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("currency")
    @classmethod
    def normalize_statement_currency(cls, value: str) -> str:
        return value.upper()


class CapabilityReport(NormalizedModel):
    provider: str = "webull"
    read_only: bool = True
    sdk_importable: bool
    credentials_configured: bool
    account_list: bool
    balances: bool
    positions: bool
    cash_activities: bool
    order_history: bool
    statement_anchors: bool = True
    connected: bool = False
    message: str | None = None


class SyncResult(NormalizedModel):
    sync_run_id: str
    snapshot_id: str
    account_id: str
    captured_at: datetime
    positions_written: int
    cash_activities_seen: int
    cash_activities_upserted: int

    @field_validator("captured_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PortfolioState(NormalizedModel):
    account: BrokerageAccount
    balance: BalanceSnapshot
    positions: tuple[PositionSnapshot, ...]
    snapshot_id: str


class ConnectionState(NormalizedModel):
    connected: bool
    accounts: tuple[BrokerageAccount, ...]
    selected_account_id: str | None
    connected_at: datetime | None = None
    last_synced_at: datetime | None = None


class DataIssue(NormalizedModel):
    code: str
    severity: str
    message: str


class ValuationPoint(NormalizedModel):
    at: datetime
    value: Decimal
    source: str = "webull_snapshot"

    @field_validator("at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PerformancePeriod(NormalizedModel):
    start: datetime
    end: datetime
    beginning_value: Decimal
    ending_value: Decimal
    net_external_flow: Decimal
    modified_dietz_return: float | None


class PerformanceReport(NormalizedModel):
    account_id: str
    start: datetime | None
    end: datetime | None
    periods: tuple[PerformancePeriod, ...]
    time_weighted_return: float | None
    money_weighted_return: float | None
    net_external_flow: Decimal
    beginning_value: Decimal | None
    ending_value: Decimal | None
    quality: str
    methodology: str = "daily_modified_dietz"


class DashboardState(NormalizedModel):
    portfolio: PortfolioState | None
    performance: PerformanceReport | None
    recent_activities: tuple[CashActivity, ...]
    issues: tuple[DataIssue, ...]


class ServiceStatus(NormalizedModel):
    connected: bool
    accounts: tuple[BrokerageAccount, ...]
    selected_account_id: str | None
    last_synced_at: datetime | None
    dashboard: DashboardState | None


class BackfillResult(NormalizedModel):
    account_id: str
    requested_days: int
    cash_activities_seen: int
    cash_activities_upserted: int
    order_records_seen: int
    order_records_upserted: int
    order_history_available: bool
    valuation_history_available: bool = False
    message: str


class HealthReport(NormalizedModel):
    status: str
    service: str = "portfolio-lab-webull"
    database_configured: bool
    webull_credentials_configured: bool
    webull_read_only_scope_confirmed: bool
    authentication_configured: bool


JsonObject = dict[str, Any]
