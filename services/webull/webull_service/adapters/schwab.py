from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from ..models import (
    BalanceSnapshot,
    BrokerageAccount,
    BrokerProvider,
    CapabilityReport,
    CashActivity,
    CashSource,
    PositionSnapshot,
)
from .base import BrokerAdapterError

SchwabReadOperation = Literal["accounts", "balance", "positions", "transactions"]


class SchwabRequestThrottle(Protocol):
    """Injected limiter; every future Schwab read must acquire before I/O."""

    def acquire(self, operation: SchwabReadOperation) -> None: ...


class SchwabReadClient(Protocol):
    """Narrow future client surface. Trading and money movement are absent."""

    def get_accounts(self) -> list[dict[str, Any]]: ...
    def get_account(self, account_hash: str) -> dict[str, Any]: ...
    def get_transactions(
        self,
        account_hash: str,
        *,
        since: datetime | None,
        until: datetime | None,
    ) -> list[dict[str, Any]]: ...


class ReadOnlySchwabAdapter:
    """Disabled readiness adapter around an injected read-only client.

    It is deliberately not wired into the service factory and has no HTTP/OAuth
    implementation. A future release must explicitly enable the instance and
    supply a reviewed client plus a real request throttle.
    """

    provider = BrokerProvider.SCHWAB

    def __init__(
        self,
        *,
        client: SchwabReadClient,
        throttle: SchwabRequestThrottle,
        enabled: bool = False,
    ) -> None:
        self._client = client
        self._throttle = throttle
        self._enabled = enabled

    def probe(self, *, live: bool = False) -> CapabilityReport:
        if live:
            self._require_enabled()
        return CapabilityReport(
            provider=BrokerProvider.SCHWAB,
            sdk_importable=True,
            credentials_configured=False,
            account_list=True,
            balances=True,
            positions=True,
            cash_activities=True,
            order_history=False,
            connected=False,
            message="Schwab read mappings are ready; live OAuth and reads are disabled.",
        )

    def list_accounts(self) -> list[BrokerageAccount]:
        self._require_enabled()
        self._throttle.acquire("accounts")
        try:
            rows = self._client.get_accounts()
            return [map_schwab_account(row) for row in rows]
        except BrokerAdapterError:
            raise
        except Exception:  # noqa: BLE001 - sanitize the injected client boundary.
            raise BrokerAdapterError("Schwab account list request failed.") from None

    def get_balance(self, account_id: str) -> BalanceSnapshot:
        payload = self._account_payload(account_id, "balance")
        return map_schwab_balance(account_id, payload, captured_at=datetime.now(UTC))

    def get_positions(self, account_id: str) -> list[PositionSnapshot]:
        payload = self._account_payload(account_id, "positions")
        return map_schwab_positions(account_id, payload)

    def get_cash_activities(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[CashActivity]:
        self._require_enabled()
        account_hash = _external_account_hash(account_id)
        self._throttle.acquire("transactions")
        try:
            rows = self._client.get_transactions(account_hash, since=since, until=until)
            return map_schwab_transactions(account_id, rows)
        except BrokerAdapterError:
            raise
        except Exception:  # noqa: BLE001 - sanitize the injected client boundary.
            raise BrokerAdapterError("Schwab transaction request failed.") from None

    def _account_payload(
        self,
        account_id: str,
        operation: Literal["balance", "positions"],
    ) -> dict[str, Any]:
        self._require_enabled()
        account_hash = _external_account_hash(account_id)
        self._throttle.acquire(operation)
        try:
            return self._client.get_account(account_hash)
        except BrokerAdapterError:
            raise
        except Exception:  # noqa: BLE001 - sanitize the injected client boundary.
            raise BrokerAdapterError(f"Schwab {operation} request failed.") from None

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise BrokerAdapterError("Schwab live access is disabled.")


def map_schwab_account(payload: dict[str, Any]) -> BrokerageAccount:
    account = _securities_account(payload)
    account_hash = str(
        payload.get("hashValue")
        or payload.get("accountHash")
        or account.get("hashValue")
        or account.get("accountHash")
        or ""
    ).strip()
    if not account_hash:
        raise BrokerAdapterError("Schwab did not return an opaque account hash.")
    return BrokerageAccount(
        account_id=_qualified_account_id(account_hash),
        provider=BrokerProvider.SCHWAB,
        account_type=str(account.get("type") or "UNKNOWN")[:64],
        status="ACTIVE" if not account.get("isClosingOnly") else "CLOSING_ONLY",
        currency=str(account.get("currency") or "USD"),
    )


def map_schwab_balance(
    account_id: str,
    payload: dict[str, Any],
    *,
    captured_at: datetime,
) -> BalanceSnapshot:
    account = _securities_account(payload)
    balances = _mapping(account.get("currentBalances"))
    equity = _decimal(balances.get("liquidationValue"), "account value")
    cash = _signed_cash_or_margin(balances)
    long_value = _optional_decimal(balances.get("longMarketValue"), "long value")
    short_value = _optional_decimal(balances.get("shortMarketValue"), "short value")
    if long_value is None and short_value is None:
        market_value = equity - cash
    else:
        long_value = long_value or Decimal(0)
        short_value = short_value or Decimal(0)
        if short_value > 0:
            short_value = -short_value
        market_value = long_value + short_value
    return BalanceSnapshot(
        account_id=account_id,
        as_of=captured_at,
        currency=str(account.get("currency") or "USD"),
        equity=equity,
        cash=cash,
        market_value=market_value,
        buying_power=_optional_decimal(balances.get("buyingPower"), "buying power"),
        day_profit_loss=_optional_decimal(
            balances.get("dayProfitLoss"), "day profit or loss"
        ),
        unrealized_profit_loss=_optional_decimal(
            balances.get("unrealizedProfitLoss"), "unrealized profit or loss"
        ),
        cash_source=CashSource.EXPLICIT,
    )


def map_schwab_positions(
    account_id: str, payload: dict[str, Any]
) -> list[PositionSnapshot]:
    account = _securities_account(payload)
    positions: list[PositionSnapshot] = []
    for index, row in enumerate(_dict_items(account.get("positions"))):
        instrument = _mapping(row.get("instrument"))
        symbol = str(instrument.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        long_quantity = _decimal_default(row.get("longQuantity"))
        short_quantity = _decimal_default(row.get("shortQuantity"))
        quantity = long_quantity - short_quantity
        if quantity == 0:
            continue
        market_value = _decimal(row.get("marketValue"), "position market value")
        if quantity < 0 and market_value > 0:
            market_value = -market_value
        average_cost = _optional_decimal(row.get("averagePrice"), "average price")
        last_price = _optional_decimal(row.get("currentPrice"), "current price")
        if last_price is None:
            last_price = abs(market_value / quantity)
        positions.append(
            PositionSnapshot(
                account_id=account_id,
                external_position_id=str(
                    instrument.get("cusip") or f"{symbol}:{index}"
                )[:128],
                symbol=symbol[:32],
                instrument_type=_schwab_instrument_type(instrument),
                currency=str(
                    instrument.get("currency") or account.get("currency") or "USD"
                ),
                quantity=quantity,
                last_price=last_price,
                market_value=market_value,
                average_cost=average_cost,
                cost_basis=(
                    average_cost * abs(quantity) if average_cost is not None else None
                ),
                unrealized_profit_loss=_optional_decimal(
                    row.get("unrealizedProfitLoss"), "unrealized profit or loss"
                ),
            )
        )
    return positions


def map_schwab_transactions(
    account_id: str, rows: list[dict[str, Any]]
) -> list[CashActivity]:
    activities: list[CashActivity] = []
    for row in rows:
        activity_id = str(row.get("activityId") or "").strip()
        if not activity_id:
            continue
        occurred_at = _timestamp(row.get("time"))
        if occurred_at is None:
            continue
        activity_type = str(row.get("type") or "UNKNOWN").strip().upper()
        activities.append(
            CashActivity(
                account_id=account_id,
                external_activity_id=activity_id[:128],
                activity_type=activity_type[:64],
                occurred_at=occurred_at,
                amount=_decimal(row.get("netAmount"), "transaction amount"),
                currency=str(row.get("currency") or "USD"),
                status=str(row.get("status") or "POSTED")[:64],
                description=str(row.get("description") or activity_type)[:500],
                is_external_flow=_is_external_flow(activity_type),
            )
        )
    return activities


def _signed_cash_or_margin(balances: dict[str, Any]) -> Decimal:
    cash = _optional_decimal(balances.get("cashBalance"), "cash balance")
    margin = _optional_decimal(balances.get("marginBalance"), "margin balance")
    if cash is None and margin is None:
        raise BrokerAdapterError("Schwab did not return cash or margin balance.")
    cash = cash or Decimal(0)
    if cash < 0 or margin is None or margin == 0:
        return cash
    return cash - abs(margin)


def _is_external_flow(activity_type: str) -> bool:
    return activity_type in {
        "ACH_RECEIPT",
        "ACH_DISBURSEMENT",
        "CASH_RECEIPT",
        "CASH_DISBURSEMENT",
        "ELECTRONIC_FUND",
        "WIRE_IN",
        "WIRE_OUT",
        "RECEIVE_AND_DELIVER",
    }


def _qualified_account_id(account_hash: str) -> str:
    return f"schwab:{account_hash}"


def _external_account_hash(account_id: str) -> str:
    prefix = "schwab:"
    if not account_id.startswith(prefix) or len(account_id) <= len(prefix):
        raise BrokerAdapterError("The selected account is not a Schwab account.")
    return account_id[len(prefix) :]


def _securities_account(payload: dict[str, Any]) -> dict[str, Any]:
    wrapped = payload.get("securitiesAccount")
    if isinstance(wrapped, dict):
        return wrapped
    return payload


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _decimal(value: Any, field: str) -> Decimal:
    if value is None:
        raise BrokerAdapterError(f"Schwab did not return a valid {field}.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BrokerAdapterError(f"Schwab did not return a valid {field}.") from None
    if not parsed.is_finite():
        raise BrokerAdapterError(f"Schwab did not return a valid {field}.")
    return parsed


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    return None if value is None else _decimal(value, field)


def _decimal_default(value: Any) -> Decimal:
    return Decimal(0) if value is None else _decimal(value, "position quantity")


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def _schwab_instrument_type(instrument: dict[str, Any]) -> str:
    asset_type = str(instrument.get("assetType") or "EQUITY").upper()
    return {"COLLECTIVE_INVESTMENT": "ETF"}.get(asset_type, asset_type)[:64]
