from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from webull_service.adapters import (
    BrokerAdapterError,
    ReadOnlySchwabAdapter,
    map_schwab_account,
    map_schwab_balance,
    map_schwab_positions,
    map_schwab_transactions,
)
from webull_service.models import BrokerOAuthCredential, BrokerProvider
from webull_service.repository import MemoryRepository, RepositoryConfigurationError

ACCOUNT = {
    "hashValue": "opaque-account-hash",
    "securitiesAccount": {
        "type": "MARGIN",
        "currency": "USD",
        "currentBalances": {
            "liquidationValue": "100000",
            "cashBalance": "5000",
            "marginBalance": "30000",
            "longMarketValue": "125000",
            "shortMarketValue": "0",
            "buyingPower": "25000",
        },
        "positions": [
            {
                "longQuantity": "10",
                "shortQuantity": "0",
                "marketValue": "1500",
                "averagePrice": "120",
                "currentPrice": "150",
                "instrument": {
                    "symbol": "AAPL",
                    "cusip": "037833100",
                    "assetType": "EQUITY",
                },
            },
            {
                "longQuantity": "0",
                "shortQuantity": "3",
                "marketValue": "270",
                "averagePrice": "100",
                "instrument": {
                    "symbol": "SHORT",
                    "cusip": "SHORTCUSIP",
                    "assetType": "EQUITY",
                },
            },
        ],
    },
}


def test_schwab_migration_adds_encrypted_refresh_and_expiry_fields() -> None:
    root = Path(__file__).parents[1]
    sql = (root / "migrations" / "0006_schwab_readiness.sql").read_text(
        encoding="utf-8"
    )
    assert "encrypted_refresh_token BYTEA" in sql
    assert "access_token_expires_at TIMESTAMPTZ" in sql
    assert "refresh_token_expires_at TIMESTAMPTZ" in sql
    assert "REVOKE ALL ON broker_connection_credentials FROM PUBLIC" in sql
    assert not (root / "migrations" / "0005_schwab_readiness.sql").exists()


def test_provider_oauth_bundle_masks_secrets_and_memory_repository_keeps_expiry() -> (
    None
):
    credential = BrokerOAuthCredential(
        provider=BrokerProvider.SCHWAB,
        access_token="private-access-token",
        refresh_token="private-refresh-token",
        access_token_expires_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        refresh_token_expires_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
    )
    assert "private-access-token" not in repr(credential)
    assert "private-refresh-token" not in credential.model_dump_json()
    assert credential.access_token_expires_at.tzinfo is UTC

    repository = MemoryRepository()
    with pytest.raises(RepositoryConfigurationError):
        repository.save_provider_oauth_credential(
            "schwab",
            connection_key="opaque-connection",
            institution_id=None,
            credential=credential,
            encryption_key="",
        )
    repository.save_provider_oauth_credential(
        "schwab",
        connection_key="opaque-connection",
        institution_id=None,
        credential=credential,
        encryption_key="test-encryption-key",
    )
    restored = repository.get_provider_oauth_credential(
        "schwab", encryption_key="test-encryption-key"
    )
    assert restored is credential
    assert repository.has_provider_connection("schwab") is True
    assert restored.refresh_token is not None
    assert restored.refresh_token.get_secret_value() == "private-refresh-token"


def test_schwab_balance_maps_borrowed_margin_to_negative_signed_cash() -> None:
    account = map_schwab_account(ACCOUNT)
    balance = map_schwab_balance(
        account.account_id,
        ACCOUNT,
        captured_at=datetime(2026, 8, 28, 20, tzinfo=UTC),
    )
    assert account.provider is BrokerProvider.SCHWAB
    assert account.account_id == "schwab:opaque-account-hash"
    assert balance.equity == Decimal(100000)
    assert balance.cash == Decimal(-25000)
    assert balance.market_value == Decimal(125000)
    assert balance.equity == balance.cash + balance.market_value


def test_schwab_positions_preserve_average_cost_and_signed_shorts() -> None:
    positions = map_schwab_positions("schwab:opaque-account-hash", ACCOUNT)
    assert len(positions) == 2
    assert positions[0].symbol == "AAPL"
    assert positions[0].quantity == Decimal(10)
    assert positions[0].average_cost == Decimal(120)
    assert positions[0].cost_basis == Decimal(1200)
    assert positions[1].quantity == Decimal(-3)
    assert positions[1].market_value == Decimal(-270)
    assert positions[1].last_price == Decimal(90)


def test_schwab_transactions_mark_only_explicit_cash_and_security_transfers_external() -> (
    None
):
    activities = map_schwab_transactions(
        "schwab:opaque-account-hash",
        [
            {
                "activityId": "ach-1",
                "time": "2026-08-28T14:00:00Z",
                "type": "ACH_RECEIPT",
                "netAmount": "1000",
                "description": "External deposit",
            },
            {
                "activityId": "dividend-1",
                "time": "2026-08-28T15:00:00Z",
                "type": "DIVIDEND_OR_INTEREST",
                "netAmount": "5.25",
                "description": "Dividend",
            },
            {
                "activityId": "transfer-1",
                "time": "2026-08-28T16:00:00Z",
                "type": "RECEIVE_AND_DELIVER",
                "netAmount": "0",
            },
        ],
    )
    assert [item.is_external_flow for item in activities] == [True, False, True]
    assert activities[0].amount == Decimal(1000)
    assert activities[1].occurred_at.tzinfo is UTC


def test_disabled_adapter_fails_closed_and_every_injected_read_is_throttled() -> None:
    class Throttle:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def acquire(self, operation: str) -> None:
            self.calls.append(operation)

    class Client:
        def get_accounts(self):
            return [ACCOUNT]

        def get_account(self, account_hash: str):
            assert account_hash == "opaque-account-hash"
            return ACCOUNT

        def get_transactions(self, account_hash: str, *, since, until):
            assert account_hash == "opaque-account-hash"
            del since, until
            return []

    throttle = Throttle()
    disabled = ReadOnlySchwabAdapter(client=Client(), throttle=throttle)
    with pytest.raises(BrokerAdapterError, match="live access is disabled"):
        disabled.list_accounts()
    assert throttle.calls == []

    adapter = ReadOnlySchwabAdapter(client=Client(), throttle=throttle, enabled=True)
    account_id = adapter.list_accounts()[0].account_id
    adapter.get_balance(account_id)
    adapter.get_positions(account_id)
    adapter.get_cash_activities(account_id)
    assert throttle.calls == ["accounts", "balance", "positions", "transactions"]


def test_readiness_surface_has_no_trading_or_live_oauth_route() -> None:
    method_names = set(vars(ReadOnlySchwabAdapter))
    forbidden_methods = {
        "place_order",
        "replace_order",
        "cancel_order",
        "preview_order",
        "transfer_money",
        "withdraw",
        "get_order_history",
    }
    assert method_names.isdisjoint(forbidden_methods)
    protocol_source = inspect.getsource(
        __import__(
            "webull_service.adapters.schwab", fromlist=["SchwabReadClient"]
        ).SchwabReadClient
    )
    assert all(name not in protocol_source for name in forbidden_methods)

    root = Path(__file__).parents[1]
    main_source = (root / "webull_service" / "main.py").read_text(encoding="utf-8")
    env_source = (root / ".env.example").read_text(encoding="utf-8")
    assert '"/providers/schwab/oauth' not in main_source
    assert '"/providers/schwab/connect' not in main_source
    assert "SCHWAB_INTEGRATION_ENABLED=false" in env_source
