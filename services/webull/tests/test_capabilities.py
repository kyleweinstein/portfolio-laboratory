import importlib.util

from webull_service.adapters import OfficialWebullAdapter, WebullAdapterError
from webull_service.models import BrokerageAccount


def test_optional_history_failure_does_not_disable_current_holdings(monkeypatch) -> None:
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
