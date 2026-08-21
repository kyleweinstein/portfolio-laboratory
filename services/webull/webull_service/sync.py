from __future__ import annotations

import logging
from datetime import timedelta

from .adapters.base import ReadOnlyWebullAdapter, WebullAdapterError
from .models import (
    BackfillResult,
    BrokerageAccount,
    ConnectionState,
    SyncResult,
    utc_now,
)
from .repository import PortfolioRepository, RepositoryError

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(
        self,
        adapter: ReadOnlyWebullAdapter,
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
        try:
            available = (
                accounts if accounts is not None else self.adapter.list_accounts()
            )
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
            balance = self.adapter.get_balance(account_id)
            positions = self.adapter.get_positions(account_id)
            activities = self.adapter.get_cash_activities(
                account_id,
                since=balance.as_of - timedelta(days=self.cash_activity_lookback_days),
                until=balance.as_of,
            )
            return self.repository.commit_sync(
                account,
                balance,
                positions,
                activities,
                started_at=started_at,
            )
        except Exception as exc:
            try:
                self.repository.record_sync_failure(account_id, started_at, str(exc))
            except Exception:
                logger.exception(
                    "Unable to persist the Webull sync failure for account %s",
                    account_id,
                )
            raise

    def sync_selected(self) -> SyncResult:
        state = self.repository.get_connection_state()
        if not state.connected or not state.selected_account_id:
            raise RepositoryError(
                "Connect Webull and select an account before syncing."
            )
        return self.sync_account(state.selected_account_id)

    def sync_all(self) -> list[SyncResult]:
        accounts = self.adapter.list_accounts()
        self.repository.connect_accounts(accounts)
        results: list[SyncResult] = []
        errors: list[str] = []
        for account in accounts:
            try:
                results.append(self.sync_account(account.account_id, accounts=accounts))
            except Exception as exc:  # noqa: BLE001 - one account must not stop the remaining syncs.
                errors.append(f"{account.account_id}: {exc}")
        if errors and not results:
            raise WebullAdapterError("; ".join(errors))
        return results

    def backfill(self, account_id: str, *, days: int) -> BackfillResult:
        requested_days = max(1, min(days, 3650))
        until = utc_now()
        since = until - timedelta(days=requested_days)
        accounts = self.adapter.list_accounts()
        account = next(
            (item for item in accounts if item.account_id == account_id), None
        )
        if account is None:
            raise RepositoryError(
                "The selected account is no longer exposed by Webull."
            )

        activities = self.adapter.get_cash_activities(
            account_id, since=since, until=until
        )
        order_history_available = True
        order_message = ""
        try:
            orders = self.adapter.get_order_history(
                account_id, since=since, until=until
            )
        except WebullAdapterError as exc:
            orders = []
            order_history_available = False
            order_message = f" Order history was unavailable: {exc}"
        activities_written, orders_written = self.repository.upsert_history(
            account, activities, orders
        )
        return BackfillResult(
            account_id=account_id,
            requested_days=requested_days,
            cash_activities_seen=len(activities),
            cash_activities_upserted=activities_written,
            order_records_seen=len(orders),
            order_records_upserted=orders_written,
            order_history_available=order_history_available,
            valuation_history_available=False,
            message=(
                "Cash activity and read-only order history were imported. Webull's account endpoints "
                "do not provide historical daily account values, so TWR accrues from scheduled "
                "snapshots or validated statement anchors." + order_message
            ),
        )
