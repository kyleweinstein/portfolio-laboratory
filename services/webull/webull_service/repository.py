from __future__ import annotations

from datetime import datetime, timedelta
from threading import RLock
from typing import Protocol
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .models import (
    BalanceSnapshot,
    BrokerageAccount,
    CashActivity,
    ConnectionState,
    OrderRecord,
    PortfolioState,
    PositionSnapshot,
    StatementAnchor,
    SyncResult,
    ValuationPoint,
    VerificationAttempt,
    VerificationError,
    VerificationStage,
    VerificationState,
    utc_now,
)

_VERIFICATION_LEASE = timedelta(minutes=7)


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
    ) -> SyncResult: ...
    def record_sync_failure(
        self, account_id: str, started_at: datetime, message: str
    ) -> None: ...
    def upsert_history(
        self,
        account: BrokerageAccount,
        activities: list[CashActivity],
        orders: list[OrderRecord],
    ) -> tuple[int, int]: ...
    def import_statement_anchor(self, anchor: StatementAnchor) -> StatementAnchor: ...
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


class PostgresRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

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
                "SELECT account_id, account_type, status, currency FROM brokerage_accounts ORDER BY account_id"
            )
            accounts = tuple(BrokerageAccount(**row) for row in cursor.fetchall())
            cursor.execute(
                "SELECT MAX(last_synced_at) AS last_synced_at FROM brokerage_accounts"
            )
            last_synced = (cursor.fetchone() or {}).get("last_synced_at")
        return ConnectionState(
            connected=bool(state["connected"]),
            accounts=accounts,
            selected_account_id=state["selected_account_id"],
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
            cursor.execute(
                """
                    INSERT INTO portfolio_snapshots (
                        snapshot_id, account_id, captured_at, currency, equity, cash,
                        market_value, buying_power, day_profit_loss, unrealized_profit_loss
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                ),
            )
            if positions_by_id:
                cursor.executemany(
                    """
                        INSERT INTO position_snapshots (
                            snapshot_id, external_position_id, account_id, symbol,
                            instrument_type, currency, quantity, last_price, market_value,
                            average_cost, cost_basis, unrealized_profit_loss
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        )
                        for item in positions_by_id.values()
                    ],
                )
            self._upsert_activities(cursor, list(activities_by_id.values()))
            cursor.execute(
                """
                    INSERT INTO sync_runs (
                        sync_run_id, account_id, status, started_at, completed_at, snapshot_id
                    ) VALUES (%s, %s, 'SUCCESS', %s, %s, %s)
                    """,
                (
                    sync_run_id,
                    account.account_id,
                    started_at,
                    completed_at,
                    snapshot_id,
                ),
            )
            cursor.execute(
                "UPDATE brokerage_accounts SET last_synced_at = %s, updated_at = %s WHERE account_id = %s",
                (balance.as_of, completed_at, account.account_id),
            )
        return SyncResult(
            sync_run_id=sync_run_id,
            snapshot_id=snapshot_id,
            account_id=account.account_id,
            captured_at=balance.as_of,
            positions_written=len(positions_by_id),
            cash_activities_seen=len(activities),
            cash_activities_upserted=len(activities_by_id),
        )

    def record_sync_failure(
        self, account_id: str, started_at: datetime, message: str
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO brokerage_accounts (account_id, account_type, status, currency)
                VALUES (%s, 'UNKNOWN', 'UNKNOWN', 'USD')
                ON CONFLICT (account_id) DO NOTHING
                """,
                (account_id,),
            )
            cursor.execute(
                """
                INSERT INTO sync_runs (
                    sync_run_id, account_id, status, started_at, completed_at, error_message
                ) VALUES (%s, %s, 'ERROR', %s, %s, %s)
                """,
                (str(uuid4()), account_id, started_at, utc_now(), message[:500]),
            )

    def upsert_history(
        self,
        account: BrokerageAccount,
        activities: list[CashActivity],
        orders: list[OrderRecord],
    ) -> tuple[int, int]:
        activities_by_id = {item.external_activity_id: item for item in activities}
        orders_by_id = {item.external_order_id: item for item in orders}
        with (
            self._connect() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            self._upsert_account(cursor, account)
            self._upsert_activities(cursor, list(activities_by_id.values()))
            self._upsert_orders(cursor, list(orders_by_id.values()))
        return len(activities_by_id), len(orders_by_id)

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

    def get_portfolio(self, account_id: str) -> PortfolioState | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT account_id, account_type, status, currency FROM brokerage_accounts WHERE account_id = %s",
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
                    SELECT account_id, captured_at AS valuation_at, equity AS value, created_at,
                           'webull_snapshot' AS source
                    FROM portfolio_snapshots
                    WHERE (captured_at AT TIME ZONE 'America/New_York')::time >= TIME '16:00'
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
                parameters,
            )
            valuations = [
                ValuationPoint(
                    at=row["valuation_at"], value=row["value"], source=row["source"]
                )
                for row in cursor.fetchall()
            ]

            activity_filters = ["account_id = %s", "is_external_flow = TRUE"]
            activity_parameters: list[object] = [account_id]
            if start is not None:
                activity_filters.append("occurred_at >= %s")
                activity_parameters.append(start)
            if end is not None:
                activity_filters.append("occurred_at <= %s")
                activity_parameters.append(end)
            cursor.execute(
                f"SELECT * FROM cash_activities WHERE {' AND '.join(activity_filters)} ORDER BY occurred_at",
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

    @staticmethod
    def _upsert_account(cursor: psycopg.Cursor, account: BrokerageAccount) -> None:
        cursor.execute(
            """
            INSERT INTO brokerage_accounts (account_id, account_type, status, currency)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (account_id) DO UPDATE SET
                account_type = EXCLUDED.account_type,
                status = EXCLUDED.status,
                currency = EXCLUDED.currency,
                updated_at = NOW()
            """,
            (
                account.account_id,
                account.account_type,
                account.status,
                account.currency,
            ),
        )

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

    def __init__(self) -> None:
        self._verification_lock = RLock()
        self.verification_attempts: list[VerificationAttempt] = []
        self.connected = False
        self.accounts: dict[str, BrokerageAccount] = {}
        self.selected_account_id: str | None = None
        self.connected_at: datetime | None = None
        self.last_synced_at: datetime | None = None
        self.snapshots: dict[str, list[PortfolioState]] = {}
        self.activities: dict[tuple[str, str], CashActivity] = {}
        self.orders: dict[tuple[str, str], OrderRecord] = {}
        self.anchors: dict[tuple[str, str], StatementAnchor] = {}
        self.failures: list[tuple[str, str]] = []

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

    def connect_accounts(self, accounts: list[BrokerageAccount]) -> ConnectionState:
        if not accounts:
            raise RepositoryError("Webull returned no accounts to connect.")
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
            last_synced_at=self.last_synced_at,
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
        self.last_synced_at = None
        self.accounts.clear()
        self.snapshots.clear()
        self.activities.clear()
        self.orders.clear()
        self.anchors.clear()

    def commit_sync(
        self,
        account: BrokerageAccount,
        balance: BalanceSnapshot,
        positions: list[PositionSnapshot],
        activities: list[CashActivity],
        *,
        started_at: datetime,
    ) -> SyncResult:
        _validate_snapshot(account.account_id, balance, positions, activities)
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
        self.last_synced_at = balance.as_of
        return SyncResult(
            sync_run_id=str(uuid4()),
            snapshot_id=snapshot_id,
            account_id=account.account_id,
            captured_at=balance.as_of,
            positions_written=len(state.positions),
            cash_activities_seen=len(activities),
            cash_activities_upserted=len(
                {item.external_activity_id for item in activities}
            ),
        )

    def record_sync_failure(
        self, account_id: str, started_at: datetime, message: str
    ) -> None:
        self.failures.append((account_id, message))

    def upsert_history(
        self,
        account: BrokerageAccount,
        activities: list[CashActivity],
        orders: list[OrderRecord],
    ) -> tuple[int, int]:
        self.accounts[account.account_id] = account
        for item in activities:
            self.activities[(item.account_id, item.external_activity_id)] = item
        for item in orders:
            self.orders[(item.account_id, item.external_order_id)] = item
        return len({item.external_activity_id for item in activities}), len(
            {item.external_order_id for item in orders}
        )

    def import_statement_anchor(self, anchor: StatementAnchor) -> StatementAnchor:
        if anchor.account_id not in self.accounts:
            raise KeyError(anchor.account_id)
        self.anchors[(anchor.account_id, anchor.external_statement_id)] = anchor
        return anchor

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
                source="webull_snapshot",
            )
            for state in self.snapshots.get(account_id, [])
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
            if stored_account == account_id
            and item.is_external_flow
            and (start is None or item.occurred_at >= start)
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
    )


def _position_from_row(row: dict) -> PositionSnapshot:
    return PositionSnapshot(**{key: row[key] for key in PositionSnapshot.model_fields})


def _activity_from_row(row: dict) -> CashActivity:
    return CashActivity(**{key: row[key] for key in CashActivity.model_fields})


def _order_from_row(row: dict) -> OrderRecord:
    return OrderRecord(**{key: row[key] for key in OrderRecord.model_fields})
