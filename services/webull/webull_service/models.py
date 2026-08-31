from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


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


class BrokerProvider(str, Enum):
    WEBULL = "webull"
    PLAID_M1 = "plaid_m1"
    SCHWAB = "schwab"


class BrokerOAuthCredential(NormalizedModel):
    """Private provider-neutral OAuth material; never an API response model."""

    provider: BrokerProvider
    access_token: SecretStr
    refresh_token: SecretStr | None = None
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None

    @field_validator("access_token", "refresh_token")
    @classmethod
    def reject_empty_tokens(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            raise ValueError("OAuth tokens cannot be empty.")
        return value

    @field_validator("access_token_expires_at", "refresh_token_expires_at")
    @classmethod
    def normalize_token_expiry(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class DiscordSessionData(NormalizedModel):
    """Private viewer-session material; never a browser response model."""

    discord_id: str = Field(pattern=r"^[1-9]\d*$", max_length=32)
    username: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    guild_id: str = Field(pattern=r"^[1-9]\d*$", max_length=32)
    access_token: SecretStr
    refresh_token: SecretStr
    token_expires_at: datetime
    membership_checked_at: datetime
    expires_at: datetime

    @field_validator("token_expires_at", "membership_checked_at", "expires_at")
    @classmethod
    def normalize_discord_session_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("access_token", "refresh_token")
    @classmethod
    def reject_empty_discord_tokens(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("Discord OAuth tokens cannot be empty.")
        return value


class DiscordSessionRequest(NormalizedModel):
    session_id: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class DiscordSessionWriteRequest(DiscordSessionRequest):
    session: DiscordSessionData


class CashSource(str, Enum):
    EXPLICIT = "explicit"
    DERIVED_RESIDUAL = "derived_residual"
    UNAVAILABLE = "unavailable"


class BrokerageAccount(NormalizedModel):
    account_id: str = Field(min_length=1, max_length=128)
    provider: BrokerProvider = BrokerProvider.WEBULL
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
    cash_source: CashSource = CashSource.EXPLICIT

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
    provider: BrokerProvider = BrokerProvider.WEBULL
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


class ProviderCapabilities(NormalizedModel):
    provider: BrokerProvider
    enabled: bool
    configured: bool
    read_only: bool = True
    accounts: bool
    balances: bool
    positions: bool
    activities: bool
    authoritative_performance: bool = False
    on_demand_refresh: bool = False
    statement_anchors: bool = True
    status: str
    message: str | None = Field(default=None, max_length=500)


class ProviderCapabilityList(NormalizedModel):
    providers: tuple[ProviderCapabilities, ...]


class BrokerageAccountReference(NormalizedModel):
    account_handle: str
    provider: BrokerProvider
    account_type: str
    currency: str
    last_synced_at: datetime | None = None


class BrokerageAccountReferenceList(NormalizedModel):
    accounts: tuple[BrokerageAccountReference, ...]


class PublishedHoldingKind(str, Enum):
    SECURITY = "security"
    CASH_MARGIN = "cash_margin"
    OTHER = "other"


class PublicationQuality(str, Enum):
    BROKER_REPORTED = "broker_reported"
    STATEMENT_RECONCILED = "statement_reconciled"
    PORTFOLIO_LAB_COMPUTED = "portfolio_lab_computed"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class PublishedHolding(NormalizedModel):
    kind: PublishedHoldingKind
    symbol: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    weight_percent: Decimal | None
    cost_basis_per_share: Decimal | None = None
    return_percent: Decimal | None = None
    quality: PublicationQuality

    @field_validator("symbol")
    @classmethod
    def normalize_optional_symbol(cls, value: str | None) -> str | None:
        return value.upper() if value else None


class PublishedPerformancePoint(NormalizedModel):
    at: datetime
    return_percent: Decimal
    benchmark_return_percent: Decimal | None = None
    quality: PublicationQuality

    @field_validator("at")
    @classmethod
    def normalize_performance_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PublishedRiskStatistics(NormalizedModel):
    annual_return_percent: float | None = None
    annual_volatility_percent: float | None = None
    sharpe_ratio: float | None = None
    maximum_drawdown_percent: float | None = None
    value_at_risk_95_percent: float | None = None
    conditional_value_at_risk_95_percent: float | None = None
    beta: float | None = None


class PublishedClassification(NormalizedModel):
    symbol: str = Field(pattern=r"^[A-Z0-9.^=/-]{1,30}$")
    style: str | None = Field(default=None, max_length=80)
    sector: str | None = Field(default=None, max_length=80)
    factor: str | None = Field(default=None, max_length=80)
    confidence: Literal["high", "moderate", "low", "unavailable"]


class PublishedPairInsight(NormalizedModel):
    left_symbol: str = Field(pattern=r"^[A-Z0-9.^=/-]{1,30}$")
    right_symbol: str = Field(pattern=r"^[A-Z0-9.^=/-]{1,30}$")
    correlation: float
    spread_volatility_percent: float
    rebalance_potential_percent: float


class PublishedAllocation(NormalizedModel):
    symbol: str = Field(pattern=r"^[A-Z0-9.^=/-]{1,30}$")
    weight_percent: float


class PublishedCorrelationMap(NormalizedModel):
    symbols: tuple[str, ...]
    packed_correlations: tuple[float, ...]

    @model_validator(mode="after")
    def validate_packed_triangle(self):
        expected = len(self.symbols) * (len(self.symbols) + 1) // 2
        if len(self.symbols) < 2 or len(self.packed_correlations) != expected:
            raise ValueError("Correlation data must be a packed upper triangle.")
        return self


class PublishedDirectionLane(NormalizedModel):
    symbol: str = Field(pattern=r"^[A-Z0-9.^=/-]{1,30}$")
    up_share_percent: float
    directions: tuple[Literal[-1, 0, 1], ...]


class PublishedDirectionComparison(NormalizedModel):
    dates: tuple[str, ...]
    lanes: tuple[PublishedDirectionLane, ...]

    @model_validator(mode="after")
    def validate_lane_lengths(self):
        if not self.dates or not 2 <= len(self.lanes) <= 6:
            raise ValueError("Direction comparison requires two to six lanes.")
        if any(len(lane.directions) != len(self.dates) for lane in self.lanes):
            raise ValueError("Direction lanes must align with the published dates.")
        return self


class PublishedRebalanceBucket(NormalizedModel):
    symbols: tuple[str, ...]
    target_weights_percent: tuple[float, ...]
    price_implied_weights_percent: tuple[float, ...]
    drift_percent: float
    triggered: bool

    @model_validator(mode="after")
    def validate_weight_lengths(self):
        if (
            not self.symbols
            or len(self.target_weights_percent) != len(self.symbols)
            or len(self.price_implied_weights_percent) != len(self.symbols)
        ):
            raise ValueError("Rebalance weights must match bucket symbols.")
        return self


class PublishedAnalytics(NormalizedModel):
    risk: PublishedRiskStatistics | None = None
    classifications: tuple[PublishedClassification, ...] = ()
    pair_insights: tuple[PublishedPairInsight, ...] = ()
    optimized_allocation: tuple[PublishedAllocation, ...] = ()
    correlation_map: PublishedCorrelationMap | None = None
    direction_comparison: PublishedDirectionComparison | None = None
    rebalance_buckets: tuple[PublishedRebalanceBucket, ...] = ()
    analytics_as_of: datetime | None = None
    observation_count: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("analytics_as_of")
    @classmethod
    def normalize_analytics_time(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class PortfolioCard(NormalizedModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    title: str = Field(min_length=1, max_length=120)
    provider: BrokerProvider
    ytd_return_percent: Decimal | None
    performance_through: datetime | None
    quality: PublicationQuality
    performance_points: tuple[PublishedPerformancePoint, ...] = ()


class PortfolioDetail(NormalizedModel):
    card: PortfolioCard
    holdings_as_of: datetime
    holdings: tuple[PublishedHolding, ...]
    gross_exposure_percent: Decimal | None
    net_exposure_percent: Decimal | None
    analytical_sleeve_percent: Decimal | None
    benchmark_symbol: str = "SPY"
    analytics: PublishedAnalytics | None = None

    @field_validator("holdings_as_of")
    @classmethod
    def normalize_holdings_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PublishedPortfolioList(NormalizedModel):
    portfolios: tuple[PortfolioCard, ...]


class PublicationConfig(NormalizedModel):
    publication_id: str
    account_handle: str
    account_id: str = Field(exclude=True)
    provider: BrokerProvider
    slug: str
    title: str
    benchmark_symbol: str
    enabled: bool
    has_published_revision: bool
    updated_at: datetime


class ManagedPublicationList(NormalizedModel):
    publications: tuple[PublicationConfig, ...]


class PublicationConfigurationRequest(NormalizedModel):
    account_handle: str | None = Field(default=None, min_length=1, max_length=64)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    title: str = Field(min_length=1, max_length=120)
    benchmark_symbol: str = Field(default="SPY", min_length=1, max_length=32)
    enabled: bool = False

    @field_validator("benchmark_symbol")
    @classmethod
    def uppercase_benchmark(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def require_one_account_reference(self):
        if bool(self.account_handle) == bool(self.account_id):
            raise ValueError("Provide exactly one accountHandle or accountId.")
        return self


class PublicationBuildRequest(NormalizedModel):
    """Owner-triggered build request; analytics are always generated server-side."""


class PlaidLinkTokenResponse(NormalizedModel):
    link_token: str
    expiration: datetime

    @field_validator("expiration")
    @classmethod
    def normalize_expiration(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PlaidExchangeRequest(NormalizedModel):
    public_token: str = Field(min_length=1, max_length=512)


class PlaidWebhookEvent(NormalizedModel):
    webhook_type: str = Field(min_length=1, max_length=64)
    webhook_code: str = Field(min_length=1, max_length=64)
    item_id: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=32)


class PlaidSignedWebhook(NormalizedModel):
    raw_body: str = Field(min_length=2, max_length=131_072)
    signature: str = Field(min_length=16, max_length=4096)


class WebhookAcknowledgement(NormalizedModel):
    accepted: bool
    sync_required: bool


class SyncResult(NormalizedModel):
    sync_run_id: str
    snapshot_id: str
    account_id: str
    captured_at: datetime
    positions_written: int
    cash_activities_seen: int
    cash_activities_upserted: int
    cash_activities_complete: bool = True
    warning: str | None = Field(default=None, max_length=500)

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


class SyncAttemptStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class SyncAttempt(NormalizedModel):
    sync_run_id: str
    status: SyncAttemptStatus
    started_at: datetime
    completed_at: datetime
    cash_activities_complete: bool | None = None
    message: str | None = Field(default=None, max_length=500)

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_sync_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class VerificationState(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class VerificationStage(str, Enum):
    STARTING = "starting"
    VERIFYING_ACCESS = "verifying_access"
    DISCOVERING_ACCOUNTS = "discovering_accounts"
    SYNCING_ACCOUNT = "syncing_account"
    FINALIZING = "finalizing"
    COMPLETE = "complete"


class VerificationError(NormalizedModel):
    code: str = Field(max_length=64)
    message: str = Field(max_length=500)


class VerificationAttempt(NormalizedModel):
    attempt_id: str
    state: VerificationState
    stage: VerificationStage
    started_at: datetime
    updated_at: datetime
    lease_expires_at: datetime
    completed_at: datetime | None = None
    error: VerificationError | None = None

    @field_validator("started_at", "updated_at", "lease_expires_at", "completed_at")
    @classmethod
    def normalize_verification_time(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class ServiceNextAction(str, Enum):
    CONFIGURE = "configure"
    START_VERIFICATION = "start_verification"
    WAIT = "wait"
    RETRY_VERIFICATION = "retry_verification"
    SYNC_ACCOUNT = "sync_account"
    VIEW_PORTFOLIO = "view_portfolio"


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
    verification_in_progress: bool = False
    verification: VerificationAttempt | None = None
    next_action: ServiceNextAction = ServiceNextAction.START_VERIFICATION
    accounts: tuple[BrokerageAccount, ...]
    selected_account_id: str | None
    last_synced_at: datetime | None
    last_sync_attempt: SyncAttempt | None = None
    dashboard: DashboardState | None


class BackfillResult(NormalizedModel):
    account_id: str
    requested_days: int
    cash_activities_seen: int
    cash_activities_upserted: int
    cash_activities_available: bool = True
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
    webull_read_only_adapter_enabled: bool
    authentication_configured: bool


JsonObject = dict[str, Any]
