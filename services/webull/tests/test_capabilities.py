import importlib.util

import pytest

from webull_service.adapters import OfficialWebullAdapter, WebullAdapterError
from webull_service.models import BrokerageAccount
from webull_service.repository import MemoryRepository
from webull_service.sync import SyncService


class _Response:
    status_code = 200

    def __init__(self, payload) -> None:
        self.payload = payload

    def json(self):
        return self.payload


def test_optional_history_failure_does_not_disable_current_holdings(
    monkeypatch,
) -> None:
    adapter = OfficialWebullAdapter(app_key="configured", app_secret="configured")
    account = BrokerageAccount(
        account_id="account-1",
        account_type="CASH",
        status="ACTIVE",
        currency="USD",
    )
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(adapter, "list_accounts", lambda: [account])
    monkeypatch.setattr(adapter, "get_balance", lambda _: object())
    monkeypatch.setattr(adapter, "get_positions", lambda _: [])
    monkeypatch.setattr(adapter, "get_cash_activities", lambda *_, **__: [])

    def unavailable(*_, **__):
        raise WebullAdapterError("Not available")

    monkeypatch.setattr(adapter, "get_order_history", unavailable)
    report = adapter.probe(live=True)

    assert report.connected is True
    assert report.balances is True
    assert report.positions is True
    assert report.order_history is False
    assert "Optional capability unavailable" in (report.message or "")


def test_raw_sdk_account_exception_is_sanitized() -> None:
    class _Accounts:
        def get_account_list(self):
            raise RuntimeError("private-token-and-account-details")

    adapter = OfficialWebullAdapter(app_key="configured", app_secret="configured")
    adapter._trade_client = type("Client", (), {"account_v2": _Accounts()})()

    with pytest.raises(WebullAdapterError) as caught:
        adapter.list_accounts()

    assert str(caught.value) == "Webull account request failed."
    assert "private-token" not in str(caught.value)


def test_raw_order_sdk_exception_degrades_backfill_without_leaking_details() -> None:
    account = {
        "account_id": "account-1",
        "account_type": "CASH",
        "status": "ACTIVE",
        "currency": "USD",
    }

    class _Accounts:
        def get_account_list(self):
            return _Response({"accounts": [account]})

    class _Activity:
        def get_activities(self, *_args, **_kwargs):
            return _Response({"activities": []})

    class _Orders:
        def get_order_history(self, *_args, **_kwargs):
            raise RuntimeError("secret-order-request-and-credential-state")

    adapter = OfficialWebullAdapter(app_key="configured", app_secret="configured")
    adapter._trade_client = type(
        "Client",
        (),
        {
            "account_v2": _Accounts(),
            "activity": _Activity(),
            "order_v2": _Orders(),
        },
    )()
    repository = MemoryRepository()
    repository.connect_accounts(adapter.list_accounts())

    result = SyncService(adapter, repository).backfill("account-1", days=30)

    assert result.order_history_available is False
    assert result.order_records_seen == 0
    assert "Webull order history is unavailable." in result.message
    assert "secret-order" not in result.message
