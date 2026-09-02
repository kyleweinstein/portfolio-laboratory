import inspect
from datetime import UTC, datetime, timedelta

import pytest

from webull_service.adapters import (
    FakeWebullAdapter,
    OfficialWebullAdapter,
    ReadOnlyWebullAdapter,
    WebullAdapterError,
    application_read_only_gate_enabled,
)
from webull_service.models import BrokerageAccount
from webull_service.repository import MemoryRepository
from webull_service.sync import (
    CASH_ACTIVITY_BACKFILL_WARNING,
    CASH_ACTIVITY_WARNING,
    POSITIONS_FAILURE,
    SyncService,
)


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
    assert result.cash_activities_complete is True
    assert result.warning is None
    assert len(repository.snapshots[adapter.account.account_id]) == 2
    assert len(repository.activities) == 2


def test_position_failure_never_commits_a_balance_only_snapshot() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository)
    service.connect(sync_selected=False)
    adapter.fail_positions = True

    with pytest.raises(WebullAdapterError, match=POSITIONS_FAILURE):
        service.sync_selected()

    assert repository.snapshots.get(adapter.account.account_id, []) == []
    assert len(repository.failures) == 1
    assert repository.failures[0][1] == POSITIONS_FAILURE
    assert "Fake position" not in repository.failures[0][1]
    latest = repository.get_latest_sync_attempt(adapter.account.account_id)
    assert latest is not None
    assert latest.status.value == "error"
    assert latest.cash_activities_complete is None
    assert latest.message == POSITIONS_FAILURE


def test_cash_activity_failure_commits_core_snapshot_with_safe_warning() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository)
    service.connect(sync_selected=False)
    adapter.fail_cash_activities = True

    result = service.sync_selected()

    assert result.positions_written == 2
    assert result.cash_activities_seen == 0
    assert result.cash_activities_upserted == 0
    assert result.cash_activities_complete is False
    assert result.warning == CASH_ACTIVITY_WARNING
    assert len(repository.snapshots[adapter.account.account_id]) == 1
    latest = repository.get_latest_sync_attempt(adapter.account.account_id)
    assert latest is not None
    assert latest.status.value == "success"
    assert latest.cash_activities_complete is False
    assert latest.message == CASH_ACTIVITY_WARNING
    assert (
        repository.get_latest_cash_activity_coverage(adapter.account.account_id)
        is False
    )


def test_cash_activity_coverage_is_bounded_to_the_fetched_interval() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository, cash_activity_lookback_days=45)
    service.connect(sync_selected=False)

    service.sync_selected()

    assert repository.cash_activity_coverage_spans(
        adapter.account.account_id,
        start=adapter.as_of - timedelta(days=45),
        end=adapter.as_of,
    )
    assert not repository.cash_activity_coverage_spans(
        adapter.account.account_id,
        start=adapter.as_of - timedelta(days=46),
        end=adapter.as_of,
    )


def test_sync_all_reports_a_hard_failure_after_other_accounts_commit() -> None:
    class PartiallyFailingAdapter(FakeWebullAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.second_account = BrokerageAccount(
                account_id="fake-account-002",
                account_type="CASH",
                status="ACTIVE",
                currency="USD",
            )

        def list_accounts(self):
            self.calls.append(("list_accounts", None))
            return [self.account, self.second_account]

        def _require_account(self, account_id: str) -> None:
            if account_id not in {
                self.account.account_id,
                self.second_account.account_id,
            }:
                raise WebullAdapterError("Unknown fake account")

        def get_positions(self, account_id: str):
            if account_id == self.second_account.account_id:
                raise WebullAdapterError("private-position-detail")
            return super().get_positions(account_id)

    adapter = PartiallyFailingAdapter()
    repository = MemoryRepository()

    with pytest.raises(
        WebullAdapterError, match="One or more Webull account snapshots failed"
    ) as caught:
        SyncService(adapter, repository).sync_all()

    assert adapter.account.account_id not in str(caught.value)
    assert adapter.second_account.account_id not in str(caught.value)
    assert len(repository.snapshots[adapter.account.account_id]) == 1
    assert repository.snapshots.get(adapter.second_account.account_id, []) == []
    failed_attempt = repository.get_latest_sync_attempt(
        adapter.second_account.account_id
    )
    assert failed_attempt is not None
    assert failed_attempt.status.value == "error"
    assert failed_attempt.message == POSITIONS_FAILURE


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


def test_backfill_keeps_orders_when_cash_activities_are_unavailable() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository)
    service.connect(sync_selected=False)
    adapter.fail_cash_activities = True

    result = service.backfill(adapter.account.account_id, days=365)

    assert result.cash_activities_available is False
    assert result.cash_activities_seen == 0
    assert result.order_history_available is True
    assert result.order_records_seen == 1
    assert len(repository.orders) == 1
    assert CASH_ACTIVITY_BACKFILL_WARNING in result.message
    assert (
        repository.get_latest_cash_activity_coverage(adapter.account.account_id)
        is False
    )


def test_short_routine_sync_cannot_clear_a_longer_backfill_coverage_gap() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    service = SyncService(adapter, repository)
    service.connect(sync_selected=False)
    adapter.fail_cash_activities = True
    failed_backfill = service.backfill(adapter.account.account_id, days=365)
    assert failed_backfill.cash_activities_available is False

    adapter.fail_cash_activities = False
    routine = service.sync_selected()

    assert routine.cash_activities_complete is False
    assert routine.warning == (
        "Cash activity coverage remains incomplete; performance remains unavailable."
    )
    assert (
        repository.get_latest_cash_activity_coverage(adapter.account.account_id)
        is False
    )

    restored = service.backfill(adapter.account.account_id, days=366)
    assert restored.cash_activities_available is True
    assert (
        repository.get_latest_cash_activity_coverage(adapter.account.account_id) is True
    )
    latest = repository.get_latest_sync_attempt(adapter.account.account_id)
    assert latest is not None
    assert latest.cash_activities_complete is True


def test_connection_state_uses_selected_accounts_own_last_sync_time() -> None:
    class TwoAccountAdapter(FakeWebullAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.second_account = BrokerageAccount(
                account_id="fake-account-002",
                account_type="CASH",
                status="ACTIVE",
                currency="USD",
            )

        def list_accounts(self):
            self.calls.append(("list_accounts", None))
            return [self.account, self.second_account]

        def _require_account(self, account_id: str) -> None:
            if account_id not in {
                self.account.account_id,
                self.second_account.account_id,
            }:
                raise WebullAdapterError("Unknown fake account")

        def get_balance(self, account_id: str):
            balance = super().get_balance(account_id)
            if account_id == self.second_account.account_id:
                return balance.model_copy(
                    update={"as_of": balance.as_of + timedelta(hours=2)}
                )
            return balance

    adapter = TwoAccountAdapter()
    repository = MemoryRepository()

    before_sync = datetime.now(UTC)
    SyncService(adapter, repository).sync_all()
    after_sync = datetime.now(UTC)

    first_state = repository.get_connection_state()
    assert first_state.selected_account_id == adapter.account.account_id
    assert first_state.last_synced_at is not None
    assert before_sync <= first_state.last_synced_at <= after_sync
    second_state = repository.select_account(adapter.second_account.account_id)
    assert second_state.last_synced_at is not None
    assert before_sync <= second_state.last_synced_at <= after_sync
