from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from threading import RLock
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .analytics import analytics_json
from .models import (
    BalanceSnapshot,
    BrokerageAccount,
    BrokerageAccountReference,
    BrokerOAuthCredential,
    CashActivity,
    ConnectionState,
    DiscordSessionData,
    ManagedPublicationList,
    OrderRecord,
    PortfolioCard,
    PortfolioDetail,
    PortfolioState,
    PositionSnapshot,
    PublicationConfig,
    PublicationConfigurationRequest,
    PublishedHolding,
    PublishedPerformancePoint,
    PublishedPortfolioList,
    StatementAnchor,
    SyncAttempt,
    SyncAttemptStatus,
    SyncResult,
    ValuationPoint,
    VerificationAttempt,
    VerificationError,
    VerificationStage,
    VerificationState,
    utc_now,
)
from .publication import validate_public_analytics, validate_publication_title
from .statement_import import (
    WEBULL_SCHEMA_VERSION,
    PreparedStatementImport,
    StatementAnchorImport,
    StatementBackfillBundleV2,
    StatementExternalFlowImport,
    StatementImportCommitResult,
    StatementImportDisposition,
    StatementImportError,
    statement_publication_eligible,
    statement_source_content_hashes,
)

_VERIFICATION_LEASE = timedelta(minutes=7)
_MARKET_TIME_ZONE = ZoneInfo("America/New_York")
_UNKNOWN_AS_OF = datetime(1970, 1, 1, tzinfo=UTC)
_INCOMPLETE_CASH_ACTIVITY_COVERAGE_MESSAGE = (
    "Cash activity coverage remains incomplete; performance remains unavailable."
)


class RepositoryError(RuntimeError):
    pass


class RepositoryConfigurationError(RepositoryError):
    pass


class PortfolioRepository(Protocol):
    def begin_verification_attempt(self) -> tuple[VerificationAttempt, bool]: ...
    def get_verification_attempt(self) -> VerificationAttempt | None: ...
    def advance_verification_attempt(
        self, attempt_id: str, stage: VerificationStage
    ) -> VerificationAttempt: ...
    def finish_verification_attempt(
        self,
        attempt_id: str,
        state: VerificationState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VerificationAttempt: ...
    def connect_accounts(self, accounts: list[BrokerageAccount]) -> ConnectionState: ...
    def get_connection_state(self) -> ConnectionState: ...
    def select_account(self, account_id: str) -> ConnectionState: ...
    def disconnect(self) -> None: ...
    def commit_sync(
        self,
        account: BrokerageAccount,
        balance: BalanceSnapshot,
        positions: list[PositionSnapshot],
        activities: list[CashActivity],
        *,
        started_at: datetime,
        cash_activities_complete: bool,
        cash_activity_from: datetime,
        cash_activity_until: datetime,
        warning: str | None = None,
    ) -> SyncResult: ...
    def record_sync_failure(
        self,
        account_id: str,
        started_at: datetime,
        message: str,
        *,
        cash_activities_complete: bool | None = None,
    ) -> None: ...
    def upsert_history(
        self,
        account: BrokerageAccount,
        activities: list[CashActivity],
        orders: list[OrderRecord],
        *,
        started_at: datetime,
        cash_activities_complete: bool,
        cash_activity_from: datetime,
        cash_activity_until: datetime,
        message: str | None = None,
    ) -> tuple[int, int]: ...
    def get_latest_sync_attempt(self, account_id: str) -> SyncAttempt | None: ...
    def get_latest_cash_activity_coverage(self, account_id: str) -> bool | None: ...
    def cash_activity_coverage_spans(
        self, account_id: str, *, start: datetime, end: datetime
    ) -> bool: ...
    def import_statement_anchor(self, anchor: StatementAnchor) -> StatementAnchor: ...
    def commit_statement_import(
        self,
        owner_github_id: str,
        prepared: PreparedStatementImport,
    ) -> StatementImportCommitResult: ...
    def get_portfolio(self, account_id: str) -> PortfolioState | None: ...
    def get_performance_inputs(
        self,
        account_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[ValuationPoint], list[CashActivity]]: ...
    def get_recent_activities(
        self, account_id: str, *, limit: int = 100
    ) -> list[CashActivity]: ...
    def get_recent_orders(
        self, account_id: str, *, limit: int = 100
    ) -> list[OrderRecord]: ...
    def configure_publication(
        self, request: PublicationConfigurationRequest
    ) -> PublicationConfig: ...
    def get_publication_config(self, publication_id: str) -> PublicationConfig: ...
    def list_managed_publications(self) -> ManagedPublicationList: ...
    def set_publication_enabled(
        self, publication_id: str, *, enabled: bool
    ) -> PublicationConfig: ...
    def commit_publication(
        self,
        publication_id: str,
        detail: PortfolioDetail,
        *,
        source_snapshot_id: str,
    ) -> PortfolioDetail: ...
    def list_published_portfolios(self) -> PublishedPortfolioList: ...
    def get_published_portfolio(self, slug: str) -> PortfolioDetail: ...
    def save_provider_access_token(
        self,
        provider: str,
        *,
        item_id: str,
        institution_id: str | None,
        access_token: str,
        encryption_key: str,
        owner_github_id: str | None = None,
    ) -> None: ...
    def has_provider_connection(self, provider: str) -> bool: ...
    def get_provider_access_token(
        self, provider: str, *, encryption_key: str
    ) -> str | None: ...
    def save_provider_oauth_credential(
        self,
        provider: str,
        *,
        connection_key: str,
        institution_id: str | None,
        credential: BrokerOAuthCredential,
        encryption_key: str,
    ) -> None: ...
    def get_provider_oauth_credential(
        self, provider: str, *, encryption_key: str
    ) -> BrokerOAuthCredential | None: ...
    def list_provider_account_references(
        self, provider: str
    ) -> tuple[BrokerageAccountReference, ...]: ...
    def write_discord_session(
        self,
        session_id: str,
        session: DiscordSessionData,
        *,
        encryption_key: str,
    ) -> None: ...
    def get_discord_session(
        self, session_id: str, *, encryption_key: str
    ) -> DiscordSessionData | None: ...
    def delete_discord_session(self, session_id: str) -> None: ...
    def request_provider_refresh(self, provider: str) -> None: ...
    def complete_provider_refresh(self, provider: str) -> None: ...


class PostgresRepository:
    def __init__(
        self,
        database_url: str,
        portfolio_owner_github_id: str = "",
    ) -> None:
        self._database_url = database_url
        self._portfolio_owner_github_id = portfolio_owner_github_id

    def _connect(self) -> psycopg.Connection:
        if not self._database_url:
            raise RepositoryConfigurationError("DATABASE_URL is not configured.")
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def begin_verification_attempt(self) -> tuple[VerificationAttempt, bool]:
        now = utc_now()
        attempt_id = str(uuid4())
        with self._connect() as connection, connection.cursor() as cursor:
            self._expire_verification_attempts(cursor, now)
            cursor.execute(
                """
                INSERT INTO verification_attempts (
                    attempt_id, provider, state, stage, started_at, updated_at,
                    lease_expires_at
                ) VALUES (%s, 'webull', 'RUNNING', 'STARTING', %s, %s, %s)
                ON CONFLICT (provider) WHERE state = 'RUNNING' DO NOTHING
                RETURNING attempt_id, state, stage, started_at, updated_at,
                          lease_expires_at, completed_at, error_code, error_message
                """,
                (attempt_id, now, now, now + _VERIFICATION_LEASE),
            )
            row = cursor.fetchone()
            created = row is not None
            if row is None:
                cursor.execute(
                    """
                    SELECT attempt_id, state, stage, started_at, updated_at,
                           lease_expires_at, completed_at, error_code, error_message
                    FROM verification_attempts
                    WHERE provider = 'webull' AND state = 'RUNNING'
                    ORDER BY started_at DESC, attempt_id DESC
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
            if row is None:
                raise RepositoryError("Unable to create a verification attempt.")
        return _verification_from_row(row), created

    def get_verification_attempt(self) -> VerificationAttempt | None:
        now = utc_now()
        with self._connect() as connection, connection.cursor() as cursor:
            self._expire_verification_attempts(cursor, now)
            cursor.execute(
                """
                SELECT attempt_id, state, stage, started_at, updated_at,
                       lease_expires_at, completed_at, error_code, error_message
                FROM verification_attempts
                WHERE provider = 'webull'
                ORDER BY started_at DESC, attempt_id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
        return _verification_from_row(row) if row is not None else None

    def advance_verification_attempt(
        self, attempt_id: str, stage: VerificationStage
    ) -> VerificationAttempt:
        now = utc_now()
        with self._connect() as connection, connection.cursor() as cursor:
            self._expire_verification_attempts(cursor, now)
            cursor.execute(
                """
                UPDATE verification_attempts
                SET stage = %s, updated_at = %s, lease_expires_at = %s
                WHERE attempt_id = %s AND provider = 'webull' AND state = 'RUNNING'
                RETURNING attempt_id, state, stage, started_at, updated_at,
                          lease_expires_at, completed_at, error_code, error_message
                """,
                (stage.name, now, now + _VERIFICATION_LEASE, attempt_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise RepositoryError("Verification attempt is no longer running.")
        return _verification_from_row(row)

    def finish_verification_attempt(
        self,
        attempt_id: str,
        state: VerificationState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VerificationAttempt:
        if state not in {VerificationState.SUCCEEDED, VerificationState.FAILED}:
            raise ValueError("A verification attempt must finish succeeded or failed.")
        now = utc_now()
        stage = (
            VerificationStage.COMPLETE.name
            if state is VerificationState.SUCCEEDED
            else None
        )
        with self._connect() as connection, connection.cursor() as cursor:
            self._expire_verification_attempts(cursor, now)
            cursor.execute(
                """
                UPDATE verification_attempts
                SET state = %s,
                    stage = COALESCE(%s, stage),
                    updated_at = %s,
                    completed_at = %s,
                    error_code = %s,
                    error_message = %s
                WHERE attempt_id = %s AND provider = 'webull' AND state = 'RUNNING'
                RETURNING attempt_id, state, stage, started_at, updated_at,
                          lease_expires_at, completed_at, error_code, error_message
                """,
                (
                    state.name,
                    stage,
                    now,
                    now,
                    error_code[:64] if error_code else None,
                    error_message[:500] if error_message else None,
                    attempt_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT attempt_id, state, stage, started_at, updated_at,
                           lease_expires_at, completed_at, error_code, error_message
                    FROM verification_attempts
                    WHERE attempt_id = %s AND provider = 'webull'
                    """,
                    (attempt_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise RepositoryError("Verification attempt was not found.")
        return _verification_from_row(row)

    @staticmethod
    def _expire_verification_attempts(cursor: psycopg.Cursor, now: datetime) -> None:
        cursor.execute(
            """
            UPDATE verification_attempts
            SET state = 'TIMED_OUT',
                updated_at = %s,
                completed_at = %s,
                error_code = 'verification_timeout',
                error_message = 'Verification timed out before completion.'
            WHERE provider = 'webull'
              AND state = 'RUNNING'
              AND lease_expires_at <= %s
            """,
            (now, now, now),
        )

    def connect_accounts(self, accounts: list[BrokerageAccount]) -> ConnectionState:
        if not accounts:
            raise RepositoryError("Webull returned no accounts to connect.")
        now = utc_now()
        with self._connect() as connection, connection.cursor() as cursor:
            for account in accounts:
                self._upsert_account(cursor, account)
            cursor.execute(
                "SELECT selected_account_id FROM service_connection_state WHERE provider = 'webull'"
            )
            row = cursor.fetchone() or {}
            account_ids = {account.account_id for account in accounts}
            selected = row.get("selected_account_id")
            if selected not in account_ids:
                selected = accounts[0].account_id
            cursor.execute(
                """
                INSERT INTO service_connection_state
                    (provider, connected, selected_account_id, connected_at, updated_at)
                VALUES ('webull', TRUE, %s, %s, %s)
                ON CONFLICT (provider) DO UPDATE SET
                    connected = TRUE,
                    selected_account_id = EXCLUDED.selected_account_id,
                    connected_at = COALESCE(service_connection_state.connected_at, EXCLUDED.connected_at),
                    updated_at = EXCLUDED.updated_at
                """,
                (selected, now, now),
            )
        return self.get_connection_state()

    def get_connection_state(self) -> ConnectionState:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT connected, selected_account_id, connected_at FROM service_connection_state WHERE provider = 'webull'"
            )
            state = cursor.fetchone() or {
                "connected": False,
                "selected_account_id": None,
                "connected_at": None,
            }
            cursor.execute(
                """
                SELECT account_id, provider, account_type, status, currency
                FROM brokerage_accounts
                WHERE provider = 'webull'
                ORDER BY account_id
                """
            )
            accounts = tuple(BrokerageAccount(**row) for row in cursor.fetchall())
            selected_account_id = state["selected_account_id"]
            if selected_account_id:
                cursor.execute(
                    "SELECT last_synced_at FROM brokerage_accounts WHERE account_id = %s",
                    (selected_account_id,),
                )
                last_synced = (cursor.fetchone() or {}).get("last_synced_at")
            else:
                last_synced = None
        return ConnectionState(
            connected=bool(state["connected"]),
            accounts=accounts,
            selected_account_id=selected_account_id,
            connected_at=state["connected_at"],
            last_synced_at=last_synced,
        )

    def select_account(self, account_id: str) -> ConnectionState:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM brokerage_accounts WHERE account_id = %s", (account_id,)
            )
            if cursor.fetchone() is None:
                raise KeyError(account_id)
            cursor.execute(
                """
                UPDATE service_connection_state
                SET selected_account_id = %s, updated_at = NOW()
                WHERE provider = 'webull' AND connected = TRUE
                """,
                (account_id,),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("Connect Webull before selecting an account.")
        return self.get_connection_state()

    def disconnect(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE service_connection_state
                SET connected = FALSE, selected_account_id = NULL, connected_at = NULL, updated_at = NOW()
                WHERE provider = 'webull'
                """
            )
            # Cascades remove imported private account data, snapshots and history.
            cursor.execute("DELETE FROM brokerage_accounts WHERE provider = 'webull'")

    def commit_sync(
        self,
        account: BrokerageAccount,
        balance: BalanceSnapshot,
        positions: list[PositionSnapshot],
        activities: list[CashActivity],
        *,
        started_at: datetime,
        cash_activities_complete: bool,
        cash_activity_from: datetime,
        cash_activity_until: datetime,
        warning: str | None = None,
    ) -> SyncResult:
        _validate_snapshot(account.account_id, balance, positions, activities)
        positions_by_id = {
            position.external_position_id: position for position in positions
        }
        activities_by_id = {
            activity.external_activity_id: activity for activity in activities
        }
        snapshot_id = str(uuid4())
        sync_run_id = str(uuid4())
        completed_at = utc_now()
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            self._upsert_account(cursor, account)
            effective_cash_coverage = self._update_cash_activity_coverage(
                cursor,
                account.account_id,
                fetched_from=cash_activity_from,
                fetched_until=cash_activity_until,
                fetch_complete=cash_activities_complete,
            )
            effective_warning = (
                warning
                if warning
                else _INCOMPLETE_CASH_ACTIVITY_COVERAGE_MESSAGE
                if not effective_cash_coverage
                else None
            )
            cursor.execute(
                """
                    INSERT INTO portfolio_snapshots (
                        snapshot_id, account_id, captured_at, currency, equity, cash,
                        market_value, buying_power, day_profit_loss, unrealized_profit_loss,
                        cash_source, internal_account_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        (SELECT internal_account_id FROM brokerage_accounts WHERE account_id = %s)
                    )
                    """,
                (
                    snapshot_id,
                    account.account_id,
                    balance.as_of,
                    balance.currency,
                    balance.equity,
                    balance.cash,
                    balance.market_value,
                    balance.buying_power,
                    balance.day_profit_loss,
                    balance.unrealized_profit_loss,
                    balance.cash_source.value,
                    account.account_id,
                ),
            )
            if positions_by_id:
                cursor.executemany(
                    """
                        INSERT INTO position_snapshots (
                            snapshot_id, external_position_id, account_id, symbol,
                            instrument_type, currency, quantity, last_price, market_value,
                            average_cost, cost_basis, unrealized_profit_loss,
                            internal_account_id
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            (SELECT internal_account_id FROM brokerage_accounts WHERE account_id = %s)
                        )
                        """,
                    [
                        (
                            snapshot_id,
                            item.external_position_id,
                            item.account_id,
                            item.symbol,
                            item.instrument_type,
                            item.currency,
                            item.quantity,
                            item.last_price,
                            item.market_value,
                            item.average_cost,
                            item.cost_basis,
                            item.unrealized_profit_loss,
                            item.account_id,
                        )
                        for item in positions_by_id.values()
                    ],
                )
            self._upsert_activities(cursor, list(activities_by_id.values()))
            cursor.execute(
                """
                    INSERT INTO sync_runs (
                        sync_run_id, account_id, status, started_at, completed_at,
                        snapshot_id, cash_activities_complete, message
                    ) VALUES (%s, %s, 'SUCCESS', %s, %s, %s, %s, %s)
                    """,
                (
                    sync_run_id,
                    account.account_id,
                    started_at,
                    completed_at,
                    snapshot_id,
                    effective_cash_coverage,
                    effective_warning[:500] if effective_warning else None,
                ),
            )
            cursor.execute(
                "UPDATE brokerage_accounts SET last_synced_at = %s, updated_at = %s WHERE account_id = %s",
                (completed_at, completed_at, account.account_id),
            )
        return SyncResult(
            sync_run_id=sync_run_id,
            snapshot_id=snapshot_id,
            account_id=account.account_id,
            captured_at=balance.as_of,
            positions_written=len(positions_by_id),
            cash_activities_seen=len(activities),
            cash_activities_upserted=len(activities_by_id),
            cash_activities_complete=effective_cash_coverage,
            warning=effective_warning,
        )

    def record_sync_failure(
        self,
        account_id: str,
        started_at: datetime,
        message: str,
        *,
        cash_activities_complete: bool | None = None,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO brokerage_accounts (
                    account_id, provider, internal_account_id, provider_account_id,
                    account_type, status, currency, owner_github_id, account_handle
                )
                VALUES (
                    %s, 'webull', %s, %s, 'UNKNOWN', 'UNKNOWN', 'USD', %s, %s
                )
                ON CONFLICT (account_id) DO NOTHING
                """,
                (
                    account_id,
                    _internal_account_uuid("webull", account_id),
                    account_id,
                    self._portfolio_owner_github_id or None,
                    _internal_account_uuid("webull", account_id),
                ),
            )
            cursor.execute(
                """
                INSERT INTO sync_runs (
                    sync_run_id, account_id, status, started_at, completed_at,
                    cash_activities_complete, message
                ) VALUES (%s, %s, 'ERROR', %s, %s, %s, %s)
                """,
                (
                    str(uuid4()),
                    account_id,
                    started_at,
                    utc_now(),
                    cash_activities_complete,
                    message[:500],
                ),
            )

    def upsert_history(
        self,
        account: BrokerageAccount,
        activities: list[CashActivity],
        orders: list[OrderRecord],
        *,
        started_at: datetime,
        cash_activities_complete: bool,
        cash_activity_from: datetime,
        cash_activity_until: datetime,
        message: str | None = None,
    ) -> tuple[int, int]:
        activities_by_id = {item.external_activity_id: item for item in activities}
        orders_by_id = {item.external_order_id: item for item in orders}
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            self._upsert_account(cursor, account)
            effective_cash_coverage = self._update_cash_activity_coverage(
                cursor,
                account.account_id,
                fetched_from=cash_activity_from,
                fetched_until=cash_activity_until,
                fetch_complete=cash_activities_complete,
            )
            effective_message = (
                message
                if message
                else _INCOMPLETE_CASH_ACTIVITY_COVERAGE_MESSAGE
                if not effective_cash_coverage
                else None
            )
            self._upsert_activities(cursor, list(activities_by_id.values()))
            self._upsert_orders(cursor, list(orders_by_id.values()))
            cursor.execute(
                """
                INSERT INTO sync_runs (
                    sync_run_id, account_id, status, started_at, completed_at,
                    cash_activities_complete, message
                ) VALUES (%s, %s, 'SUCCESS', %s, %s, %s, %s)
                """,
                (
                    str(uuid4()),
                    account.account_id,
                    started_at,
                    utc_now(),
                    effective_cash_coverage,
                    effective_message[:500] if effective_message else None,
                ),
            )
        return len(activities_by_id), len(orders_by_id)

    def get_latest_sync_attempt(self, account_id: str) -> SyncAttempt | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT sync_run_id, status, started_at, completed_at,
                       cash_activities_complete, message
                FROM sync_runs
                WHERE account_id = %s
                ORDER BY completed_at DESC, sync_run_id DESC
                LIMIT 1
                """,
                (account_id,),
            )
            row = cursor.fetchone()
        return _sync_attempt_from_row(row) if row is not None else None

    def get_latest_cash_activity_coverage(self, account_id: str) -> bool | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cash_activity_coverage_start, cash_activity_coverage_end,
                       cash_activity_gap_start, cash_activity_gap_end
                FROM brokerage_accounts
                WHERE account_id = %s
                """,
                (account_id,),
            )
            account_row = cursor.fetchone()
            if account_row is None:
                return None
            if account_row["cash_activity_gap_start"] is not None:
                return False
            if account_row["cash_activity_coverage_start"] is None:
                return None
            cursor.execute(
                """
                SELECT cash_activities_complete
                FROM sync_runs
                WHERE account_id = %s
                  AND cash_activities_complete IS NOT NULL
                ORDER BY completed_at DESC, sync_run_id DESC
                LIMIT 1
                """,
                (account_id,),
            )
            attempt_row = cursor.fetchone()
        return (
            bool(attempt_row["cash_activities_complete"])
            if attempt_row is not None
            else None
        )

    def cash_activity_coverage_spans(
        self, account_id: str, *, start: datetime, end: datetime
    ) -> bool:
        if start > end:
            raise RepositoryError("Cash activity coverage range is invalid.")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cash_activity_coverage_start, cash_activity_coverage_end,
                       cash_activity_gap_start, cash_activity_gap_end
                FROM brokerage_accounts
                WHERE account_id = %s
                """,
                (account_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    """
                    SELECT batch.coverage_start, batch.coverage_end
                    FROM statement_import_batches batch
                    JOIN brokerage_accounts account
                      ON account.internal_account_id = batch.internal_account_id
                    WHERE account.account_id = %s
                      AND batch.publication_eligible = TRUE
                    ORDER BY batch.coverage_start, batch.coverage_end
                    """,
                    (account_id,),
                )
                statement_coverage = cursor.fetchall()
            else:
                statement_coverage = []
        if row is None:
            raise KeyError(account_id)
        intervals = _provider_coverage_intervals(
            row["cash_activity_coverage_start"],
            row["cash_activity_coverage_end"],
            row["cash_activity_gap_start"],
            row["cash_activity_gap_end"],
        )
        intervals.extend(
            _statement_coverage_interval(item["coverage_start"], item["coverage_end"])
            for item in statement_coverage
        )
        return _coverage_intervals_span(intervals, start=start, end=end)

    def import_statement_anchor(self, anchor: StatementAnchor) -> StatementAnchor:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM brokerage_accounts WHERE account_id = %s",
                (anchor.account_id,),
            )
            if cursor.fetchone() is None:
                raise KeyError(anchor.account_id)
            cursor.execute(
                """
                INSERT INTO statement_anchors (
                    account_id, external_statement_id, statement_date, ending_equity,
                    currency, ending_cash, source_sha256
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_id, external_statement_id) DO UPDATE SET
                    statement_date = EXCLUDED.statement_date,
                    ending_equity = EXCLUDED.ending_equity,
                    currency = EXCLUDED.currency,
                    ending_cash = EXCLUDED.ending_cash,
                    source_sha256 = EXCLUDED.source_sha256,
                    imported_at = NOW()
                """,
                (
                    anchor.account_id,
                    anchor.external_statement_id,
                    anchor.statement_date,
                    anchor.ending_equity,
                    anchor.currency,
                    anchor.ending_cash,
                    anchor.source_sha256,
                ),
            )
        return anchor

    def commit_statement_import(
        self,
        owner_github_id: str,
        prepared: PreparedStatementImport,
    ) -> StatementImportCommitResult:
        """Resolve and persist a validated bundle in one private transaction."""

        bundle = prepared.bundle
        target_provider = (
            "webull" if bundle.schema_version == WEBULL_SCHEMA_VERSION else "plaid_m1"
        )
        source_hashes = statement_source_content_hashes(bundle)
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            # account_handle is an opaque, case-sensitive lookup key. It is never
            # cast to UUID and never compared with provider account identifiers.
            cursor.execute(
                """
                SELECT account.account_id, account.internal_account_id,
                       account.connection_id,
                       account.owner_github_id AS account_owner_github_id,
                       connection.owner_github_id AS connection_owner_github_id
                FROM brokerage_accounts account
                JOIN broker_connections connection
                  ON connection.connection_id = account.connection_id
                LEFT JOIN service_connection_state state
                  ON state.provider = 'webull'
                 AND state.selected_account_id = account.account_id
                WHERE account.account_handle = %s COLLATE "C"
                  AND (
                      (
                          account.owner_github_id = %s
                          AND connection.owner_github_id = %s
                      )
                      OR (
                          %s = 'webull'
                          AND UPPER(account.account_type) = 'MARGIN'
                          AND state.selected_account_id IS NOT NULL
                          AND account.owner_github_id IS NULL
                          AND connection.owner_github_id IS NULL
                      )
                  )
                  AND account.provider = %s
                  AND connection.provider = %s
                  AND account.currency = 'USD'
                  AND UPPER(account.status) IN ('ACTIVE', 'READY')
                  AND connection.status = 'READY'
                FOR UPDATE OF account, connection
                """,
                (
                    bundle.account_handle,
                    owner_github_id,
                    owner_github_id,
                    target_provider,
                    target_provider,
                    target_provider,
                ),
            )
            accounts = cursor.fetchall()
            if len(accounts) != 1:
                raise StatementImportError("STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE")
            account = accounts[0]
            account_id = str(account["account_id"])
            internal_account_id = str(account["internal_account_id"])

            # Legacy Webull rows predate owner scoping. Only the currently
            # selected MARGIN account may claim a fully-unowned account and
            # connection. Mixed or different ownership never reaches this row.
            if (
                target_provider == "webull"
                and account["account_owner_github_id"] is None
                and account["connection_owner_github_id"] is None
            ):
                cursor.execute(
                    """
                    UPDATE broker_connections
                    SET owner_github_id = %s, updated_at = NOW()
                    WHERE connection_id = %s AND owner_github_id IS NULL
                    """,
                    (owner_github_id, account["connection_id"]),
                )
                if cursor.rowcount != 1:
                    raise StatementImportError("STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE")
                cursor.execute(
                    """
                    UPDATE brokerage_accounts
                    SET owner_github_id = %s, updated_at = NOW()
                    WHERE account_id = %s AND owner_github_id IS NULL
                    """,
                    (owner_github_id, account_id),
                )
                if cursor.rowcount != 1:
                    raise StatementImportError("STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE")

            cursor.execute(
                """
                INSERT INTO statement_import_batches (
                    batch_id, internal_account_id, owner_github_id, account_handle,
                    payload_sha256, source_document_count, anchor_count,
                    external_flow_count, coverage_start, coverage_end,
                    contiguous_monthly_coverage,
                    external_flow_coverage_complete, publication_eligible
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (batch_id) DO NOTHING
                """,
                (
                    str(bundle.batch_id),
                    internal_account_id,
                    owner_github_id,
                    bundle.account_handle,
                    prepared.decrypted_payload_sha256,
                    len(bundle.sources),
                    len(bundle.anchors),
                    len(bundle.external_flows),
                    bundle.validation.coverage.start,
                    bundle.validation.coverage.end,
                    bundle.validation.coverage.contiguous_monthly_coverage,
                    bundle.validation.coverage.external_flow_coverage_complete,
                    statement_publication_eligible(bundle),
                ),
            )
            inserted_batch = cursor.rowcount == 1
            if not inserted_batch:
                cursor.execute(
                    """
                    SELECT internal_account_id, owner_github_id, account_handle,
                           payload_sha256, source_document_count, anchor_count,
                           external_flow_count, coverage_start, coverage_end,
                           contiguous_monthly_coverage,
                           external_flow_coverage_complete, publication_eligible
                    FROM statement_import_batches
                    WHERE batch_id = %s
                    """,
                    (str(bundle.batch_id),),
                )
                existing_batch = cursor.fetchone()
                if existing_batch is None or not _statement_batch_matches(
                    existing_batch,
                    internal_account_id=internal_account_id,
                    owner_github_id=owner_github_id,
                    account_handle=bundle.account_handle,
                    payload_sha256=prepared.decrypted_payload_sha256,
                    source_document_count=len(bundle.sources),
                    anchor_count=len(bundle.anchors),
                    external_flow_count=len(bundle.external_flows),
                    coverage_start=bundle.validation.coverage.start,
                    coverage_end=bundle.validation.coverage.end,
                    contiguous_monthly_coverage=(
                        bundle.validation.coverage.contiguous_monthly_coverage
                    ),
                    external_flow_coverage_complete=(
                        bundle.validation.coverage.external_flow_coverage_complete
                    ),
                    publication_eligible=statement_publication_eligible(bundle),
                ):
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
                return _postgres_statement_commit_result(
                    cursor,
                    bundle=bundle,
                    internal_account_id=internal_account_id,
                    disposition=StatementImportDisposition.ALREADY_IMPORTED,
                )

            for source in bundle.sources:
                cursor.execute(
                    """
                    INSERT INTO statement_import_sources (
                        source_id, internal_account_id, source_content_sha256,
                        provider, source_kind, parser_version, page_count,
                        statement_start,
                        statement_end, account_fingerprint
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_id) DO NOTHING
                    """,
                    (
                        source.source_id,
                        internal_account_id,
                        source_hashes[source.source_id],
                        source.provider,
                        source.source_kind,
                        source.parser_version,
                        source.page_count,
                        source.statement_start,
                        source.statement_end,
                        source.account_fingerprint,
                    ),
                )
                cursor.execute(
                    """
                    SELECT internal_account_id, source_content_sha256
                    FROM statement_import_sources
                    WHERE source_id = %s
                    """,
                    (source.source_id,),
                )
                stored_source = cursor.fetchone()
                if (
                    stored_source is None
                    or str(stored_source["internal_account_id"]) != internal_account_id
                    or str(stored_source["source_content_sha256"]).strip()
                    != source_hashes[source.source_id]
                ):
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
                cursor.execute(
                    """
                    INSERT INTO statement_import_batch_sources (batch_id, source_id)
                    VALUES (%s, %s)
                    ON CONFLICT (batch_id, source_id) DO NOTHING
                    """,
                    (str(bundle.batch_id), source.source_id),
                )

            for anchor in bundle.anchors:
                statement_at = _statement_market_close(anchor.date)
                cursor.execute(
                    """
                    INSERT INTO statement_anchors (
                        account_id, external_statement_id, statement_date,
                        ending_equity, currency, ending_cash, source_sha256,
                        internal_account_id, statement_import_batch_id,
                        statement_source_id, cash_balance, margin_debit_balance,
                        signed_cash_balance, quality
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (account_id, external_statement_id) DO NOTHING
                    """,
                    (
                        account_id,
                        anchor.source_id,
                        statement_at,
                        anchor.net_liquidation_value,
                        anchor.currency,
                        anchor.signed_cash_balance,
                        anchor.source_id.removeprefix("sha256:"),
                        internal_account_id,
                        str(bundle.batch_id),
                        anchor.source_id,
                        anchor.cash_balance,
                        anchor.margin_debit_balance,
                        anchor.signed_cash_balance,
                        anchor.quality,
                    ),
                )
                cursor.execute(
                    """
                    SELECT statement_date, ending_equity, currency, ending_cash,
                           source_sha256, internal_account_id, statement_source_id,
                           cash_balance, margin_debit_balance,
                           signed_cash_balance, quality
                    FROM statement_anchors
                    WHERE account_id = %s AND external_statement_id = %s
                    """,
                    (account_id, anchor.source_id),
                )
                stored_anchor = cursor.fetchone()
                if stored_anchor is None or not _statement_anchor_matches(
                    stored_anchor,
                    internal_account_id=internal_account_id,
                    source_id=anchor.source_id,
                    statement_at=statement_at,
                    net_liquidation_value=anchor.net_liquidation_value,
                    currency=anchor.currency,
                    cash_balance=anchor.cash_balance,
                    margin_debit_balance=anchor.margin_debit_balance,
                    signed_cash_balance=anchor.signed_cash_balance,
                    quality=anchor.quality,
                ):
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")

            for flow in bundle.external_flows:
                cursor.execute(
                    """
                    INSERT INTO statement_external_flows (
                        internal_account_id, batch_id, source_id, sequence,
                        flow_date, flow_at, kind, amount, currency,
                        valuation_status,
                        evidence
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (internal_account_id, source_id, sequence)
                    DO NOTHING
                    """,
                    (
                        internal_account_id,
                        str(bundle.batch_id),
                        flow.source_id,
                        flow.sequence,
                        flow.date,
                        flow.occurred_at,
                        flow.kind,
                        flow.amount,
                        flow.currency,
                        flow.valuation_status,
                        flow.evidence,
                    ),
                )
                cursor.execute(
                    """
                    SELECT flow_date, flow_at, kind, amount, currency,
                           valuation_status,
                           evidence
                    FROM statement_external_flows
                    WHERE internal_account_id = %s
                      AND source_id = %s
                      AND sequence = %s
                    """,
                    (internal_account_id, flow.source_id, flow.sequence),
                )
                stored_flow = cursor.fetchone()
                if stored_flow is None or not _statement_flow_matches(
                    stored_flow,
                    flow_date=flow.date,
                    flow_at=flow.occurred_at,
                    kind=flow.kind,
                    amount=flow.amount,
                    currency=flow.currency,
                    valuation_status=flow.valuation_status,
                    evidence=flow.evidence,
                ):
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
            commit_result = _postgres_statement_commit_result(
                cursor,
                bundle=bundle,
                internal_account_id=internal_account_id,
                disposition=StatementImportDisposition.IMPORTED,
            )

        return commit_result

    def get_portfolio(self, account_id: str) -> PortfolioState | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT account_id, provider, account_type, status, currency
                FROM brokerage_accounts WHERE account_id = %s
                """,
                (account_id,),
            )
            account_row = cursor.fetchone()
            if account_row is None:
                raise KeyError(account_id)
            cursor.execute(
                """
                SELECT * FROM portfolio_snapshots
                WHERE account_id = %s
                ORDER BY captured_at DESC, created_at DESC
                LIMIT 1
                """,
                (account_id,),
            )
            snapshot = cursor.fetchone()
            if snapshot is None:
                return None
            cursor.execute(
                "SELECT * FROM position_snapshots WHERE snapshot_id = %s ORDER BY market_value DESC, symbol",
                (snapshot["snapshot_id"],),
            )
            positions = tuple(_position_from_row(row) for row in cursor.fetchall())
        return PortfolioState(
            account=BrokerageAccount(**account_row),
            balance=_balance_from_row(snapshot),
            positions=positions,
            snapshot_id=str(snapshot["snapshot_id"]),
        )

    def get_performance_inputs(
        self,
        account_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[ValuationPoint], list[CashActivity]]:
        filters = ["account_id = %s"]
        parameters: list[object] = [account_id]
        if start is not None:
            filters.append("valuation_at >= %s")
            parameters.append(start)
        if end is not None:
            filters.append("valuation_at <= %s")
            parameters.append(end)
        where = " AND ".join(filters)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH valuations AS (
                    SELECT snapshot.account_id, snapshot.captured_at AS valuation_at,
                           snapshot.equity AS value, snapshot.created_at,
                           account.provider || '_snapshot' AS source
                    FROM portfolio_snapshots snapshot
                    JOIN brokerage_accounts account
                      ON account.account_id = snapshot.account_id
                    WHERE captured_at > %s
                      AND (captured_at AT TIME ZONE 'America/New_York')::time >= TIME '16:00'
                    UNION ALL
                    SELECT account_id, statement_date AS valuation_at, ending_equity AS value, imported_at AS created_at,
                           'statement_anchor' AS source
                    FROM statement_anchors
                ), daily AS (
                    SELECT DISTINCT ON ((valuation_at AT TIME ZONE 'America/New_York')::date)
                        valuation_at, value, source
                    FROM valuations
                    WHERE {where}
                    ORDER BY (valuation_at AT TIME ZONE 'America/New_York')::date, valuation_at DESC, created_at DESC
                )
                SELECT valuation_at, value, source FROM daily ORDER BY valuation_at
                """,
                [_UNKNOWN_AS_OF, *parameters],
            )
            valuations = [
                ValuationPoint(
                    at=row["valuation_at"], value=row["value"], source=row["source"]
                )
                for row in cursor.fetchall()
            ]

            activity_filters = ["account_id = %s"]
            activity_parameters: list[object] = [account_id]
            if start is not None:
                activity_filters.append("occurred_at >= %s")
                activity_parameters.append(start)
            if end is not None:
                activity_filters.append("occurred_at <= %s")
                activity_parameters.append(end)
            cursor.execute(
                f"""
                WITH provider_flows AS (
                    SELECT activity.account_id,
                           activity.external_activity_id,
                           activity.activity_type,
                           activity.occurred_at,
                           activity.amount,
                           activity.currency,
                           activity.status,
                           activity.description,
                           activity.is_external_flow,
                           account.internal_account_id,
                           (activity.occurred_at AT TIME ZONE
                               'America/New_York')::DATE AS match_date,
                           CASE
                               WHEN LOWER(activity.activity_type) = 'deposit'
                                   THEN 'deposit'
                               WHEN LOWER(activity.activity_type) = 'withdrawal'
                                   THEN 'withdrawal'
                               ELSE 'transfer'
                           END AS match_kind,
                           ROW_NUMBER() OVER (
                               PARTITION BY account.internal_account_id,
                                   (activity.occurred_at AT TIME ZONE
                                       'America/New_York')::DATE,
                                   CASE
                                       WHEN LOWER(activity.activity_type) = 'deposit'
                                           THEN 'deposit'
                                       WHEN LOWER(activity.activity_type) = 'withdrawal'
                                           THEN 'withdrawal'
                                       ELSE 'transfer'
                                   END,
                                   activity.amount,
                                   activity.currency
                               ORDER BY activity.external_activity_id
                           ) AS match_ordinal
                    FROM cash_activities activity
                    JOIN brokerage_accounts account
                      ON account.account_id = activity.account_id
                    WHERE activity.is_external_flow = TRUE
                ), statement_flows AS (
                    SELECT account.account_id,
                           'statement:' || flow.source_id || ':' ||
                               flow.sequence::TEXT AS external_activity_id,
                           CASE
                               WHEN flow.kind = 'deposit' THEN 'DEPOSIT'
                               WHEN flow.kind = 'withdrawal' THEN 'WITHDRAWAL'
                               ELSE 'TRANSFER'
                           END AS activity_type,
                           COALESCE(
                               flow.flow_at,
                               (flow.flow_date + TIME '16:00') AT TIME ZONE
                                   'America/New_York'
                           ) AS occurred_at,
                           flow.amount,
                           flow.currency,
                           'POSTED'::TEXT AS status,
                           'Statement-reconciled external flow'::TEXT AS description,
                           TRUE AS is_external_flow,
                           flow.internal_account_id,
                           flow.flow_date AS match_date,
                           CASE
                               WHEN flow.kind = 'deposit' THEN 'deposit'
                               WHEN flow.kind = 'withdrawal' THEN 'withdrawal'
                               ELSE 'transfer'
                           END AS match_kind,
                           ROW_NUMBER() OVER (
                               PARTITION BY flow.internal_account_id,
                                   flow.flow_date,
                                   CASE
                                       WHEN flow.kind = 'deposit' THEN 'deposit'
                                       WHEN flow.kind = 'withdrawal' THEN 'withdrawal'
                                       ELSE 'transfer'
                                   END,
                                   flow.amount,
                                   flow.currency
                               ORDER BY flow.source_id, flow.sequence
                           ) AS match_ordinal
                    FROM statement_external_flows flow
                    JOIN brokerage_accounts account
                      ON account.internal_account_id = flow.internal_account_id
                    WHERE flow.valuation_status = 'reported'
                      AND flow.amount IS NOT NULL
                ), effective_flows AS (
                    SELECT account_id, external_activity_id, activity_type,
                           occurred_at, amount, currency, status, description,
                           is_external_flow
                    FROM provider_flows
                    UNION ALL
                    SELECT statement.account_id,
                           statement.external_activity_id,
                           statement.activity_type,
                           statement.occurred_at,
                           statement.amount,
                           statement.currency,
                           statement.status,
                           statement.description,
                           statement.is_external_flow
                    FROM statement_flows statement
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM provider_flows provider
                        WHERE provider.internal_account_id =
                                  statement.internal_account_id
                          AND provider.match_date = statement.match_date
                          AND provider.match_kind = statement.match_kind
                          AND provider.amount = statement.amount
                          AND provider.currency = statement.currency
                          AND provider.match_ordinal = statement.match_ordinal
                    )
                )
                SELECT account_id, external_activity_id, activity_type,
                       occurred_at, amount, currency, status, description,
                       is_external_flow
                FROM effective_flows
                WHERE {" AND ".join(activity_filters)}
                ORDER BY occurred_at
                """,
                activity_parameters,
            )
            activities = [_activity_from_row(row) for row in cursor.fetchall()]
        return valuations, activities

    def get_recent_activities(
        self, account_id: str, *, limit: int = 100
    ) -> list[CashActivity]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM cash_activities WHERE account_id = %s ORDER BY occurred_at DESC LIMIT %s",
                (account_id, max(1, min(limit, 500))),
            )
            return [_activity_from_row(row) for row in cursor.fetchall()]

    def get_recent_orders(
        self, account_id: str, *, limit: int = 100
    ) -> list[OrderRecord]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM order_records WHERE account_id = %s
                ORDER BY COALESCE(filled_at, placed_at) DESC NULLS LAST LIMIT %s
                """,
                (account_id, max(1, min(limit, 500))),
            )
            return [_order_from_row(row) for row in cursor.fetchall()]

    def configure_publication(
        self, request: PublicationConfigurationRequest
    ) -> PublicationConfig:
        now = utc_now()
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            if request.account_handle:
                cursor.execute(
                    """
                    SELECT account_id, internal_account_id, provider
                    FROM brokerage_accounts
                    WHERE account_handle = %s COLLATE "C"
                      AND (%s = '' OR owner_github_id = %s)
                    """,
                    (
                        request.account_handle,
                        self._portfolio_owner_github_id,
                        self._portfolio_owner_github_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    SELECT account_id, internal_account_id, provider
                    FROM brokerage_accounts WHERE account_id = %s
                    """,
                    (request.account_id,),
                )
            account = cursor.fetchone()
            if account is None:
                raise KeyError(request.account_handle or request.account_id)
            title = validate_publication_title(
                request.title, str(account["account_id"])
            )
            cursor.execute(
                """
                INSERT INTO published_portfolios (
                    publication_id, internal_account_id, provider, slug, title,
                    benchmark_symbol, enabled, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (internal_account_id) DO UPDATE SET
                    slug = EXCLUDED.slug,
                    title = EXCLUDED.title,
                    benchmark_symbol = EXCLUDED.benchmark_symbol,
                    enabled = EXCLUDED.enabled,
                    updated_at = EXCLUDED.updated_at
                RETURNING publication_id, internal_account_id, provider, slug,
                          title, benchmark_symbol, enabled, active_revision_id,
                          updated_at
                """,
                (
                    str(uuid4()),
                    account["internal_account_id"],
                    account["provider"],
                    request.slug,
                    title,
                    request.benchmark_symbol,
                    request.enabled,
                    now,
                ),
            )
            row = cursor.fetchone()
            row["account_id"] = account["account_id"]
        return _publication_config_from_row(row)

    def write_discord_session(
        self,
        session_id: str,
        session: DiscordSessionData,
        *,
        encryption_key: str,
    ) -> None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Discord session encryption is not configured."
            )
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM discord_viewer_sessions WHERE expires_at <= NOW()"
            )
            cursor.execute(
                """
                INSERT INTO discord_viewer_sessions (
                    session_id_hash, discord_user_id, username, display_name,
                    guild_id, encrypted_access_token, encrypted_refresh_token,
                    token_expires_at, membership_checked_at, expires_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    PGP_SYM_ENCRYPT(%s, %s, 'cipher-algo=aes256'),
                    PGP_SYM_ENCRYPT(%s, %s, 'cipher-algo=aes256'),
                    %s, %s, %s, NOW()
                )
                ON CONFLICT (session_id_hash) DO UPDATE SET
                    discord_user_id = EXCLUDED.discord_user_id,
                    username = EXCLUDED.username,
                    display_name = EXCLUDED.display_name,
                    guild_id = EXCLUDED.guild_id,
                    encrypted_access_token = EXCLUDED.encrypted_access_token,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    token_expires_at = EXCLUDED.token_expires_at,
                    membership_checked_at = EXCLUDED.membership_checked_at,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
                """,
                (
                    _discord_session_hash(session_id),
                    session.discord_id,
                    session.username,
                    session.display_name,
                    session.guild_id,
                    session.access_token.get_secret_value(),
                    encryption_key,
                    session.refresh_token.get_secret_value(),
                    encryption_key,
                    session.token_expires_at,
                    session.membership_checked_at,
                    session.expires_at,
                ),
            )

    def get_discord_session(
        self, session_id: str, *, encryption_key: str
    ) -> DiscordSessionData | None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Discord session encryption is not configured."
            )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT discord_user_id, username, display_name, guild_id,
                       PGP_SYM_DECRYPT(encrypted_access_token, %s) AS access_token,
                       PGP_SYM_DECRYPT(encrypted_refresh_token, %s) AS refresh_token,
                       token_expires_at, membership_checked_at, expires_at
                FROM discord_viewer_sessions
                WHERE session_id_hash = %s AND expires_at > NOW()
                """,
                (encryption_key, encryption_key, _discord_session_hash(session_id)),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return DiscordSessionData(
            discord_id=row["discord_user_id"],
            username=row["username"],
            display_name=row["display_name"],
            guild_id=row["guild_id"],
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            token_expires_at=row["token_expires_at"],
            membership_checked_at=row["membership_checked_at"],
            expires_at=row["expires_at"],
        )

    def delete_discord_session(self, session_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM discord_viewer_sessions WHERE session_id_hash = %s",
                (_discord_session_hash(session_id),),
            )

    def request_provider_refresh(self, provider: str) -> None:
        if provider not in {"plaid_m1", "schwab"}:
            raise RepositoryError("This provider does not support refresh requests.")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO provider_refresh_requests (
                    provider, requested_at, processed_at, updated_at
                ) VALUES (%s, NOW(), NULL, NOW())
                ON CONFLICT (provider) DO UPDATE SET
                    requested_at = NOW(), processed_at = NULL, updated_at = NOW()
                """,
                (provider,),
            )

    def complete_provider_refresh(self, provider: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE provider_refresh_requests
                SET processed_at = NOW(), updated_at = NOW()
                WHERE provider = %s AND processed_at IS NULL
                """,
                (provider,),
            )

    def save_provider_access_token(
        self,
        provider: str,
        *,
        item_id: str,
        institution_id: str | None,
        access_token: str,
        encryption_key: str,
        owner_github_id: str | None = None,
    ) -> None:
        if provider not in {"plaid_m1", "schwab"}:
            raise RepositoryError(
                "This provider does not use stored OAuth credentials."
            )
        if not access_token or not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        connection_id = str(uuid5(NAMESPACE_URL, f"portfolio-lab:{provider}:{item_id}"))
        reference = hashlib.sha256(item_id.encode("utf-8")).hexdigest()
        owner = owner_github_id or self._portfolio_owner_github_id or None
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                INSERT INTO broker_connections (
                    connection_id, provider, credential_reference,
                    institution_id, status, owner_github_id, updated_at
                ) VALUES (%s, %s, %s, %s, 'READY', %s, NOW())
                ON CONFLICT (connection_id) DO UPDATE SET
                    institution_id = EXCLUDED.institution_id,
                    status = 'READY',
                    owner_github_id = COALESCE(
                        broker_connections.owner_github_id,
                        EXCLUDED.owner_github_id
                    ),
                    updated_at = NOW()
                RETURNING owner_github_id
                """,
                (connection_id, provider, reference, institution_id, owner),
            )
            stored_connection = cursor.fetchone()
            if (
                owner is not None
                and stored_connection is not None
                and stored_connection["owner_github_id"] != owner
            ):
                raise RepositoryError(
                    "The provider connection belongs to another owner."
                )
            cursor.execute(
                """
                INSERT INTO broker_connection_credentials (
                    connection_id, encrypted_access_token, encrypted_at
                ) VALUES (
                    %s, PGP_SYM_ENCRYPT(%s, %s, 'cipher-algo=aes256'), NOW()
                )
                ON CONFLICT (connection_id) DO UPDATE SET
                    encrypted_access_token = EXCLUDED.encrypted_access_token,
                    encrypted_at = NOW()
                """,
                (connection_id, access_token, encryption_key),
            )

    def has_provider_connection(self, provider: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM broker_connections connection
                JOIN broker_connection_credentials credential
                  ON credential.connection_id = connection.connection_id
                WHERE connection.provider = %s AND connection.status = 'READY'
                LIMIT 1
                """,
                (provider,),
            )
            return cursor.fetchone() is not None

    def get_provider_access_token(
        self, provider: str, *, encryption_key: str
    ) -> str | None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT PGP_SYM_DECRYPT(
                    credential.encrypted_access_token, %s
                ) AS access_token
                FROM broker_connections connection
                JOIN broker_connection_credentials credential
                  ON credential.connection_id = connection.connection_id
                WHERE connection.provider = %s AND connection.status = 'READY'
                ORDER BY connection.updated_at DESC
                LIMIT 1
                """,
                (encryption_key, provider),
            )
            row = cursor.fetchone()
        return str(row["access_token"]) if row is not None else None

    def save_provider_oauth_credential(
        self,
        provider: str,
        *,
        connection_key: str,
        institution_id: str | None,
        credential: BrokerOAuthCredential,
        encryption_key: str,
    ) -> None:
        if (
            provider not in {"plaid_m1", "schwab"}
            or credential.provider.value != provider
        ):
            raise RepositoryError("The OAuth credential provider does not match.")
        if not connection_key or not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        access_token = credential.access_token.get_secret_value()
        refresh_token = (
            credential.refresh_token.get_secret_value()
            if credential.refresh_token is not None
            else None
        )
        connection_id = str(
            uuid5(NAMESPACE_URL, f"portfolio-lab:{provider}:{connection_key}")
        )
        reference = hashlib.sha256(connection_key.encode("utf-8")).hexdigest()
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                INSERT INTO broker_connections (
                    connection_id, provider, credential_reference,
                    institution_id, status, updated_at
                ) VALUES (%s, %s, %s, %s, 'READY', NOW())
                ON CONFLICT (connection_id) DO UPDATE SET
                    institution_id = EXCLUDED.institution_id,
                    status = 'READY',
                    updated_at = NOW()
                """,
                (connection_id, provider, reference, institution_id),
            )
            cursor.execute(
                """
                INSERT INTO broker_connection_credentials (
                    connection_id, encrypted_access_token,
                    encrypted_refresh_token, access_token_expires_at,
                    refresh_token_expires_at, encrypted_at
                ) VALUES (
                    %s,
                    PGP_SYM_ENCRYPT(%s, %s, 'cipher-algo=aes256'),
                    CASE WHEN CAST(%s AS TEXT) IS NULL THEN NULL ELSE
                        PGP_SYM_ENCRYPT(%s, %s, 'cipher-algo=aes256')
                    END,
                    %s, %s, NOW()
                )
                ON CONFLICT (connection_id) DO UPDATE SET
                    encrypted_access_token = EXCLUDED.encrypted_access_token,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    access_token_expires_at = EXCLUDED.access_token_expires_at,
                    refresh_token_expires_at = EXCLUDED.refresh_token_expires_at,
                    encrypted_at = NOW()
                """,
                (
                    connection_id,
                    access_token,
                    encryption_key,
                    refresh_token,
                    refresh_token,
                    encryption_key,
                    credential.access_token_expires_at,
                    credential.refresh_token_expires_at,
                ),
            )

    def get_provider_oauth_credential(
        self, provider: str, *, encryption_key: str
    ) -> BrokerOAuthCredential | None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT connection.provider,
                       PGP_SYM_DECRYPT(
                           credential.encrypted_access_token, %s
                       ) AS access_token,
                       CASE WHEN credential.encrypted_refresh_token IS NULL
                           THEN NULL
                           ELSE PGP_SYM_DECRYPT(
                               credential.encrypted_refresh_token, %s
                           )
                       END AS refresh_token,
                       credential.access_token_expires_at,
                       credential.refresh_token_expires_at
                FROM broker_connections connection
                JOIN broker_connection_credentials credential
                  ON credential.connection_id = connection.connection_id
                WHERE connection.provider = %s AND connection.status = 'READY'
                ORDER BY connection.updated_at DESC
                LIMIT 1
                """,
                (encryption_key, encryption_key, provider),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return BrokerOAuthCredential(
            provider=row["provider"],
            access_token=str(row["access_token"]),
            refresh_token=(
                str(row["refresh_token"]) if row["refresh_token"] is not None else None
            ),
            access_token_expires_at=row["access_token_expires_at"],
            refresh_token_expires_at=row["refresh_token_expires_at"],
        )

    def list_provider_account_references(
        self, provider: str
    ) -> tuple[BrokerageAccountReference, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT account_handle, provider, account_type, currency,
                       last_synced_at
                FROM brokerage_accounts
                WHERE provider = %s
                  AND (%s = '' OR owner_github_id = %s)
                ORDER BY account_type, account_handle
                """,
                (
                    provider,
                    self._portfolio_owner_github_id,
                    self._portfolio_owner_github_id,
                ),
            )
            rows = cursor.fetchall()
        return tuple(
            BrokerageAccountReference(
                account_handle=str(row["account_handle"]),
                provider=row["provider"],
                account_type=row["account_type"],
                currency=row["currency"],
                last_synced_at=row["last_synced_at"],
            )
            for row in rows
        )

    def get_publication_config(self, publication_id: str) -> PublicationConfig:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT publication.publication_id, account.account_id,
                       account.internal_account_id,
                       publication.provider, publication.slug, publication.title,
                       publication.benchmark_symbol, publication.enabled,
                       publication.active_revision_id, publication.updated_at
                FROM published_portfolios publication
                JOIN brokerage_accounts account
                  ON account.internal_account_id = publication.internal_account_id
                WHERE publication.publication_id = %s
                """,
                (publication_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(publication_id)
        return _publication_config_from_row(row)

    def list_managed_publications(self) -> ManagedPublicationList:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT publication.publication_id, account.account_id,
                       account.internal_account_id,
                       publication.provider, publication.slug, publication.title,
                       publication.benchmark_symbol, publication.enabled,
                       publication.active_revision_id, publication.updated_at
                FROM published_portfolios publication
                JOIN brokerage_accounts account
                  ON account.internal_account_id = publication.internal_account_id
                ORDER BY publication.title, publication.slug
                """
            )
            rows = cursor.fetchall()
        return ManagedPublicationList(
            publications=tuple(_publication_config_from_row(row) for row in rows)
        )

    def set_publication_enabled(
        self, publication_id: str, *, enabled: bool
    ) -> PublicationConfig:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE published_portfolios
                SET enabled = %s, updated_at = NOW()
                WHERE publication_id = %s
                """,
                (enabled, publication_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(publication_id)
        return self.get_publication_config(publication_id)

    def commit_publication(
        self,
        publication_id: str,
        detail: PortfolioDetail,
        *,
        source_snapshot_id: str,
    ) -> PortfolioDetail:
        revision_id = str(uuid4())
        serialized_analytics = analytics_json(detail.analytics)
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT publication.slug, publication.title, publication.provider,
                       publication.enabled, publication.internal_account_id
                FROM published_portfolios publication
                WHERE publication.publication_id = %s
                FOR UPDATE OF publication
                """,
                (publication_id,),
            )
            publication = cursor.fetchone()
            if publication is None:
                raise KeyError(publication_id)
            if not publication["enabled"]:
                raise RepositoryError("Enable the portfolio before publishing it.")
            if (
                publication["slug"] != detail.card.slug
                or publication["title"] != detail.card.title
                or publication["provider"] != detail.card.provider.value
            ):
                raise RepositoryError(
                    "Publication detail does not match its configuration."
                )
            cursor.execute(
                """
                SELECT 1 FROM portfolio_snapshots
                WHERE snapshot_id = %s AND internal_account_id = %s
                """,
                (source_snapshot_id, publication["internal_account_id"]),
            )
            if cursor.fetchone() is None:
                raise RepositoryError(
                    "The source snapshot does not belong to this portfolio."
                )
            cursor.execute(
                """
                SELECT 1
                FROM publication_revisions
                WHERE publication_id = %s
                  AND (holdings_as_of AT TIME ZONE 'America/New_York')::date = %s
                LIMIT 1
                """,
                (
                    publication_id,
                    detail.holdings_as_of.astimezone(_MARKET_TIME_ZONE).date(),
                ),
            )
            if cursor.fetchone() is not None:
                # The publication row lock makes this check and the insert below
                # one atomic once-per-market-date operation, including concurrent
                # manual and scheduled requests.
                return self.get_published_portfolio(detail.card.slug)

            # Superseding first is safe because the whole operation is one transaction:
            # any subsequent failure rolls this update back and leaves the last good
            # active revision untouched.
            cursor.execute(
                """
                UPDATE publication_revisions
                SET state = 'SUPERSEDED'
                WHERE publication_id = %s AND state = 'PUBLISHED'
                """,
                (publication_id,),
            )
            cursor.execute(
                """
                INSERT INTO publication_revisions (
                    revision_id, publication_id, source_snapshot_id, state,
                    holdings_as_of, performance_through, ytd_return_percent,
                    gross_exposure_percent, net_exposure_percent,
                    analytical_sleeve_percent, quality, analytics
                ) VALUES (
                    %s, %s, %s, 'PUBLISHED', %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    revision_id,
                    publication_id,
                    source_snapshot_id,
                    detail.holdings_as_of,
                    detail.card.performance_through,
                    detail.card.ytd_return_percent,
                    detail.gross_exposure_percent,
                    detail.net_exposure_percent,
                    detail.analytical_sleeve_percent,
                    detail.card.quality.value,
                    Jsonb(serialized_analytics)
                    if serialized_analytics is not None
                    else None,
                ),
            )
            if detail.holdings:
                cursor.executemany(
                    """
                    INSERT INTO published_holdings (
                        revision_id, ordinal, kind, symbol, name, weight_percent,
                        cost_basis_per_share, return_percent, quality
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            revision_id,
                            ordinal,
                            holding.kind.value,
                            holding.symbol,
                            holding.name,
                            holding.weight_percent,
                            holding.cost_basis_per_share,
                            holding.return_percent,
                            holding.quality.value,
                        )
                        for ordinal, holding in enumerate(detail.holdings)
                    ],
                )
            if detail.card.performance_points:
                cursor.executemany(
                    """
                    INSERT INTO published_performance_points (
                        revision_id, ordinal, at, return_percent,
                        benchmark_return_percent, quality
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            revision_id,
                            ordinal,
                            point.at,
                            point.return_percent,
                            point.benchmark_return_percent,
                            point.quality.value,
                        )
                        for ordinal, point in enumerate(detail.card.performance_points)
                    ],
                )
            cursor.execute(
                """
                UPDATE published_portfolios
                SET active_revision_id = %s, updated_at = NOW()
                WHERE publication_id = %s
                """,
                (revision_id, publication_id),
            )
        return self.get_published_portfolio(detail.card.slug)

    def list_published_portfolios(self) -> PublishedPortfolioList:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT slug FROM published_portfolios
                WHERE enabled = TRUE AND active_revision_id IS NOT NULL
                ORDER BY title, slug
                """
            )
            slugs = [str(row["slug"]) for row in cursor.fetchall()]
        return PublishedPortfolioList(
            portfolios=tuple(self.get_published_portfolio(slug).card for slug in slugs)
        )

    def get_published_portfolio(self, slug: str) -> PortfolioDetail:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT publication.slug, publication.title, publication.provider,
                       publication.benchmark_symbol, revision.revision_id,
                       revision.holdings_as_of, revision.performance_through,
                       revision.ytd_return_percent, revision.gross_exposure_percent,
                       revision.net_exposure_percent,
                       revision.analytical_sleeve_percent, revision.quality,
                       revision.analytics
                FROM published_portfolios publication
                JOIN publication_revisions revision
                  ON revision.revision_id = publication.active_revision_id
                WHERE publication.slug = %s AND publication.enabled = TRUE
                  AND revision.state = 'PUBLISHED'
                """,
                (slug,),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(slug)
            cursor.execute(
                """
                SELECT kind, symbol, name, weight_percent, cost_basis_per_share,
                       return_percent, quality
                FROM published_holdings WHERE revision_id = %s ORDER BY ordinal
                """,
                (row["revision_id"],),
            )
            holdings = tuple(PublishedHolding(**item) for item in cursor.fetchall())
            cursor.execute(
                """
                SELECT at, return_percent, benchmark_return_percent, quality
                FROM published_performance_points
                WHERE revision_id = %s ORDER BY ordinal
                """,
                (row["revision_id"],),
            )
            points = tuple(
                PublishedPerformancePoint(**item) for item in cursor.fetchall()
            )
        return _portfolio_detail_from_rows(row, holdings, points)

    @staticmethod
    def _update_cash_activity_coverage(
        cursor: psycopg.Cursor,
        account_id: str,
        *,
        fetched_from: datetime,
        fetched_until: datetime,
        fetch_complete: bool,
    ) -> bool:
        if fetched_from > fetched_until:
            raise RepositoryError("Cash activity coverage range is invalid.")
        cursor.execute(
            """
            SELECT cash_activity_coverage_start, cash_activity_coverage_end,
                   cash_activity_gap_start, cash_activity_gap_end
            FROM brokerage_accounts
            WHERE account_id = %s
            FOR UPDATE
            """,
            (account_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RepositoryError("Cash activity coverage account was not found.")
        coverage_start = row["cash_activity_coverage_start"]
        coverage_end = row["cash_activity_coverage_end"]
        gap_start = row["cash_activity_gap_start"]
        gap_end = row["cash_activity_gap_end"]
        if (coverage_start is None) != (coverage_end is None):
            raise RepositoryError("Cash activity coverage state is invalid.")
        if (gap_start is None) != (gap_end is None):
            raise RepositoryError("Cash activity coverage state is invalid.")

        if fetch_complete:
            if coverage_start is None:
                merged_coverage_start = fetched_from
                merged_coverage_end = fetched_until
            elif fetched_from <= coverage_end and fetched_until >= coverage_start:
                merged_coverage_start = min(coverage_start, fetched_from)
                merged_coverage_end = max(coverage_end, fetched_until)
            else:
                # A single pair of bounds may describe only one continuous
                # interval. Keep the newer complete interval rather than
                # claiming that the unobserved interval between two disjoint
                # fetches was covered.
                if fetched_until > coverage_end:
                    merged_coverage_start = fetched_from
                    merged_coverage_end = fetched_until
                else:
                    merged_coverage_start = coverage_start
                    merged_coverage_end = coverage_end
            clears_gap = (
                gap_start is not None
                and fetched_from <= gap_start
                and fetched_until >= gap_end
            )
            merged_gap_start = None if clears_gap else gap_start
            merged_gap_end = None if clears_gap else gap_end
            cursor.execute(
                """
                UPDATE brokerage_accounts
                SET cash_activity_coverage_start = %s,
                    cash_activity_coverage_end = %s,
                    cash_activity_gap_start = %s,
                    cash_activity_gap_end = %s,
                    updated_at = NOW()
                WHERE account_id = %s
                """,
                (
                    merged_coverage_start,
                    merged_coverage_end,
                    merged_gap_start,
                    merged_gap_end,
                    account_id,
                ),
            )
            return merged_gap_start is None

        merged_start = min(gap_start, fetched_from) if gap_start else fetched_from
        merged_end = max(gap_end, fetched_until) if gap_end else fetched_until
        cursor.execute(
            """
            UPDATE brokerage_accounts
            SET cash_activity_gap_start = %s,
                cash_activity_gap_end = %s,
                updated_at = NOW()
            WHERE account_id = %s
            """,
            (merged_start, merged_end, account_id),
        )
        return False

    def _upsert_account(
        self,
        cursor: psycopg.Cursor,
        account: BrokerageAccount,
    ) -> None:
        provider = account.provider.value
        owner_github_id = self._portfolio_owner_github_id or None
        connection_id: str | None = None
        if provider in {"plaid_m1", "webull"} and owner_github_id is not None:
            cursor.execute(
                """
                SELECT connection_id, owner_github_id
                FROM broker_connections
                WHERE provider = %s
                  AND status = 'READY'
                  AND (owner_github_id = %s OR owner_github_id IS NULL)
                ORDER BY updated_at DESC, connection_id
                """,
                (provider, owner_github_id),
            )
            ready_connections = cursor.fetchall()
            if len(ready_connections) == 1:
                connection_id = str(ready_connections[0]["connection_id"])
                if ready_connections[0]["owner_github_id"] is None:
                    cursor.execute(
                        """
                        UPDATE broker_connections
                        SET owner_github_id = %s, updated_at = NOW()
                        WHERE connection_id = %s AND owner_github_id IS NULL
                        """,
                        (owner_github_id, connection_id),
                    )
        cursor.execute(
            """
            INSERT INTO brokerage_accounts (
                account_id, provider, internal_account_id, provider_account_id,
                account_type, status, currency, owner_github_id, account_handle,
                connection_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id) DO UPDATE SET
                provider = EXCLUDED.provider,
                provider_account_id = EXCLUDED.provider_account_id,
                account_type = EXCLUDED.account_type,
                status = EXCLUDED.status,
                currency = EXCLUDED.currency,
                owner_github_id = COALESCE(
                    brokerage_accounts.owner_github_id,
                    EXCLUDED.owner_github_id
                ),
                account_handle = COALESCE(
                    brokerage_accounts.account_handle,
                    EXCLUDED.account_handle
                ),
                connection_id = COALESCE(
                    EXCLUDED.connection_id,
                    brokerage_accounts.connection_id
                ),
                updated_at = NOW()
            WHERE brokerage_accounts.owner_github_id IS NULL
               OR EXCLUDED.owner_github_id IS NULL
               OR brokerage_accounts.owner_github_id = EXCLUDED.owner_github_id
            """,
            (
                account.account_id,
                provider,
                _internal_account_uuid(provider, account.account_id),
                _provider_account_id(provider, account.account_id),
                account.account_type,
                account.status,
                account.currency,
                owner_github_id,
                _internal_account_uuid(provider, account.account_id),
                connection_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RepositoryError("The brokerage account belongs to another owner.")

    @staticmethod
    def _upsert_activities(
        cursor: psycopg.Cursor, activities: list[CashActivity]
    ) -> None:
        if not activities:
            return
        cursor.executemany(
            """
            INSERT INTO cash_activities (
                account_id, external_activity_id, activity_type, occurred_at, amount,
                currency, status, description, is_external_flow
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, external_activity_id) DO UPDATE SET
                activity_type = EXCLUDED.activity_type,
                occurred_at = EXCLUDED.occurred_at,
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency,
                status = EXCLUDED.status,
                description = EXCLUDED.description,
                is_external_flow = EXCLUDED.is_external_flow,
                updated_at = NOW()
            """,
            [
                (
                    item.account_id,
                    item.external_activity_id,
                    item.activity_type,
                    item.occurred_at,
                    item.amount,
                    item.currency,
                    item.status,
                    item.description,
                    item.is_external_flow,
                )
                for item in activities
            ],
        )

    @staticmethod
    def _upsert_orders(cursor: psycopg.Cursor, orders: list[OrderRecord]) -> None:
        if not orders:
            return
        cursor.executemany(
            """
            INSERT INTO order_records (
                account_id, external_order_id, client_order_id, symbol, instrument_type,
                side, status, order_type, total_quantity, filled_quantity,
                average_fill_price, placed_at, filled_at, commission, fees
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, external_order_id) DO UPDATE SET
                client_order_id = EXCLUDED.client_order_id,
                symbol = EXCLUDED.symbol,
                instrument_type = EXCLUDED.instrument_type,
                side = EXCLUDED.side,
                status = EXCLUDED.status,
                order_type = EXCLUDED.order_type,
                total_quantity = EXCLUDED.total_quantity,
                filled_quantity = EXCLUDED.filled_quantity,
                average_fill_price = EXCLUDED.average_fill_price,
                placed_at = EXCLUDED.placed_at,
                filled_at = EXCLUDED.filled_at,
                commission = EXCLUDED.commission,
                fees = EXCLUDED.fees,
                updated_at = NOW()
            """,
            [
                (
                    item.account_id,
                    item.external_order_id,
                    item.client_order_id,
                    item.symbol,
                    item.instrument_type,
                    item.side,
                    item.status,
                    item.order_type,
                    item.total_quantity,
                    item.filled_quantity,
                    item.average_fill_price,
                    item.placed_at,
                    item.filled_at,
                    item.commission,
                    item.fees,
                )
                for item in orders
            ],
        )


class MemoryRepository:
    """Transactional-behavior test repository; never selected by production config."""

    def __init__(self, portfolio_owner_github_id: str = "") -> None:
        self._verification_lock = RLock()
        self._statement_import_lock = RLock()
        self._portfolio_owner_github_id = portfolio_owner_github_id
        self.verification_attempts: list[VerificationAttempt] = []
        self.connected = False
        self.accounts: dict[str, BrokerageAccount] = {}
        self.selected_account_id: str | None = None
        self.connected_at: datetime | None = None
        self.account_last_synced_at: dict[str, datetime] = {}
        self.cash_activity_gaps: dict[str, tuple[datetime, datetime]] = {}
        self.cash_activity_coverage_ranges: dict[str, tuple[datetime, datetime]] = {}
        self.snapshots: dict[str, list[PortfolioState]] = {}
        self.activities: dict[tuple[str, str], CashActivity] = {}
        self.orders: dict[tuple[str, str], OrderRecord] = {}
        self.anchors: dict[tuple[str, str], StatementAnchor] = {}
        self.account_owners: dict[str, str] = {}
        self.account_handles: dict[str, str] = {}
        self.ready_account_ids: set[str] = set()
        self.statement_import_batches: dict[str, tuple[object, ...]] = {}
        self.statement_import_sources: dict[str, tuple[str, str]] = {}
        self.statement_anchor_evidence: dict[
            tuple[str, str], StatementAnchorImport
        ] = {}
        self.statement_external_flows: dict[
            tuple[str, str, int], StatementExternalFlowImport
        ] = {}
        self.failures: list[tuple[str, str]] = []
        self.sync_attempts: list[tuple[str, SyncAttempt]] = []
        self.publication_configs: dict[str, PublicationConfig] = {}
        self.published_details: dict[str, PortfolioDetail] = {}
        self.provider_tokens: dict[str, str] = {}
        self.provider_connection_owners: dict[str, str] = {}
        self.provider_oauth_credentials: dict[str, BrokerOAuthCredential] = {}
        self.discord_sessions: dict[str, DiscordSessionData] = {}
        self.provider_refresh_requests: set[str] = set()

    def begin_verification_attempt(self) -> tuple[VerificationAttempt, bool]:
        now = utc_now()
        with self._verification_lock:
            self._reconcile_expired_verification(now)
            if self.verification_attempts:
                latest = self.verification_attempts[-1]
                if latest.state is VerificationState.RUNNING:
                    return latest, False
            attempt = VerificationAttempt(
                attempt_id=str(uuid4()),
                state=VerificationState.RUNNING,
                stage=VerificationStage.STARTING,
                started_at=now,
                updated_at=now,
                lease_expires_at=now + _VERIFICATION_LEASE,
            )
            self.verification_attempts.append(attempt)
            return attempt, True

    def get_verification_attempt(self) -> VerificationAttempt | None:
        with self._verification_lock:
            self._reconcile_expired_verification(utc_now())
            return (
                self.verification_attempts[-1] if self.verification_attempts else None
            )

    def advance_verification_attempt(
        self, attempt_id: str, stage: VerificationStage
    ) -> VerificationAttempt:
        now = utc_now()
        with self._verification_lock:
            self._reconcile_expired_verification(now)
            attempt = self._verification_by_id(attempt_id)
            if attempt.state is not VerificationState.RUNNING:
                raise RepositoryError("Verification attempt is no longer running.")
            updated = attempt.model_copy(
                update={
                    "stage": stage,
                    "updated_at": now,
                    "lease_expires_at": now + _VERIFICATION_LEASE,
                }
            )
            self._replace_verification_attempt(updated)
            return updated

    def finish_verification_attempt(
        self,
        attempt_id: str,
        state: VerificationState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VerificationAttempt:
        if state not in {VerificationState.SUCCEEDED, VerificationState.FAILED}:
            raise ValueError("A verification attempt must finish succeeded or failed.")
        now = utc_now()
        with self._verification_lock:
            self._reconcile_expired_verification(now)
            attempt = self._verification_by_id(attempt_id)
            if attempt.state is not VerificationState.RUNNING:
                return attempt
            updated = attempt.model_copy(
                update={
                    "state": state,
                    "stage": VerificationStage.COMPLETE
                    if state is VerificationState.SUCCEEDED
                    else attempt.stage,
                    "updated_at": now,
                    "completed_at": now,
                    "error": VerificationError(
                        code=(error_code or "verification_failed")[:64],
                        message=(
                            error_message
                            or "Webull verification could not be completed."
                        )[:500],
                    )
                    if state is VerificationState.FAILED
                    else None,
                }
            )
            self._replace_verification_attempt(updated)
            return updated

    def _reconcile_expired_verification(self, now: datetime) -> None:
        if not self.verification_attempts:
            return
        attempt = self.verification_attempts[-1]
        if (
            attempt.state is VerificationState.RUNNING
            and attempt.lease_expires_at <= now
        ):
            self.verification_attempts[-1] = attempt.model_copy(
                update={
                    "state": VerificationState.TIMED_OUT,
                    "updated_at": now,
                    "completed_at": now,
                    "error": VerificationError(
                        code="verification_timeout",
                        message="Verification timed out before completion.",
                    ),
                }
            )

    def _verification_by_id(self, attempt_id: str) -> VerificationAttempt:
        attempt = next(
            (
                item
                for item in reversed(self.verification_attempts)
                if item.attempt_id == attempt_id
            ),
            None,
        )
        if attempt is None:
            raise RepositoryError("Verification attempt was not found.")
        return attempt

    def _replace_verification_attempt(self, updated: VerificationAttempt) -> None:
        for index, attempt in enumerate(self.verification_attempts):
            if attempt.attempt_id == updated.attempt_id:
                self.verification_attempts[index] = updated
                return
        raise RepositoryError("Verification attempt was not found.")

    def _register_account_scope(self, account: BrokerageAccount) -> None:
        owner = self._portfolio_owner_github_id
        if not owner:
            return
        existing_owner = self.account_owners.get(account.account_id)
        if existing_owner is not None and existing_owner != owner:
            raise RepositoryError("The brokerage account belongs to another owner.")
        self.account_owners[account.account_id] = owner
        self.account_handles.setdefault(
            account.account_id,
            _internal_account_uuid(account.provider.value, account.account_id),
        )
        if account.status.upper() in {"ACTIVE", "READY"}:
            self.ready_account_ids.add(account.account_id)
        else:
            self.ready_account_ids.discard(account.account_id)

    def connect_accounts(self, accounts: list[BrokerageAccount]) -> ConnectionState:
        if not accounts:
            raise RepositoryError("Webull returned no accounts to connect.")
        for account in accounts:
            self._register_account_scope(account)
        self.accounts.update({item.account_id: item for item in accounts})
        self.connected = True
        self.connected_at = self.connected_at or utc_now()
        if self.selected_account_id not in self.accounts:
            self.selected_account_id = accounts[0].account_id
        return self.get_connection_state()

    def get_connection_state(self) -> ConnectionState:
        return ConnectionState(
            connected=self.connected,
            accounts=tuple(
                sorted(self.accounts.values(), key=lambda item: item.account_id)
            ),
            selected_account_id=self.selected_account_id,
            connected_at=self.connected_at,
            last_synced_at=self.account_last_synced_at.get(self.selected_account_id),
        )

    def select_account(self, account_id: str) -> ConnectionState:
        if account_id not in self.accounts:
            raise KeyError(account_id)
        if not self.connected:
            raise RepositoryError("Connect Webull before selecting an account.")
        self.selected_account_id = account_id
        return self.get_connection_state()

    def disconnect(self) -> None:
        self.connected = False
        self.selected_account_id = None
        self.connected_at = None
        self.account_last_synced_at.clear()
        self.cash_activity_gaps.clear()
        self.cash_activity_coverage_ranges.clear()
        self.accounts.clear()
        self.snapshots.clear()
        self.activities.clear()
        self.orders.clear()
        self.anchors.clear()
        self.account_owners.clear()
        self.account_handles.clear()
        self.ready_account_ids.clear()
        self.statement_import_batches.clear()
        self.statement_import_sources.clear()
        self.statement_anchor_evidence.clear()
        self.statement_external_flows.clear()
        self.sync_attempts.clear()
        self.publication_configs.clear()
        self.published_details.clear()

    def commit_sync(
        self,
        account: BrokerageAccount,
        balance: BalanceSnapshot,
        positions: list[PositionSnapshot],
        activities: list[CashActivity],
        *,
        started_at: datetime,
        cash_activities_complete: bool,
        cash_activity_from: datetime,
        cash_activity_until: datetime,
        warning: str | None = None,
    ) -> SyncResult:
        _validate_snapshot(account.account_id, balance, positions, activities)
        self._register_account_scope(account)
        snapshot_id = str(uuid4())
        state = PortfolioState(
            account=account,
            balance=balance,
            positions=tuple(
                {item.external_position_id: item for item in positions}.values()
            ),
            snapshot_id=snapshot_id,
        )
        self.accounts[account.account_id] = account
        self.snapshots.setdefault(account.account_id, []).append(state)
        for activity in activities:
            self.activities[(activity.account_id, activity.external_activity_id)] = (
                activity
            )
        effective_cash_coverage = self._update_cash_activity_coverage(
            account.account_id,
            fetched_from=cash_activity_from,
            fetched_until=cash_activity_until,
            fetch_complete=cash_activities_complete,
        )
        effective_warning = (
            warning
            if warning
            else _INCOMPLETE_CASH_ACTIVITY_COVERAGE_MESSAGE
            if not effective_cash_coverage
            else None
        )
        completed_at = utc_now()
        self.account_last_synced_at[account.account_id] = completed_at
        sync_run_id = str(uuid4())
        self.sync_attempts.append(
            (
                account.account_id,
                SyncAttempt(
                    sync_run_id=sync_run_id,
                    status=SyncAttemptStatus.SUCCESS,
                    started_at=started_at,
                    completed_at=completed_at,
                    cash_activities_complete=effective_cash_coverage,
                    message=effective_warning,
                ),
            )
        )
        return SyncResult(
            sync_run_id=sync_run_id,
            snapshot_id=snapshot_id,
            account_id=account.account_id,
            captured_at=balance.as_of,
            positions_written=len(state.positions),
            cash_activities_seen=len(activities),
            cash_activities_upserted=len(
                {item.external_activity_id for item in activities}
            ),
            cash_activities_complete=effective_cash_coverage,
            warning=effective_warning,
        )

    def record_sync_failure(
        self,
        account_id: str,
        started_at: datetime,
        message: str,
        *,
        cash_activities_complete: bool | None = None,
    ) -> None:
        self.failures.append((account_id, message))
        self.sync_attempts.append(
            (
                account_id,
                SyncAttempt(
                    sync_run_id=str(uuid4()),
                    status=SyncAttemptStatus.ERROR,
                    started_at=started_at,
                    completed_at=utc_now(),
                    cash_activities_complete=cash_activities_complete,
                    message=message[:500],
                ),
            )
        )

    def upsert_history(
        self,
        account: BrokerageAccount,
        activities: list[CashActivity],
        orders: list[OrderRecord],
        *,
        started_at: datetime,
        cash_activities_complete: bool,
        cash_activity_from: datetime,
        cash_activity_until: datetime,
        message: str | None = None,
    ) -> tuple[int, int]:
        self._register_account_scope(account)
        self.accounts[account.account_id] = account
        for item in activities:
            self.activities[(item.account_id, item.external_activity_id)] = item
        for item in orders:
            self.orders[(item.account_id, item.external_order_id)] = item
        effective_cash_coverage = self._update_cash_activity_coverage(
            account.account_id,
            fetched_from=cash_activity_from,
            fetched_until=cash_activity_until,
            fetch_complete=cash_activities_complete,
        )
        effective_message = (
            message
            if message
            else _INCOMPLETE_CASH_ACTIVITY_COVERAGE_MESSAGE
            if not effective_cash_coverage
            else None
        )
        self.sync_attempts.append(
            (
                account.account_id,
                SyncAttempt(
                    sync_run_id=str(uuid4()),
                    status=SyncAttemptStatus.SUCCESS,
                    started_at=started_at,
                    completed_at=utc_now(),
                    cash_activities_complete=effective_cash_coverage,
                    message=effective_message,
                ),
            )
        )
        return len({item.external_activity_id for item in activities}), len(
            {item.external_order_id for item in orders}
        )

    def get_latest_sync_attempt(self, account_id: str) -> SyncAttempt | None:
        return next(
            (
                attempt
                for stored_account, attempt in reversed(self.sync_attempts)
                if stored_account == account_id
            ),
            None,
        )

    def get_latest_cash_activity_coverage(self, account_id: str) -> bool | None:
        if account_id in self.cash_activity_gaps:
            return False
        if account_id not in self.cash_activity_coverage_ranges:
            return None
        return next(
            (
                attempt.cash_activities_complete
                for stored_account, attempt in reversed(self.sync_attempts)
                if stored_account == account_id
                and attempt.cash_activities_complete is not None
            ),
            None,
        )

    def cash_activity_coverage_spans(
        self, account_id: str, *, start: datetime, end: datetime
    ) -> bool:
        if start > end:
            raise RepositoryError("Cash activity coverage range is invalid.")
        if account_id not in self.accounts:
            raise KeyError(account_id)
        coverage = self.cash_activity_coverage_ranges.get(account_id)
        gap = self.cash_activity_gaps.get(account_id)
        intervals = _provider_coverage_intervals(
            coverage[0] if coverage is not None else None,
            coverage[1] if coverage is not None else None,
            gap[0] if gap is not None else None,
            gap[1] if gap is not None else None,
        )
        intervals.extend(
            _statement_coverage_interval(batch[7], batch[8])
            for batch in self.statement_import_batches.values()
            if batch[0] == account_id and batch[11] is True
        )
        return _coverage_intervals_span(intervals, start=start, end=end)

    def _update_cash_activity_coverage(
        self,
        account_id: str,
        *,
        fetched_from: datetime,
        fetched_until: datetime,
        fetch_complete: bool,
    ) -> bool:
        if fetched_from > fetched_until:
            raise RepositoryError("Cash activity coverage range is invalid.")
        gap = self.cash_activity_gaps.get(account_id)
        if fetch_complete:
            coverage = self.cash_activity_coverage_ranges.get(account_id)
            if coverage is None:
                merged = (fetched_from, fetched_until)
            elif fetched_from <= coverage[1] and fetched_until >= coverage[0]:
                merged = (
                    min(coverage[0], fetched_from),
                    max(coverage[1], fetched_until),
                )
            elif fetched_until > coverage[1]:
                merged = (fetched_from, fetched_until)
            else:
                merged = coverage
            self.cash_activity_coverage_ranges[account_id] = merged
            if gap is not None and fetched_from <= gap[0] and fetched_until >= gap[1]:
                del self.cash_activity_gaps[account_id]
                gap = None
            return gap is None

        gap_start, gap_end = gap or (fetched_from, fetched_until)
        self.cash_activity_gaps[account_id] = (
            min(gap_start, fetched_from),
            max(gap_end, fetched_until),
        )
        return False

    def import_statement_anchor(self, anchor: StatementAnchor) -> StatementAnchor:
        if anchor.account_id not in self.accounts:
            raise KeyError(anchor.account_id)
        self.anchors[(anchor.account_id, anchor.external_statement_id)] = anchor
        return anchor

    def commit_statement_import(
        self,
        owner_github_id: str,
        prepared: PreparedStatementImport,
    ) -> StatementImportCommitResult:
        bundle = prepared.bundle
        target_provider = (
            "webull" if bundle.schema_version == WEBULL_SCHEMA_VERSION else "plaid_m1"
        )
        source_hashes = statement_source_content_hashes(bundle)
        with self._statement_import_lock:
            candidates = [
                (account_id, account)
                for account_id, account in self.accounts.items()
                if self.account_handles.get(
                    account_id,
                    _internal_account_uuid(account.provider.value, account_id),
                )
                == bundle.account_handle
                and self.account_owners.get(account_id) == owner_github_id
                and account_id in self.ready_account_ids
                and account.provider.value == target_provider
                and account.currency == "USD"
            ]
            if len(candidates) != 1:
                raise StatementImportError("STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE")
            account_id, _account = candidates[0]

            batch_key = str(bundle.batch_id)
            batch_record: tuple[object, ...] = (
                account_id,
                owner_github_id,
                bundle.account_handle,
                prepared.decrypted_payload_sha256,
                len(bundle.sources),
                len(bundle.anchors),
                len(bundle.external_flows),
                bundle.validation.coverage.start,
                bundle.validation.coverage.end,
                bundle.validation.coverage.contiguous_monthly_coverage,
                bundle.validation.coverage.external_flow_coverage_complete,
                statement_publication_eligible(bundle),
            )
            existing_batch = self.statement_import_batches.get(batch_key)
            if existing_batch is not None:
                if existing_batch != batch_record:
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
                return self._memory_statement_commit_result(
                    account_id,
                    bundle,
                    StatementImportDisposition.ALREADY_IMPORTED,
                )

            for source in bundle.sources:
                existing_source = self.statement_import_sources.get(source.source_id)
                expected_source = (account_id, source_hashes[source.source_id])
                if existing_source is not None and existing_source != expected_source:
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")

            expected_anchors: dict[tuple[str, str], StatementAnchor] = {}
            for anchor in bundle.anchors:
                key = (account_id, anchor.source_id)
                expected = StatementAnchor(
                    account_id=account_id,
                    external_statement_id=anchor.source_id,
                    statement_date=_statement_market_close(anchor.date),
                    ending_equity=anchor.net_liquidation_value,
                    currency=anchor.currency,
                    ending_cash=anchor.signed_cash_balance,
                    source_sha256=anchor.source_id.removeprefix("sha256:"),
                )
                existing_anchor = self.anchors.get(key)
                existing_evidence = self.statement_anchor_evidence.get(key)
                if existing_anchor is not None and (
                    existing_anchor != expected or existing_evidence != anchor
                ):
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
                expected_anchors[key] = expected

            expected_flows: dict[tuple[str, str, int], StatementExternalFlowImport] = {}
            for flow in bundle.external_flows:
                key = (account_id, flow.source_id, flow.sequence)
                existing_flow = self.statement_external_flows.get(key)
                if existing_flow is not None and existing_flow != flow:
                    raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
                expected_flows[key] = flow

            # Copy/swap keeps every observable collection at its preceding state
            # until all collision checks and normalized conversions have passed.
            batches = self.statement_import_batches.copy()
            sources = self.statement_import_sources.copy()
            anchors = self.anchors.copy()
            anchor_evidence = self.statement_anchor_evidence.copy()
            flows = self.statement_external_flows.copy()
            batches[batch_key] = batch_record
            for source in bundle.sources:
                sources[source.source_id] = (
                    account_id,
                    source_hashes[source.source_id],
                )
            anchors.update(expected_anchors)
            anchor_evidence.update(
                {(account_id, anchor.source_id): anchor for anchor in bundle.anchors}
            )
            flows.update(expected_flows)

            self.statement_import_batches = batches
            self.statement_import_sources = sources
            self.anchors = anchors
            self.statement_anchor_evidence = anchor_evidence
            self.statement_external_flows = flows
            return self._memory_statement_commit_result(
                account_id,
                bundle,
                StatementImportDisposition.IMPORTED,
            )

    def _memory_statement_commit_result(
        self,
        account_id: str,
        bundle: StatementBackfillBundleV2,
        disposition: StatementImportDisposition,
    ) -> StatementImportCommitResult:
        persisted_anchors = [
            self.statement_anchor_evidence.get((account_id, anchor.source_id))
            for anchor in bundle.anchors
        ]
        persisted_flows = [
            self.statement_external_flows.get(
                (account_id, flow.source_id, flow.sequence)
            )
            for flow in bundle.external_flows
        ]
        persisted_sources = [
            self.statement_import_sources.get(source.source_id)
            for source in bundle.sources
        ]
        complete = (
            all(item is not None for item in persisted_sources)
            and all(item is not None for item in persisted_anchors)
            and all(item is not None for item in persisted_flows)
        )
        return StatementImportCommitResult(
            disposition=disposition,
            coverage_start=min(source.statement_start for source in bundle.sources),
            coverage_end=max(anchor.date for anchor in bundle.anchors),
            publication_eligible=(complete and statement_publication_eligible(bundle)),
        )

    def get_portfolio(self, account_id: str) -> PortfolioState | None:
        if account_id not in self.accounts:
            raise KeyError(account_id)
        values = self.snapshots.get(account_id, [])
        return values[-1] if values else None

    def get_performance_inputs(
        self,
        account_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[ValuationPoint], list[CashActivity]]:
        valuations = [
            ValuationPoint(
                at=state.balance.as_of,
                value=state.balance.equity,
                source=f"{state.account.provider.value}_snapshot",
            )
            for state in self.snapshots.get(account_id, [])
            if state.balance.as_of > _UNKNOWN_AS_OF
        ]
        valuations.extend(
            ValuationPoint(
                at=anchor.statement_date,
                value=anchor.ending_equity,
                source="statement_anchor",
            )
            for (stored_account, _), anchor in self.anchors.items()
            if stored_account == account_id
        )
        valuations = [
            item
            for item in valuations
            if (start is None or item.at >= start) and (end is None or item.at <= end)
        ]
        activities = [
            item
            for (stored_account, _), item in self.activities.items()
            if stored_account == account_id and item.is_external_flow
        ]
        provider_matches = Counter(
            (
                item.occurred_at.astimezone(_MARKET_TIME_ZONE).date(),
                _external_flow_match_kind(item.activity_type),
                item.amount,
                item.currency,
            )
            for item in activities
        )
        statement_matches: Counter[tuple[object, ...]] = Counter()
        for (stored_account, source_id, sequence), flow in sorted(
            self.statement_external_flows.items()
        ):
            if (
                stored_account != account_id
                or flow.valuation_status != "reported"
                or flow.amount is None
            ):
                continue
            match_key = (
                flow.date,
                _external_flow_match_kind(flow.kind),
                flow.amount,
                flow.currency,
            )
            statement_matches[match_key] += 1
            if statement_matches[match_key] <= provider_matches[match_key]:
                continue
            activities.append(
                CashActivity(
                    account_id=account_id,
                    external_activity_id=f"statement:{source_id}:{sequence}",
                    activity_type=_statement_activity_type(flow.kind),
                    occurred_at=(
                        flow.occurred_at
                        if flow.occurred_at is not None
                        else _statement_market_close(flow.date)
                    ),
                    amount=flow.amount,
                    currency=flow.currency,
                    status="POSTED",
                    description="Statement-reconciled external flow",
                    is_external_flow=True,
                )
            )
        activities = [
            item
            for item in activities
            if (start is None or item.occurred_at >= start)
            and (end is None or item.occurred_at <= end)
        ]
        return sorted(valuations, key=lambda item: item.at), sorted(
            activities, key=lambda item: item.occurred_at
        )

    def get_recent_activities(
        self, account_id: str, *, limit: int = 100
    ) -> list[CashActivity]:
        values = [
            item
            for (stored_account, _), item in self.activities.items()
            if stored_account == account_id
        ]
        return sorted(values, key=lambda item: item.occurred_at, reverse=True)[:limit]

    def get_recent_orders(
        self, account_id: str, *, limit: int = 100
    ) -> list[OrderRecord]:
        values = [
            item
            for (stored_account, _), item in self.orders.items()
            if stored_account == account_id
        ]
        return sorted(
            values,
            key=lambda item: (
                item.filled_at
                or item.placed_at
                or datetime.min.replace(tzinfo=utc_now().tzinfo)
            ),
            reverse=True,
        )[:limit]

    def configure_publication(
        self, request: PublicationConfigurationRequest
    ) -> PublicationConfig:
        account_id = request.account_id
        if request.account_handle:
            account_id = next(
                (
                    stored_id
                    for stored_id, candidate in self.accounts.items()
                    if self.account_handles.get(
                        stored_id,
                        _internal_account_uuid(candidate.provider.value, stored_id),
                    )
                    == request.account_handle
                    and (
                        not self._portfolio_owner_github_id
                        or self.account_owners.get(stored_id)
                        == self._portfolio_owner_github_id
                    )
                ),
                None,
            )
        account = self.accounts.get(account_id or "")
        if account is None:
            raise KeyError(request.account_handle or request.account_id)
        title = validate_publication_title(request.title, account.account_id)
        existing = next(
            (
                item
                for item in self.publication_configs.values()
                if item.account_id == account_id
            ),
            None,
        )
        if any(
            item.slug == request.slug
            and (existing is None or item.publication_id != existing.publication_id)
            for item in self.publication_configs.values()
        ):
            raise RepositoryError("Publication slug is already in use.")
        config = PublicationConfig(
            publication_id=existing.publication_id if existing else str(uuid4()),
            account_handle=self.account_handles.get(
                account_id or "",
                _internal_account_uuid(account.provider.value, account_id or ""),
            ),
            account_id=account_id or "",
            provider=account.provider,
            slug=request.slug,
            title=title,
            benchmark_symbol=request.benchmark_symbol,
            enabled=request.enabled,
            has_published_revision=(
                existing.has_published_revision if existing else False
            ),
            updated_at=utc_now(),
        )
        if existing and existing.slug != config.slug:
            self.published_details.pop(existing.slug, None)
        self.publication_configs[config.publication_id] = config
        return config

    def save_provider_access_token(
        self,
        provider: str,
        *,
        item_id: str,
        institution_id: str | None,
        access_token: str,
        encryption_key: str,
        owner_github_id: str | None = None,
    ) -> None:
        del item_id, institution_id
        if not access_token or not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        owner = owner_github_id or self._portfolio_owner_github_id
        if owner:
            existing_owner = self.provider_connection_owners.get(provider)
            if existing_owner is not None and existing_owner != owner:
                raise RepositoryError(
                    "The provider connection belongs to another owner."
                )
            self.provider_connection_owners[provider] = owner
        self.provider_tokens[provider] = access_token

    def has_provider_connection(self, provider: str) -> bool:
        return (
            provider in self.provider_tokens
            or provider in self.provider_oauth_credentials
        )

    def get_provider_access_token(
        self, provider: str, *, encryption_key: str
    ) -> str | None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        return self.provider_tokens.get(provider)

    def save_provider_oauth_credential(
        self,
        provider: str,
        *,
        connection_key: str,
        institution_id: str | None,
        credential: BrokerOAuthCredential,
        encryption_key: str,
    ) -> None:
        del connection_key, institution_id
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        if credential.provider.value != provider:
            raise RepositoryError("The OAuth credential provider does not match.")
        self.provider_oauth_credentials[provider] = credential

    def get_provider_oauth_credential(
        self, provider: str, *, encryption_key: str
    ) -> BrokerOAuthCredential | None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Broker credential encryption is not configured."
            )
        return self.provider_oauth_credentials.get(provider)

    def list_provider_account_references(
        self, provider: str
    ) -> tuple[BrokerageAccountReference, ...]:
        return tuple(
            BrokerageAccountReference(
                account_handle=self.account_handles.get(
                    account_id,
                    _internal_account_uuid(provider, account_id),
                ),
                provider=account.provider,
                account_type=account.account_type,
                currency=account.currency,
                last_synced_at=self.account_last_synced_at.get(account_id),
            )
            for account_id, account in sorted(self.accounts.items())
            if account.provider.value == provider
            and (
                not self._portfolio_owner_github_id
                or self.account_owners.get(account_id)
                == self._portfolio_owner_github_id
            )
        )

    def write_discord_session(
        self,
        session_id: str,
        session: DiscordSessionData,
        *,
        encryption_key: str,
    ) -> None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Discord session encryption is not configured."
            )
        self.discord_sessions[_discord_session_hash(session_id)] = session

    def get_discord_session(
        self, session_id: str, *, encryption_key: str
    ) -> DiscordSessionData | None:
        if not encryption_key:
            raise RepositoryConfigurationError(
                "Discord session encryption is not configured."
            )
        session = self.discord_sessions.get(_discord_session_hash(session_id))
        if session is None or session.expires_at <= utc_now():
            return None
        return session

    def delete_discord_session(self, session_id: str) -> None:
        self.discord_sessions.pop(_discord_session_hash(session_id), None)

    def request_provider_refresh(self, provider: str) -> None:
        if provider not in {"plaid_m1", "schwab"}:
            raise RepositoryError("This provider does not support refresh requests.")
        self.provider_refresh_requests.add(provider)

    def complete_provider_refresh(self, provider: str) -> None:
        self.provider_refresh_requests.discard(provider)

    def get_publication_config(self, publication_id: str) -> PublicationConfig:
        try:
            return self.publication_configs[publication_id]
        except KeyError:
            raise KeyError(publication_id) from None

    def list_managed_publications(self) -> ManagedPublicationList:
        return ManagedPublicationList(
            publications=tuple(
                sorted(
                    self.publication_configs.values(),
                    key=lambda item: (item.title, item.slug),
                )
            )
        )

    def set_publication_enabled(
        self, publication_id: str, *, enabled: bool
    ) -> PublicationConfig:
        config = self.get_publication_config(publication_id)
        updated = config.model_copy(
            update={"enabled": enabled, "updated_at": utc_now()}
        )
        self.publication_configs[publication_id] = updated
        return updated

    def commit_publication(
        self,
        publication_id: str,
        detail: PortfolioDetail,
        *,
        source_snapshot_id: str,
    ) -> PortfolioDetail:
        config = self.get_publication_config(publication_id)
        if not config.enabled:
            raise RepositoryError("Enable the portfolio before publishing it.")
        snapshots = self.snapshots.get(config.account_id, [])
        if not any(item.snapshot_id == source_snapshot_id for item in snapshots):
            raise RepositoryError(
                "The source snapshot does not belong to this portfolio."
            )
        if (
            config.slug != detail.card.slug
            or config.title != detail.card.title
            or config.provider is not detail.card.provider
        ):
            raise RepositoryError(
                "Publication detail does not match its configuration."
            )
        existing_detail = self.published_details.get(config.slug)
        if (
            existing_detail is not None
            and existing_detail.holdings_as_of.astimezone(_MARKET_TIME_ZONE).date()
            == detail.holdings_as_of.astimezone(_MARKET_TIME_ZONE).date()
        ):
            return existing_detail
        # Assignment is the commit point; validation failures before it preserve
        # the previous detail exactly.
        self.published_details[config.slug] = detail
        self.publication_configs[publication_id] = config.model_copy(
            update={"has_published_revision": True, "updated_at": utc_now()}
        )
        return detail

    def list_published_portfolios(self) -> PublishedPortfolioList:
        cards = [
            self.published_details[config.slug].card
            for config in self.publication_configs.values()
            if config.enabled and config.slug in self.published_details
        ]
        return PublishedPortfolioList(
            portfolios=tuple(sorted(cards, key=lambda item: (item.title, item.slug)))
        )

    def get_published_portfolio(self, slug: str) -> PortfolioDetail:
        enabled = any(
            item.slug == slug and item.enabled
            for item in self.publication_configs.values()
        )
        if not enabled or slug not in self.published_details:
            raise KeyError(slug)
        return self.published_details[slug]


def _verification_from_row(row: dict) -> VerificationAttempt:
    error_code = row.get("error_code")
    error_message = row.get("error_message")
    error = (
        VerificationError(
            code=str(error_code or "verification_failed")[:64],
            message=str(error_message or "Webull verification could not be completed.")[
                :500
            ],
        )
        if error_code or error_message
        else None
    )
    return VerificationAttempt(
        attempt_id=str(row["attempt_id"]),
        state=VerificationState[str(row["state"]).upper()],
        stage=VerificationStage[str(row["stage"]).upper()],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        lease_expires_at=row["lease_expires_at"],
        completed_at=row.get("completed_at"),
        error=error,
    )


def _sync_attempt_from_row(row: dict) -> SyncAttempt:
    return SyncAttempt(
        sync_run_id=str(row["sync_run_id"]),
        status=SyncAttemptStatus[str(row["status"]).upper()],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        cash_activities_complete=row.get("cash_activities_complete"),
        message=str(row["message"])[:500] if row.get("message") else None,
    )


def _validate_snapshot(
    account_id: str,
    balance: BalanceSnapshot,
    positions: list[PositionSnapshot],
    activities: list[CashActivity],
) -> None:
    if balance.account_id != account_id:
        raise RepositoryError("Balance account does not match the sync account.")
    if any(item.account_id != account_id for item in positions):
        raise RepositoryError("Position account does not match the sync account.")
    if any(item.account_id != account_id for item in activities):
        raise RepositoryError("Cash activity account does not match the sync account.")


def _balance_from_row(row: dict) -> BalanceSnapshot:
    return BalanceSnapshot(
        account_id=row["account_id"],
        as_of=row["captured_at"],
        currency=row["currency"],
        equity=row["equity"],
        cash=row["cash"],
        market_value=row["market_value"],
        buying_power=row["buying_power"],
        day_profit_loss=row["day_profit_loss"],
        unrealized_profit_loss=row["unrealized_profit_loss"],
        cash_source=row.get("cash_source", "explicit"),
    )


def _position_from_row(row: dict) -> PositionSnapshot:
    return PositionSnapshot(**{key: row[key] for key in PositionSnapshot.model_fields})


def _activity_from_row(row: dict) -> CashActivity:
    return CashActivity(**{key: row[key] for key in CashActivity.model_fields})


def _order_from_row(row: dict) -> OrderRecord:
    return OrderRecord(**{key: row[key] for key in OrderRecord.model_fields})


def _statement_coverage_interval(
    coverage_start: date,
    coverage_end: date,
) -> tuple[datetime, datetime]:
    start = datetime.combine(
        coverage_start,
        time.min,
        _MARKET_TIME_ZONE,
    ).astimezone(UTC)
    end = datetime.combine(
        coverage_end + timedelta(days=1),
        time.min,
        _MARKET_TIME_ZONE,
    ).astimezone(UTC)
    return start, end


def _provider_coverage_intervals(
    coverage_start: datetime | None,
    coverage_end: datetime | None,
    gap_start: datetime | None,
    gap_end: datetime | None,
) -> list[tuple[datetime, datetime]]:
    if coverage_start is None or coverage_end is None:
        return []
    if gap_start is None and gap_end is None:
        return [(coverage_start, coverage_end)]
    if gap_start is None or gap_end is None:
        return []
    intervals: list[tuple[datetime, datetime]] = []
    if coverage_start < gap_start:
        intervals.append((coverage_start, gap_start - timedelta(microseconds=1)))
    if gap_end < coverage_end:
        intervals.append((gap_end + timedelta(microseconds=1), coverage_end))
    return intervals


def _coverage_intervals_span(
    intervals: list[tuple[datetime, datetime]],
    *,
    start: datetime,
    end: datetime,
) -> bool:
    merged_end: datetime | None = None
    for interval_start, interval_end in sorted(intervals):
        if interval_end < interval_start:
            continue
        if merged_end is None or interval_start > merged_end:
            if interval_start > start:
                continue
            merged_end = interval_end
        else:
            merged_end = max(merged_end, interval_end)
        if merged_end >= end:
            return True
    return False


def _statement_market_close(value: date) -> datetime:
    return datetime.combine(value, time(16), _MARKET_TIME_ZONE).astimezone(UTC)


def _external_flow_match_kind(activity_type: str) -> str:
    normalized = activity_type.strip().lower()
    if normalized == "deposit":
        return "deposit"
    if normalized == "withdrawal":
        return "withdrawal"
    return "transfer"


def _statement_activity_type(kind: str) -> str:
    normalized = _external_flow_match_kind(kind)
    return normalized.upper()


def _postgres_statement_commit_result(
    cursor: psycopg.Cursor,
    *,
    bundle: StatementBackfillBundleV2,
    internal_account_id: str,
    disposition: StatementImportDisposition,
) -> StatementImportCommitResult:
    batch_id = str(bundle.batch_id)
    cursor.execute(
        """
        SELECT batch.publication_eligible,
               (
                   SELECT COUNT(*)
                   FROM statement_import_batch_sources batch_source
                   WHERE batch_source.batch_id = batch.batch_id
               ) AS source_count,
               (
                   SELECT MIN(source.statement_start)
                   FROM statement_import_batch_sources batch_source
                   JOIN statement_import_sources source
                     ON source.source_id = batch_source.source_id
                   WHERE batch_source.batch_id = batch.batch_id
               ) AS coverage_start,
               (
                   SELECT COUNT(*)
                   FROM statement_import_batch_sources batch_source
                   JOIN statement_anchors anchor
                     ON anchor.statement_source_id = batch_source.source_id
                    AND anchor.internal_account_id = %s
                   WHERE batch_source.batch_id = batch.batch_id
               ) AS anchor_count,
               (
                   SELECT MAX(
                       (anchor.statement_date AT TIME ZONE 'America/New_York')::DATE
                   )
                   FROM statement_import_batch_sources batch_source
                   JOIN statement_anchors anchor
                     ON anchor.statement_source_id = batch_source.source_id
                    AND anchor.internal_account_id = %s
                   WHERE batch_source.batch_id = batch.batch_id
               ) AS coverage_end,
               (
                   SELECT COUNT(*)
                   FROM statement_import_batch_sources batch_source
                   JOIN statement_external_flows flow
                     ON flow.source_id = batch_source.source_id
                    AND flow.internal_account_id = %s
                   WHERE batch_source.batch_id = batch.batch_id
               ) AS external_flow_count,
               (
                   SELECT COUNT(*)
                   FROM statement_import_batch_sources batch_source
                   JOIN statement_external_flows flow
                     ON flow.source_id = batch_source.source_id
                    AND flow.internal_account_id = %s
                   WHERE batch_source.batch_id = batch.batch_id
                     AND (flow.valuation_status <> 'reported' OR flow.amount IS NULL)
               ) AS unvalued_flow_count
        FROM statement_import_batches batch
        WHERE batch.batch_id = %s
          AND batch.internal_account_id = %s
        """,
        (
            internal_account_id,
            internal_account_id,
            internal_account_id,
            internal_account_id,
            batch_id,
            internal_account_id,
        ),
    )
    row = cursor.fetchone()
    if (
        row is None
        or row["coverage_start"] is None
        or row["coverage_end"] is None
        or row["source_count"] != len(bundle.sources)
        or row["anchor_count"] != len(bundle.anchors)
        or row["external_flow_count"] != len(bundle.external_flows)
    ):
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
    return StatementImportCommitResult(
        disposition=disposition,
        coverage_start=row["coverage_start"],
        coverage_end=row["coverage_end"],
        publication_eligible=(
            bool(row["publication_eligible"])
            and row["unvalued_flow_count"] == 0
            and statement_publication_eligible(bundle)
        ),
    )


def _statement_batch_matches(
    row: dict,
    *,
    internal_account_id: str,
    owner_github_id: str,
    account_handle: str,
    payload_sha256: str,
    source_document_count: int,
    anchor_count: int,
    external_flow_count: int,
    coverage_start: date,
    coverage_end: date,
    contiguous_monthly_coverage: bool,
    external_flow_coverage_complete: bool,
    publication_eligible: bool,
) -> bool:
    return (
        str(row["internal_account_id"]) == internal_account_id
        and row["owner_github_id"] == owner_github_id
        and row["account_handle"] == account_handle
        and str(row["payload_sha256"]).strip() == payload_sha256
        and row["source_document_count"] == source_document_count
        and row["anchor_count"] == anchor_count
        and row["external_flow_count"] == external_flow_count
        and row["coverage_start"] == coverage_start
        and row["coverage_end"] == coverage_end
        and bool(row["contiguous_monthly_coverage"]) is contiguous_monthly_coverage
        and bool(row["external_flow_coverage_complete"])
        is external_flow_coverage_complete
        and bool(row["publication_eligible"]) is publication_eligible
    )


def _statement_anchor_matches(
    row: dict,
    *,
    internal_account_id: str,
    source_id: str,
    statement_at: datetime,
    net_liquidation_value: Decimal,
    currency: str,
    cash_balance: Decimal | None,
    margin_debit_balance: Decimal | None,
    signed_cash_balance: Decimal | None,
    quality: str,
) -> bool:
    return (
        row["statement_date"].astimezone(UTC) == statement_at
        and row["ending_equity"] == net_liquidation_value
        and row["currency"] == currency
        and row["ending_cash"] == signed_cash_balance
        and str(row["source_sha256"]).strip() == source_id.removeprefix("sha256:")
        and str(row["internal_account_id"]) == internal_account_id
        and row["statement_source_id"] == source_id
        and row["cash_balance"] == cash_balance
        and row["margin_debit_balance"] == margin_debit_balance
        and row["signed_cash_balance"] == signed_cash_balance
        and row["quality"] == quality
    )


def _statement_flow_matches(
    row: dict,
    *,
    flow_date: date,
    flow_at: datetime | None,
    kind: str,
    amount: Decimal | None,
    currency: str,
    valuation_status: str,
    evidence: str,
) -> bool:
    return (
        row["flow_date"] == flow_date
        and (row["flow_at"].astimezone(UTC) if row["flow_at"] is not None else None)
        == flow_at
        and row["kind"] == kind
        and row["amount"] == amount
        and row["currency"] == currency
        and row["valuation_status"] == valuation_status
        and row["evidence"] == evidence
    )


def _internal_account_uuid(provider: str, account_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"portfolio-lab:{provider}:{account_id}"))


def _provider_account_id(provider: str, account_id: str) -> str:
    prefix = f"{provider}:"
    return account_id.removeprefix(prefix)


def _discord_session_hash(session_id: str) -> str:
    if not session_id or len(session_id) > 128:
        raise RepositoryError("Discord session identifier is invalid.")
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _publication_config_from_row(row: dict) -> PublicationConfig:
    return PublicationConfig(
        publication_id=str(row["publication_id"]),
        account_handle=str(row["internal_account_id"]),
        account_id=str(row["account_id"]),
        provider=row["provider"],
        slug=row["slug"],
        title=row["title"],
        benchmark_symbol=row["benchmark_symbol"],
        enabled=bool(row["enabled"]),
        has_published_revision=row.get("active_revision_id") is not None,
        updated_at=row["updated_at"],
    )


def _portfolio_detail_from_rows(
    row: dict,
    holdings: tuple[PublishedHolding, ...],
    points: tuple[PublishedPerformancePoint, ...],
) -> PortfolioDetail:
    validate_public_analytics(row["analytics"])
    return PortfolioDetail(
        card=PortfolioCard(
            slug=row["slug"],
            title=row["title"],
            provider=row["provider"],
            ytd_return_percent=row["ytd_return_percent"],
            performance_through=row["performance_through"],
            quality=row["quality"],
            performance_points=points,
        ),
        holdings_as_of=row["holdings_as_of"],
        holdings=holdings,
        gross_exposure_percent=row["gross_exposure_percent"],
        net_exposure_percent=row["net_exposure_percent"],
        analytical_sleeve_percent=row["analytical_sleeve_percent"],
        benchmark_symbol=row["benchmark_symbol"],
        analytics=row["analytics"],
    )
