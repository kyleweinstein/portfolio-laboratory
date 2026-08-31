from __future__ import annotations

import logging
from datetime import timedelta

from .adapters.base import (
    BrokerAdapterError,
    ReadOnlyBrokerAdapter,
    WebullAdapterError,
)
from .models import (
    BackfillResult,
    BrokerageAccount,
    ConnectionState,
    SyncResult,
    utc_now,
)
from .repository import PortfolioRepository, RepositoryError

logger = logging.getLogger(__name__)

ACCOUNT_LIST_FAILURE = "Webull account list request failed."
ACCOUNT_UNAVAILABLE_FAILURE = "The selected Webull account is unavailable."
BALANCE_FAILURE = "Webull balance request failed."
POSITIONS_FAILURE = "Webull positions request failed."
CASH_ACTIVITY_FAILURE = "Webull cash activity request failed."
CASH_ACTIVITY_WARNING = (
    "Current holdings were refreshed, but cash activity coverage is incomplete; "
    "performance remains unavailable."
)
CASH_ACTIVITY_BACKFILL_WARNING = (
    "Cash activity history was unavailable; performance coverage remains incomplete."
)
ORDER_HISTORY_WARNING = "Read-only order history was unavailable."
PERSISTENCE_FAILURE = "Portfolio snapshot persistence failed."


class SyncService:
    def __init__(
        self,
        adapter: ReadOnlyBrokerAdapter,
        repository: PortfolioRepository,
        *,
        cash_activity_lookback_days: int = 45,
    ) -> None:
        self.adapter = adapter
        self.repository = repository
        self.cash_activity_lookback_days = max(1, cash_activity_lookback_days)

    def connect(self, *, sync_selected: bool = True) -> ConnectionState:
        accounts = self.adapter.list_accounts()
        state = self.repository.connect_accounts(accounts)
        if sync_selected and state.selected_account_id:
            self.sync_account(state.selected_account_id, accounts=accounts)
            state = self.repository.get_connection_state()
        return state

    def sync_account(
        self,
        account_id: str,
        *,
        accounts: list[BrokerageAccount] | None = None,
    ) -> SyncResult:
        started_at = utc_now()
        failure_message = ACCOUNT_LIST_FAILURE
        try:
            available = (
                accounts if accounts is not None else self.adapter.list_accounts()
            )
            failure_message = ACCOUNT_UNAVAILABLE_FAILURE
            account = next(
                (item for item in available if item.account_id == account_id), None
            )
            if account is None:
                raise RepositoryError(
                    "The selected account is no longer exposed by Webull."
                )
            # Fetch the complete upstream snapshot before opening the database
            # transaction. A failed position call can therefore never leave a new
            # balance without its corresponding positions.
            failure_message = BALANCE_FAILURE
            balance = self.adapter.get_balance(account_id)
            failure_message = POSITIONS_FAILURE
            positions = self.adapter.get_positions(account_id)
            failure_message = CASH_ACTIVITY_FAILURE
            cash_activities_complete = True
            warning = None
            cash_activity_from = balance.as_of - timedelta(
                days=self.cash_activity_lookback_days
            )
            cash_activity_until = balance.as_of
            try:
                activities = self.adapter.get_cash_activities(
                    account_id,
                    since=cash_activity_from,
                    until=cash_activity_until,
                )
            except BrokerAdapterError:
                activities = []
                cash_activities_complete = False
                warning = CASH_ACTIVITY_WARNING
            failure_message = PERSISTENCE_FAILURE
            return self.repository.commit_sync(
                account,
                balance,
                positions,
                activities,
                started_at=started_at,
                cash_activities_complete=cash_activities_complete,
                cash_activity_from=cash_activity_from,
                cash_activity_until=cash_activity_until,
                warning=warning,
            )
        except Exception as exc:
            try:
                self.repository.record_sync_failure(
                    account_id, started_at, failure_message
                )
            except Exception:  # noqa: BLE001 - persistence failure must not mask root failure.
                logger.error("Unable to persist the Webull sync failure.")
            if isinstance(exc, BrokerAdapterError):
                if isinstance(exc, WebullAdapterError):
                    raise WebullAdapterError(failure_message) from None
                raise BrokerAdapterError(
                    f"{self.adapter.provider.value} read-only synchronization failed."
                ) from None
            raise

    def sync_selected(self) -> SyncResult:
        state = self.repository.get_connection_state()
        if not state.connected or not state.selected_account_id:
            raise RepositoryError(
                "Connect Webull and select an account before syncing."
            )
        return self.sync_account(state.selected_account_id)

    def sync_all(self) -> list[SyncResult]:
        try:
            accounts = self.adapter.list_accounts()
        except BrokerAdapterError:
            if self.adapter.provider.value == "webull":
                raise WebullAdapterError(ACCOUNT_LIST_FAILURE) from None
            raise BrokerAdapterError(
                f"{self.adapter.provider.value} account list request failed."
            ) from None
        self.repository.connect_accounts(accounts)
        results: list[SyncResult] = []
        error_count = 0
        for account in accounts:
            try:
                results.append(self.sync_account(account.account_id, accounts=accounts))
            except Exception:  # noqa: BLE001 - one account must not stop the remaining syncs.
                error_count += 1
        if error_count:
            if self.adapter.provider.value == "webull":
                raise WebullAdapterError("One or more Webull account snapshots failed.")
            raise BrokerAdapterError("One or more brokerage account snapshots failed.")
        return results

    def backfill(self, account_id: str, *, days: int) -> BackfillResult:
        started_at = utc_now()
        requested_days = max(1, min(days, 3650))
        until = started_at
        since = until - timedelta(days=requested_days)
        try:
            accounts = self.adapter.list_accounts()
        except BrokerAdapterError:
            if self.adapter.provider.value == "webull":
                raise WebullAdapterError(ACCOUNT_LIST_FAILURE) from None
            raise BrokerAdapterError(
                f"{self.adapter.provider.value} account list request failed."
            ) from None
        account = next(
            (item for item in accounts if item.account_id == account_id), None
        )
        if account is None:
            raise RepositoryError(
                "The selected account is no longer exposed by Webull."
            )

        cash_activities_available = True
        warnings: list[str] = []
        try:
            activities = self.adapter.get_cash_activities(
                account_id, since=since, until=until
            )
        except BrokerAdapterError:
            activities = []
            cash_activities_available = False
            warnings.append(CASH_ACTIVITY_BACKFILL_WARNING)
        order_history_available = True
        try:
            orders = self.adapter.get_order_history(
                account_id, since=since, until=until
            )
        except BrokerAdapterError:
            orders = []
            order_history_available = False
            warnings.append(ORDER_HISTORY_WARNING)
        warning_message = " ".join(warnings) or None
        activities_written, orders_written = self.repository.upsert_history(
            account,
            activities,
            orders,
            started_at=started_at,
            cash_activities_complete=cash_activities_available,
            cash_activity_from=since,
            cash_activity_until=until,
            message=warning_message,
        )
        return BackfillResult(
            account_id=account_id,
            requested_days=requested_days,
            cash_activities_seen=len(activities),
            cash_activities_upserted=activities_written,
            cash_activities_available=cash_activities_available,
            order_records_seen=len(orders),
            order_records_upserted=orders_written,
            order_history_available=order_history_available,
            valuation_history_available=False,
            message=(
                "Read-only history backfill completed. Webull's account endpoints do not provide "
                "historical daily account values, so TWR accrues from scheduled snapshots or "
                "validated statement anchors."
                + (f" {warning_message}" if warning_message else "")
            ),
        )
