from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..models import (
    BalanceSnapshot,
    BrokerageAccount,
    CapabilityReport,
    CashActivity,
    OrderRecord,
    PositionSnapshot,
    ensure_utc,
    utc_now,
)
from .base import AdapterConfigurationError, ReadOnlyWebullAdapter, WebullAdapterError

_DEPOSIT_TYPES = {
    "DEPOSIT",
    "ACH_DEPOSIT",
    "WIRE_DEPOSIT",
    "TRANSFER_IN",
    "CASH_TRANSFER_IN",
    "JOURNAL_IN",
}
_WITHDRAWAL_TYPES = {
    "WITHDRAWAL",
    "ACH_WITHDRAWAL",
    "WIRE_WITHDRAWAL",
    "TRANSFER_OUT",
    "CASH_TRANSFER_OUT",
    "JOURNAL_OUT",
}


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WebullAdapterError(
            "Webull returned a non-numeric account value."
        ) from exc


def _datetime(value: Any, default: datetime | None = None) -> datetime:
    if value is None or value == "":
        return default or utc_now()
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError as exc:
        raise WebullAdapterError(
            "Webull returned an invalid activity timestamp."
        ) from exc


def _first(value: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return default


def _items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            nested = _items(candidate, *keys, "items", "list", "records")
            if nested:
                return nested
    return []


def _stable_id(prefix: str, value: dict[str, Any], fields: tuple[str, ...]) -> str:
    material = {field: value.get(field) for field in fields}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:32]}"


class OfficialWebullAdapter(ReadOnlyWebullAdapter):
    """Read-only adapter for Webull's official OpenAPI Python SDK.

    Only account, balance, position and cash-activity SDK surfaces are imported.
    The class deliberately exposes no order client and no trading operation.
    """

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        region: str = "us",
        endpoint: str = "api.webull.com",
        token_dir: str | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._region = region
        self._endpoint = endpoint
        self._token_dir = token_dir
        self._trade_client: Any | None = None

    @property
    def credentials_configured(self) -> bool:
        return bool(self._app_key and self._app_secret)

    def probe(self, *, live: bool = False) -> CapabilityReport:
        sdk_importable = importlib.util.find_spec("webull") is not None
        base = {
            "sdk_importable": sdk_importable,
            "credentials_configured": self.credentials_configured,
            "account_list": sdk_importable,
            "balances": sdk_importable,
            "positions": sdk_importable,
            "cash_activities": sdk_importable,
            "order_history": sdk_importable,
        }
        if not live:
            message = None
            if not sdk_importable:
                message = "Install the official webull-openapi-python-sdk package."
            elif not self.credentials_configured:
                message = "Webull OpenAPI credentials are not configured."
            return CapabilityReport(**base, connected=False, message=message)

        try:
            accounts = self.list_accounts()
            if not accounts:
                return CapabilityReport(
                    **base,
                    connected=True,
                    message="Credentials are valid but expose no accounts.",
                )
            account_id = accounts[0].account_id
            self.get_balance(account_id)
            self.get_positions(account_id)
        except WebullAdapterError as exc:
            return CapabilityReport(**base, connected=False, message=str(exc))

        # Activity and order history are optional backfill capabilities. A missing
        # production entitlement or route must not disable current holdings.
        unavailable: list[str] = []
        now = utc_now()
        try:
            self.get_cash_activities(account_id, since=now, until=now)
        except WebullAdapterError:
            base["cash_activities"] = False
            unavailable.append("cash activities")
        try:
            self.get_order_history(account_id, since=now, until=now)
        except WebullAdapterError:
            base["order_history"] = False
            unavailable.append("order history")
        suffix = (
            f" Optional capability unavailable: {', '.join(unavailable)}."
            if unavailable
            else ""
        )
        return CapabilityReport(
            **base,
            connected=True,
            message=f"Core read-only account capabilities verified.{suffix}",
        )

    def list_accounts(self) -> list[BrokerageAccount]:
        payload = self._call(lambda client: client.account_v2.get_account_list())
        rows = _items(payload, "accounts", "data", "items", "list")
        return [
            BrokerageAccount(
                account_id=str(_first(row, "account_id", "accountId", "id")),
                account_type=str(
                    _first(
                        row, "account_type", "accountType", "type", default="UNKNOWN"
                    )
                ),
                status=str(_first(row, "status", "account_status", default="ACTIVE")),
                currency=str(_first(row, "currency", "base_currency", default="USD")),
            )
            for row in rows
            if _first(row, "account_id", "accountId", "id")
        ]

    def get_balance(self, account_id: str) -> BalanceSnapshot:
        payload = self._call(
            lambda client: client.account_v2.get_account_balance(account_id)
        )
        if isinstance(payload, list):
            row = payload[0] if payload else {}
        elif isinstance(payload, dict):
            row = (
                payload.get("balance")
                if isinstance(payload.get("balance"), dict)
                else payload
            )
        else:
            row = {}
        if not row:
            raise WebullAdapterError(
                "Webull returned no balance for the selected account."
            )

        currency_assets = _items(row, "account_currency_assets", "currency_assets")
        primary_currency = currency_assets[0] if currency_assets else {}
        currency = str(
            _first(
                row,
                "total_asset_currency",
                "currency",
                default=_first(primary_currency, "currency", default="USD"),
            )
        )
        equity = _decimal(
            _first(
                row,
                "total_net_liquidation_value",
                "net_liquidation_value",
                "total_asset_value",
                default=_first(primary_currency, "net_liquidation_value"),
            )
        )
        cash = _decimal(
            _first(
                row,
                "total_cash_balance",
                "cash_balance",
                default=_first(primary_currency, "cash_balance"),
            )
        )
        market_value = _decimal(
            _first(
                row,
                "total_market_value",
                "market_value",
                default=_first(primary_currency, "market_value"),
            )
        )
        return BalanceSnapshot(
            account_id=account_id,
            as_of=_datetime(
                _first(row, "as_of", "update_time", "timestamp"), utc_now()
            ),
            currency=currency,
            equity=equity,
            cash=cash,
            market_value=market_value,
            buying_power=_optional_decimal(
                _first(
                    row,
                    "buying_power",
                    default=_first(primary_currency, "buying_power"),
                )
            ),
            day_profit_loss=_optional_decimal(
                _first(
                    row,
                    "total_day_profit_loss",
                    "day_profit_loss",
                    default=_first(primary_currency, "day_profit_loss"),
                )
            ),
            unrealized_profit_loss=_optional_decimal(
                _first(
                    row,
                    "total_unrealized_profit_loss",
                    "unrealized_profit_loss",
                    default=_first(primary_currency, "unrealized_profit_loss"),
                )
            ),
        )

    def get_positions(self, account_id: str) -> list[PositionSnapshot]:
        payload = self._call(
            lambda client: client.account_v2.get_account_position(account_id)
        )
        rows = _items(payload, "positions", "data", "items", "list")
        positions: list[PositionSnapshot] = []
        for row in rows:
            symbol = str(_first(row, "symbol", "ticker", default="")).strip().upper()
            if not symbol:
                continue
            quantity = _decimal(_first(row, "quantity", "qty"))
            last_price = _decimal(_first(row, "last_price", "market_price", "price"))
            market_value = _decimal(
                _first(row, "market_value"), default=str(quantity * last_price)
            )
            average_cost = _optional_decimal(
                _first(row, "cost_price", "average_cost", "avg_price")
            )
            cost_basis = _optional_decimal(_first(row, "cost_basis"))
            if cost_basis is None and average_cost is not None:
                cost_basis = quantity * average_cost
            external_id = str(
                _first(row, "position_id", "positionId", "id", default="")
            )
            if not external_id:
                external_id = _stable_id(
                    "position",
                    row,
                    ("symbol", "instrument_type", "quantity", "cost_price"),
                )
            positions.append(
                PositionSnapshot(
                    account_id=account_id,
                    external_position_id=external_id,
                    symbol=symbol,
                    instrument_type=str(
                        _first(row, "instrument_type", "asset_type", default="EQUITY")
                    ),
                    currency=str(_first(row, "currency", default="USD")),
                    quantity=quantity,
                    last_price=last_price,
                    market_value=market_value,
                    average_cost=average_cost,
                    cost_basis=cost_basis,
                    unrealized_profit_loss=_optional_decimal(
                        _first(row, "unrealized_profit_loss", "unrealized_pl")
                    ),
                )
            )
        return positions

    def get_cash_activities(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[CashActivity]:
        client = self._client()
        activity_client = getattr(client, "activity", None)
        get_activities = getattr(activity_client, "get_activities", None)
        if not callable(get_activities):
            raise WebullAdapterError(
                "The installed official SDK does not expose cash activities."
            )

        start_time = (
            ensure_utc(since).isoformat().replace("+00:00", "Z") if since else None
        )
        end_time = (
            ensure_utc(until).isoformat().replace("+00:00", "Z") if until else None
        )
        cursor: str | None = None
        seen_cursors: set[str] = set()
        rows: list[dict[str, Any]] = []
        for _ in range(100):
            payload = self._response_json(
                get_activities(
                    account_id,
                    start_time=start_time,
                    end_time=end_time,
                    last_activity_id=cursor,
                    page_size=100,
                )
            )
            page = _items(payload, "activities", "data", "items", "list", "records")
            rows.extend(page)
            next_cursor = None
            if isinstance(payload, dict):
                next_cursor = _first(
                    payload, "next_activity_id", "last_activity_id", "next_cursor"
                )
            if not next_cursor and len(page) >= 100:
                next_cursor = _first(page[-1], "activity_id", "id")
            if not next_cursor or str(next_cursor) in seen_cursors or len(page) < 100:
                break
            cursor = str(next_cursor)
            seen_cursors.add(cursor)

        return [self._normalize_cash_activity(account_id, row) for row in rows]

    def get_order_history(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[OrderRecord]:
        client = self._client()
        order_client = getattr(client, "order_v2", None)
        get_history = getattr(order_client, "get_order_history", None)
        if not callable(get_history):
            raise WebullAdapterError(
                "The installed official SDK does not expose read-only order history."
            )

        start_date = ensure_utc(since).date().isoformat() if since else None
        end_date = ensure_utc(until).date().isoformat() if until else None
        last_client_order_id: str | None = None
        last_order_id: str | None = None
        seen_cursors: set[tuple[str | None, str | None]] = set()
        rows: list[dict[str, Any]] = []
        for _ in range(500):
            payload = self._response_json(
                get_history(
                    account_id,
                    page_size=100,
                    start_date=start_date,
                    end_date=end_date,
                    last_client_order_id=last_client_order_id,
                    last_order_id=last_order_id,
                )
            )
            page = _flatten_order_rows(payload)
            rows.extend(page)
            if len(page) < 100:
                break
            last = page[-1]
            next_cursor = (
                str(_first(last, "client_order_id", default="")) or None,
                str(_first(last, "order_id", default="")) or None,
            )
            if next_cursor in seen_cursors or next_cursor == (None, None):
                break
            seen_cursors.add(next_cursor)
            last_client_order_id, last_order_id = next_cursor

        records = [self._normalize_order(account_id, row) for row in rows]
        return [
            record
            for record in records
            if (
                since is None
                or (record.filled_at or record.placed_at) is None
                or (record.filled_at or record.placed_at) >= since
            )
            and (
                until is None
                or (record.filled_at or record.placed_at) is None
                or (record.filled_at or record.placed_at) <= until
            )
        ]

    def _normalize_order(self, account_id: str, row: dict[str, Any]) -> OrderRecord:
        external_id = str(_first(row, "order_id", "id", default=""))
        if not external_id:
            external_id = _stable_id(
                "order",
                row,
                ("client_order_id", "symbol", "side", "place_time", "total_quantity"),
            )
        commission_payload = row.get("commission")
        commission = None
        if isinstance(commission_payload, dict):
            commission = _optional_decimal(
                _first(commission_payload, "actual_commission", "receivable_commission")
            )
        fees_payload = row.get("fees")
        fee_values = (
            _items({"fees": fees_payload}, "fees")
            if isinstance(fees_payload, list)
            else []
        )
        fees = (
            sum(
                (
                    _decimal(_first(item, "actual_value", "receivable_value"))
                    for item in fee_values
                ),
                Decimal(0),
            )
            if fee_values
            else None
        )
        return OrderRecord(
            account_id=account_id,
            external_order_id=external_id,
            client_order_id=str(_first(row, "client_order_id"))
            if _first(row, "client_order_id") is not None
            else None,
            symbol=str(_first(row, "symbol", default="UNKNOWN")),
            instrument_type=str(_first(row, "instrument_type", default="UNKNOWN")),
            side=str(_first(row, "side", default="UNKNOWN")),
            status=str(_first(row, "status", default="UNKNOWN")),
            order_type=str(_first(row, "order_type"))
            if _first(row, "order_type") is not None
            else None,
            total_quantity=_decimal(_first(row, "total_quantity", "quantity")),
            filled_quantity=_decimal(_first(row, "filled_quantity")),
            average_fill_price=_optional_decimal(
                _first(row, "filled_price", "average_fill_price")
            ),
            placed_at=_optional_datetime(_first(row, "place_time_at", "place_time")),
            filled_at=_optional_datetime(_first(row, "filled_time_at", "filled_time")),
            commission=commission,
            fees=fees,
        )

    def _normalize_cash_activity(
        self, account_id: str, row: dict[str, Any]
    ) -> CashActivity:
        activity_type = str(
            _first(row, "activity_type", "type", "biz_type", default="OTHER")
        ).upper()
        raw_amount = _decimal(_first(row, "amount", "net_amount", "cash_amount"))
        if activity_type in _WITHDRAWAL_TYPES:
            amount = -abs(raw_amount)
        elif activity_type in _DEPOSIT_TYPES:
            amount = abs(raw_amount)
        else:
            amount = raw_amount
        external_id = str(
            _first(row, "activity_id", "id", "transaction_id", default="")
        )
        if not external_id:
            external_id = _stable_id(
                "activity",
                row,
                (
                    "activity_type",
                    "type",
                    "amount",
                    "currency",
                    "trade_time",
                    "occurred_at",
                    "symbol",
                ),
            )
        return CashActivity(
            account_id=account_id,
            external_activity_id=external_id,
            activity_type=activity_type,
            occurred_at=_datetime(
                _first(
                    row,
                    "occurred_at",
                    "activity_time",
                    "trade_time",
                    "transaction_time",
                    "update_time",
                    "timestamp",
                )
            ),
            amount=amount,
            currency=str(_first(row, "currency", default="USD")),
            status=str(_first(row, "status"))
            if _first(row, "status") is not None
            else None,
            description=str(_first(row, "description", "memo", "remark"))[:500]
            if _first(row, "description", "memo", "remark") is not None
            else None,
            is_external_flow=activity_type in _DEPOSIT_TYPES
            or activity_type in _WITHDRAWAL_TYPES,
        )

    def _call(self, operation: Callable[[Any], Any]) -> Any:
        return self._response_json(operation(self._client()))

    def _client(self) -> Any:
        if self._trade_client is not None:
            return self._trade_client
        if not self.credentials_configured:
            raise AdapterConfigurationError(
                "Webull OpenAPI credentials are not configured."
            )
        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
        except ImportError as exc:
            raise AdapterConfigurationError(
                "Install the official webull-openapi-python-sdk package."
            ) from exc

        api_client = ApiClient(self._app_key, self._app_secret, self._region)
        api_client.add_endpoint(self._region, self._endpoint)
        if self._token_dir:
            api_client.set_token_dir(self._token_dir)
        # Mark logging as configured before TradeClient construction so the SDK
        # cannot create its default local account log. Suppress SDK request/response
        # logging because those records can contain private account identifiers.
        try:
            api_client.set_stream_logger(
                stream=_DiscardLogStream(),
                log_level=logging.CRITICAL + 1,
            )
        except TypeError:
            api_client.set_stream_logger(stream=_DiscardLogStream())
        self._trade_client = TradeClient(api_client)
        return self._trade_client

    @staticmethod
    def _response_json(response: Any) -> Any:
        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            message = "Webull OpenAPI request failed."
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    public_message = payload.get("message") or payload.get("error")
                    if public_message:
                        message = f"Webull OpenAPI request failed: {str(public_message)[:300]}"
            except (AttributeError, TypeError, ValueError):
                # Some SDK response wrappers do not expose a decodable body.
                # Preserve the deliberately generic public error in that case.
                message = "Webull OpenAPI request failed."
            raise WebullAdapterError(message)
        try:
            return response.json()
        except (AttributeError, TypeError, ValueError) as exc:
            raise WebullAdapterError(
                "Webull returned an invalid JSON response."
            ) from exc


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return _decimal(value)


class _DiscardLogStream:
    def write(self, _: str) -> int:
        return 0

    def flush(self) -> None:
        return None


def _optional_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    return _datetime(value)


def _flatten_order_rows(payload: Any) -> list[dict[str, Any]]:
    candidates = _items(payload, "data", "items", "list", "records", "orders")
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        nested = candidate.get("orders")
        if isinstance(nested, list):
            rows.extend(item for item in nested if isinstance(item, dict))
        elif _first(candidate, "order_id", "client_order_id") is not None:
            rows.append(candidate)
    if (
        not rows
        and isinstance(payload, dict)
        and _first(payload, "order_id", "client_order_id") is not None
    ):
        rows.append(payload)
    return rows
