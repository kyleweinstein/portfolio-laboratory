import inspect

import pytest

from webull_service.adapters import (
    FakeWebullAdapter,
    OfficialWebullAdapter,
    ReadOnlyWebullAdapter,
    WebullAdapterError,
    application_read_only_gate_enabled,
)
from webull_service.repository import MemoryRepository
from webull_service.sync import SyncService


def test_adapter_contract_has_no_trading_methods() -> None:
    assert ReadOnlyWebullAdapter.enforcement_mode == "unverified"
    assert OfficialWebullAdapter.__dict__["enforcement_mode"] == "application"
    assert FakeWebullAdapter.__dict__["enforcement_mode"] == "application"
    methods = {
        name
        for name, _ in inspect.getmembers(ReadOnlyWebullAdapter, inspect.isfunction)
    }
    assert methods == {
        "get_balance",
        "get_cash_activities",
        "get_order_history",
        "get_positions",
        "list_accounts",
        "probe",
    }
    official_methods = {
        name
        for name, _ in inspect.getmembers(OfficialWebullAdapter, inspect.isfunction)
    }
    forbidden_tokens = {
        "place",
        "replace",
        "cancel",
        "preview",
        "transfer",
        "withdraw",
        "deposit",
        "fund",
        "trade",
    }
    assert not any(
        token in method_name
        for method_name in official_methods
        for token in forbidden_tokens
    )


def test_application_gate_requires_concrete_adapter_opt_in() -> None:
    class InheritedOnlyAdapter(FakeWebullAdapter):
        pass

    assert application_read_only_gate_enabled(
        configured=True, adapter=FakeWebullAdapter()
    )
    assert not application_read_only_gate_enabled(
        configured=False, adapter=FakeWebullAdapter()
    )
    assert not application_read_only_gate_enabled(
        configured=True, adapter=InheritedOnlyAdapter()
    )


def test_sync_commits_balance_positions_and_dedupes_cash_atomically() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository)

    state = service.connect(sync_selected=True)
    assert state.connected is True
    assert len(repository.snapshots[adapter.account.account_id]) == 1
    assert len(repository.activities) == 2

    adapter.advance(hours=1)
    result = service.sync_selected()
    assert result.positions_written == 2
    assert result.cash_activities_seen == 2
    assert result.cash_activities_upserted == 2
    assert len(repository.snapshots[adapter.account.account_id]) == 2
    assert len(repository.activities) == 2


def test_position_failure_never_commits_a_balance_only_snapshot() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository)
    service.connect(sync_selected=False)
    adapter.fail_positions = True

    with pytest.raises(WebullAdapterError, match="position endpoint failure"):
        service.sync_selected()

    assert repository.snapshots.get(adapter.account.account_id, []) == []
    assert len(repository.failures) == 1


def test_backfill_dedupes_cash_and_read_only_order_history() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository)
    service.connect(sync_selected=False)

    first = service.backfill(adapter.account.account_id, days=365)
    second = service.backfill(adapter.account.account_id, days=365)

    assert first.order_history_available is True
    assert first.order_records_seen == 1
    assert len(repository.orders) == 1
    assert len(repository.activities) == 2
    assert second.order_records_upserted == 1
