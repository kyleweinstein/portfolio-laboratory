from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from ..models import (
    BalanceSnapshot,
    BrokerageAccount,
    BrokerProvider,
    CapabilityReport,
    CashActivity,
    CashSource,
    OrderRecord,
    PositionSnapshot,
)
from .base import BrokerAdapterError, ReadOnlyBrokerAdapter

_PLAID_BASE_URLS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}
_SUPPORTED_INVESTMENT_ACCOUNT_TYPES = {"investment", "brokerage"}
_EXTERNAL_FLOW_SUBTYPES = {
    "deposit": True,
    "withdrawal": True,
    "transfer": True,
}
_MARKET_TIME_ZONE = ZoneInfo("America/New_York")


class PlaidClient(Protocol):
    """Narrow Investments client. No payment or trading calls are represented."""

    def accounts_get(self, access_token: str) -> dict[str, Any]: ...
    def holdings_get(self, access_token: str) -> dict[str, Any]: ...
    def investment_transactions_get(
        self,
        access_token: str,
        *,
        start_date: date,
        end_date: date,
        offset: int,
        count: int,
    ) -> dict[str, Any]: ...
    def item_get(self, access_token: str) -> dict[str, Any]: ...
    def institution_get_by_id(self, institution_id: str) -> dict[str, Any]: ...
    def link_token_create(
        self,
        *,
        client_user_id: str,
        redirect_uri: str | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]: ...
    def exchange_public_token(self, public_token: str) -> dict[str, Any]: ...
    def webhook_verification_key_get(self, key_id: str) -> dict[str, Any]: ...


class PlaidHttpClient:
    """Small production Plaid HTTP client with sanitized failures and no logging."""

    def __init__(
        self,
        *,
        client_id: str,
        secret: str,
        environment: str,
        production_enabled: bool,
        timeout_seconds: int = 30,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        normalized_environment = environment.strip().lower()
        if normalized_environment not in _PLAID_BASE_URLS:
            raise ValueError("PLAID_ENV must be sandbox, development, or production.")
        if normalized_environment == "production" and not production_enabled:
            raise ValueError(
                "PLAID_M1_INTEGRATION_ENABLED must be true for production Plaid calls."
            )
        self._client_id = client_id
        self._secret = secret
        self._base_url = _PLAID_BASE_URLS[normalized_environment]
        self._timeout_seconds = max(1, timeout_seconds)
        self._opener = opener

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._secret)

    def accounts_get(self, access_token: str) -> dict[str, Any]:
        return self._post("/accounts/get", {"access_token": access_token})

    def holdings_get(self, access_token: str) -> dict[str, Any]:
        return self._post("/investments/holdings/get", {"access_token": access_token})

    def investment_transactions_get(
        self,
        access_token: str,
        *,
        start_date: date,
        end_date: date,
        offset: int,
        count: int,
    ) -> dict[str, Any]:
        return self._post(
            "/investments/transactions/get",
            {
                "access_token": access_token,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "options": {"offset": offset, "count": count},
            },
        )

    def item_get(self, access_token: str) -> dict[str, Any]:
        return self._post("/item/get", {"access_token": access_token})

    def institution_get_by_id(self, institution_id: str) -> dict[str, Any]:
        return self._post(
            "/institutions/get_by_id",
            {"institution_id": institution_id, "country_codes": ["US"]},
        )

    def link_token_create(
        self,
        *,
        client_user_id: str,
        redirect_uri: str | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_name": "Portfolio Lab",
            "language": "en",
            "country_codes": ["US"],
            "user": {"client_user_id": client_user_id},
        }
        if access_token:
            payload["access_token"] = access_token
        else:
            payload["products"] = ["investments"]
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri
        return self._post("/link/token/create", payload)

    def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        return self._post("/item/public_token/exchange", {"public_token": public_token})

    def webhook_verification_key_get(self, key_id: str) -> dict[str, Any]:
        return self._post("/webhook_verification_key/get", {"key_id": key_id})

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise BrokerAdapterError("Plaid server credentials are not configured.")
        body = {
            **payload,
            "client_id": self._client_id,
            "secret": self._secret,
        }
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "Plaid-Version": "2020-09-14"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            raise BrokerAdapterError("Plaid Investments request failed.") from None
        if not isinstance(parsed, dict):
            raise BrokerAdapterError("Plaid Investments returned an invalid response.")
        return parsed


def verify_plaid_webhook(
    raw_body: str,
    signed_jwt: str,
    client: PlaidClient,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify Plaid's ES256 JWT and exact whitespace-sensitive body digest."""
    parts = signed_jwt.split(".")
    if len(parts) != 3:
        raise BrokerAdapterError("Plaid webhook verification failed.")
    try:
        header = json.loads(_decode_base64url(parts[0]).decode("utf-8"))
        claims = json.loads(_decode_base64url(parts[1]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        raise BrokerAdapterError("Plaid webhook verification failed.") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise BrokerAdapterError("Plaid webhook verification failed.")
    key_id = header.get("kid")
    if header.get("alg") != "ES256" or not isinstance(key_id, str) or not key_id:
        raise BrokerAdapterError("Plaid webhook verification failed.")

    response = client.webhook_verification_key_get(key_id)
    key = response.get("key") if isinstance(response.get("key"), dict) else None
    if not key or not _valid_plaid_jwk(key, key_id, now=now):
        raise BrokerAdapterError("Plaid webhook verification failed.")
    try:
        x = int.from_bytes(_decode_base64url(str(key["x"])), "big")
        y = int.from_bytes(_decode_base64url(str(key["y"])), "big")
        public_key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        raw_signature = _decode_base64url(parts[2])
        if len(raw_signature) != 64:
            raise ValueError
        public_key.verify(
            encode_dss_signature(
                int.from_bytes(raw_signature[:32], "big"),
                int.from_bytes(raw_signature[32:], "big"),
            ),
            f"{parts[0]}.{parts[1]}".encode("ascii"),
            ec.ECDSA(hashes.SHA256()),
        )
    except (InvalidSignature, KeyError, TypeError, ValueError):
        raise BrokerAdapterError("Plaid webhook verification failed.") from None

    issued_at = claims.get("iat")
    expected_digest = claims.get("request_body_sha256")
    current = int(time.time()) if now is None else now
    if (
        not isinstance(issued_at, int)
        or issued_at > current + 30
        or current - issued_at > 300
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
    ):
        raise BrokerAdapterError("Plaid webhook verification failed.")
    actual_digest = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest.lower()):
        raise BrokerAdapterError("Plaid webhook verification failed.")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise BrokerAdapterError("Plaid webhook verification failed.") from None
    if not isinstance(payload, dict):
        raise BrokerAdapterError("Plaid webhook verification failed.")
    allowed = {
        "webhook_type": payload.get("webhook_type"),
        "webhook_code": payload.get("webhook_code"),
        "item_id": payload.get("item_id"),
        "environment": payload.get("environment"),
    }
    if not isinstance(allowed["webhook_type"], str) or not isinstance(
        allowed["webhook_code"], str
    ):
        raise BrokerAdapterError("Plaid webhook verification failed.")
    return allowed


def _valid_plaid_jwk(key: dict[str, Any], key_id: str, *, now: int | None) -> bool:
    current = int(time.time()) if now is None else now
    expired_at = key.get("expired_at")
    return bool(
        key.get("alg") == "ES256"
        and key.get("kty") == "EC"
        and key.get("crv") == "P-256"
        and key.get("use") == "sig"
        and key.get("kid") == key_id
        and isinstance(key.get("x"), str)
        and isinstance(key.get("y"), str)
        and (
            expired_at is None or (isinstance(expired_at, int) and expired_at > current)
        )
    )


def _decode_base64url(value: str) -> bytes:
    if not value or any(character.isspace() for character in value):
        raise ValueError("Invalid base64url value.")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class PlaidM1InvestmentsAdapter(ReadOnlyBrokerAdapter):
    """Read-only M1 adapter over Plaid Investments."""

    enforcement_mode = "application"
    provider = BrokerProvider.PLAID_M1

    def __init__(
        self,
        *,
        client: PlaidClient,
        access_token: str,
        expected_institution_id: str | None = None,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._expected_institution_id = expected_institution_id or None
        self._holdings_cache: tuple[float, dict[str, Any]] | None = None

    @property
    def configured(self) -> bool:
        return bool(self._access_token)

    def probe(self, *, live: bool = False) -> CapabilityReport:
        if not self.configured:
            return CapabilityReport(
                provider=self.provider,
                sdk_importable=True,
                credentials_configured=False,
                account_list=False,
                balances=False,
                positions=False,
                cash_activities=False,
                order_history=False,
                connected=False,
                message="Plaid M1 connection is not configured.",
            )
        connected = False
        message = "Plaid Investments is configured for read-only M1 access."
        if live:
            self._validate_item()
            connected = bool(self.list_accounts())
        return CapabilityReport(
            provider=self.provider,
            sdk_importable=True,
            credentials_configured=True,
            account_list=True,
            balances=True,
            positions=True,
            cash_activities=True,
            order_history=False,
            connected=connected,
            message=message,
        )

    def list_accounts(self) -> list[BrokerageAccount]:
        self._require_configured()
        self._validate_item()
        payload = self._client.accounts_get(self._access_token)
        accounts: list[BrokerageAccount] = []
        for row in _dict_items(payload.get("accounts")):
            account_type = str(row.get("type") or "").lower()
            subtype = str(row.get("subtype") or "").lower()
            if (
                account_type not in _SUPPORTED_INVESTMENT_ACCOUNT_TYPES
                and subtype
                not in {
                    "brokerage",
                    "401k",
                    "ira",
                    "roth",
                }
            ):
                continue
            account_id = str(row.get("account_id") or "").strip()
            if not account_id:
                continue
            balances = (
                row.get("balances") if isinstance(row.get("balances"), dict) else {}
            )
            currency = str(balances.get("iso_currency_code") or "USD")
            accounts.append(
                BrokerageAccount(
                    account_id=_qualified_account_id(account_id),
                    provider=self.provider,
                    account_type=(subtype or account_type or "INVESTMENT").upper(),
                    status="ACTIVE",
                    currency=currency,
                )
            )
        return accounts

    def get_balance(self, account_id: str) -> BalanceSnapshot:
        payload = self._holdings_payload()
        external_id = _external_account_id(account_id)
        account = next(
            (
                row
                for row in _dict_items(payload.get("accounts"))
                if str(row.get("account_id")) == external_id
            ),
            None,
        )
        if account is None:
            raise BrokerAdapterError(
                "The selected Plaid investment account is unavailable."
            )
        balances = (
            account.get("balances") if isinstance(account.get("balances"), dict) else {}
        )
        if balances.get("current") is None:
            raise BrokerAdapterError(
                "Plaid did not return current investment account value."
            )
        equity = _decimal(balances.get("current"), field="account value")
        currency = str(balances.get("iso_currency_code") or "USD").upper()
        securities = _security_map(payload)
        holdings = _holdings_for_account(payload, external_id)
        snapshot_as_of, common_as_of = _common_account_holdings_as_of(
            payload, account, holdings
        )
        non_cash_market_value = Decimal(0)
        explicit_cash = Decimal(0)
        has_explicit_cash = False
        currencies: set[str] = {currency}
        for holding in holdings:
            security = securities.get(str(holding.get("security_id") or ""), {})
            holding_currency = str(
                holding.get("iso_currency_code")
                or security.get("iso_currency_code")
                or currency
            ).upper()
            currencies.add(holding_currency)
            value = _decimal(
                holding.get("institution_value"), field="holding market value"
            )
            if str(security.get("type") or "").lower() == "cash":
                has_explicit_cash = True
                explicit_cash += value
            else:
                non_cash_market_value += value
        if has_explicit_cash:
            cash = explicit_cash
            cash_source = CashSource.EXPLICIT
        elif currencies == {currency} and common_as_of:
            cash = equity - non_cash_market_value
            cash_source = CashSource.DERIVED_RESIDUAL
        else:
            cash = Decimal(0)
            cash_source = CashSource.UNAVAILABLE
        return BalanceSnapshot(
            account_id=account_id,
            as_of=snapshot_as_of,
            currency=currency,
            equity=equity,
            cash=cash,
            market_value=non_cash_market_value,
            cash_source=cash_source,
        )

    def get_positions(self, account_id: str) -> list[PositionSnapshot]:
        payload = self._holdings_payload()
        external_id = _external_account_id(account_id)
        securities = _security_map(payload)
        positions: list[PositionSnapshot] = []
        for holding in _holdings_for_account(payload, external_id):
            security_id = str(holding.get("security_id") or "")
            security = securities.get(security_id, {})
            if str(security.get("type") or "").lower() == "cash":
                continue
            quantity = _decimal(holding.get("quantity"), field="holding quantity")
            total_cost = _optional_decimal(
                holding.get("cost_basis"), field="holding cost basis"
            )
            average_cost = (
                total_cost / quantity if total_cost is not None and quantity else None
            )
            institution_value = _decimal(
                holding.get("institution_value"), field="holding market value"
            )
            symbol = str(
                security.get("ticker_symbol") or security.get("name") or security_id
            )[:32]
            positions.append(
                PositionSnapshot(
                    account_id=account_id,
                    external_position_id=security_id or f"holding-{len(positions)}",
                    symbol=symbol,
                    instrument_type=_instrument_type(security),
                    currency=str(
                        holding.get("iso_currency_code")
                        or security.get("iso_currency_code")
                        or "USD"
                    ),
                    quantity=quantity,
                    last_price=_decimal(
                        holding.get("institution_price"), field="holding price"
                    ),
                    market_value=institution_value,
                    average_cost=average_cost,
                    cost_basis=total_cost,
                    unrealized_profit_loss=(
                        institution_value - total_cost
                        if total_cost is not None
                        else None
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
        self._require_configured()
        start = (since or datetime(1970, 1, 1, tzinfo=UTC)).date()
        end = (until or datetime.now(UTC)).date()
        external_id = _external_account_id(account_id)
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._client.investment_transactions_get(
                self._access_token,
                start_date=start,
                end_date=end,
                offset=offset,
                count=500,
            )
            raw_page = _dict_items(payload.get("investment_transactions"))
            page = [
                row for row in raw_page if str(row.get("account_id")) == external_id
            ]
            rows.extend(page)
            offset += len(raw_page)
            total = int(payload.get("total_investment_transactions") or offset)
            if offset >= total or not raw_page:
                break
        activities: list[CashActivity] = []
        for row in rows:
            subtype = str(row.get("subtype") or "unknown").lower()
            external_activity_id = str(
                row.get("investment_transaction_id") or ""
            ).strip()
            if not external_activity_id:
                continue
            try:
                transaction_date = date.fromisoformat(str(row.get("date")))
            except ValueError:
                continue
            occurred_at = datetime.combine(transaction_date, datetime.min.time(), UTC)
            amount = -_decimal(row.get("amount"), field="transaction amount")
            activities.append(
                CashActivity(
                    account_id=account_id,
                    external_activity_id=external_activity_id,
                    activity_type=subtype,
                    occurred_at=occurred_at,
                    amount=amount,
                    currency=str(row.get("iso_currency_code") or "USD"),
                    status="POSTED",
                    description=str(row.get("name") or subtype)[:500],
                    is_external_flow=subtype in _EXTERNAL_FLOW_SUBTYPES,
                )
            )
        return activities

    def get_order_history(
        self,
        account_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[OrderRecord]:
        del account_id, since, until
        return []

    def _require_configured(self) -> None:
        if not self.configured:
            raise BrokerAdapterError("Plaid M1 connection is not configured.")

    def _validate_item(self) -> None:
        self._require_configured()
        if not self._expected_institution_id:
            return
        payload = self._client.item_get(self._access_token)
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        if str(item.get("institution_id") or "") != self._expected_institution_id:
            raise BrokerAdapterError(
                "The Plaid Item does not match the approved M1 institution."
            )

    def _holdings_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._holdings_cache and now - self._holdings_cache[0] <= 5:
            return self._holdings_cache[1]
        payload = self._client.holdings_get(self._access_token)
        self._holdings_cache = (now, payload)
        return payload


def _qualified_account_id(account_id: str) -> str:
    return f"plaid_m1:{account_id}"


def _external_account_id(account_id: str) -> str:
    prefix = "plaid_m1:"
    if not account_id.startswith(prefix):
        raise BrokerAdapterError("The selected account is not a Plaid M1 account.")
    return account_id[len(prefix) :]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _security_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("security_id")): item
        for item in _dict_items(payload.get("securities"))
        if item.get("security_id")
    }


def _holdings_for_account(
    payload: dict[str, Any], account_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in _dict_items(payload.get("holdings"))
        if str(item.get("account_id")) == account_id
    ]


def _decimal(value: Any, *, field: str) -> Decimal:
    if value is None:
        raise BrokerAdapterError(f"Plaid did not return a valid {field}.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BrokerAdapterError(f"Plaid did not return a valid {field}.") from None
    if not parsed.is_finite():
        raise BrokerAdapterError(f"Plaid did not return a valid {field}.")
    return parsed


def _optional_decimal(value: Any, *, field: str) -> Decimal | None:
    return None if value is None else _decimal(value, field=field)


def _common_account_holdings_as_of(
    payload: dict[str, Any],
    account: dict[str, Any],
    holdings: list[dict[str, Any]],
) -> tuple[datetime, bool]:
    """Return a conservative snapshot timestamp and residual-cash eligibility.

    A Plaid residual is safe to call cash only when the account value and every
    holding explicitly identify the same source date. Merely arriving in one
    HTTP response does not prove that the independently refreshed values share
    an as-of, so missing or conflicting source timestamps disable derivation.
    """

    balances = (
        account.get("balances") if isinstance(account.get("balances"), dict) else {}
    )
    account_as_of = _first_as_of(
        balances.get("as_of"),
        balances.get("last_updated_datetime"),
        account.get("as_of"),
        account.get("last_updated_datetime"),
        payload.get("as_of"),
        payload.get("last_updated_datetime"),
    )
    holding_as_ofs = [
        _first_as_of(
            holding.get("institution_value_as_of"),
            holding.get("institution_price_as_of"),
            holding.get("as_of"),
            holding.get("last_updated_datetime"),
        )
        for holding in holdings
    ]
    known_as_ofs = [item for item in [account_as_of, *holding_as_ofs] if item]
    unknown_as_of = datetime(1970, 1, 1, tzinfo=UTC)
    if account_as_of is None or any(item is None for item in holding_as_ofs):
        return unknown_as_of, False
    dates = {account_as_of[0], *(item[0] for item in holding_as_ofs if item)}
    if len(dates) != 1:
        return unknown_as_of, False
    return min(item[1] for item in known_as_ofs), True


def _first_as_of(*values: Any) -> tuple[date, datetime] | None:
    for value in values:
        parsed = _parse_as_of(value)
        if parsed is not None:
            return parsed
    return None


def _parse_as_of(value: Any) -> tuple[date, datetime] | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        normalized = value.astimezone(UTC)
        return normalized.astimezone(_MARKET_TIME_ZONE).date(), normalized
    if isinstance(value, date):
        return value, _market_close_as_utc(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            parsed_date = date.fromisoformat(text)
            return parsed_date, _market_close_as_utc(parsed_date)
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_datetime.tzinfo is None:
        return None
    normalized = parsed_datetime.astimezone(UTC)
    return normalized.astimezone(_MARKET_TIME_ZONE).date(), normalized


def _market_close_as_utc(value: date) -> datetime:
    return datetime(
        value.year, value.month, value.day, 16, tzinfo=_MARKET_TIME_ZONE
    ).astimezone(UTC)


def _instrument_type(security: dict[str, Any]) -> str:
    security_type = str(security.get("type") or "equity").lower()
    if security_type in {"equity", "etf"}:
        return "ETF" if security_type == "etf" else "EQUITY"
    return security_type.upper()[:64]
