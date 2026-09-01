from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from hashlib import sha256

import psycopg
from psycopg.rows import dict_row


class ReconciliationAuditError(RuntimeError):
    """A deliberately generic private audit failure."""


def reconciliation_audit(
    database_url: str,
    expected_owner_github_id: str,
    expected_account_handle_sha256: str = "",
) -> tuple[dict[str, object], ...]:
    if not database_url:
        raise ReconciliationAuditError("configuration")
    try:
        with (
            psycopg.connect(database_url, row_factory=dict_row) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT account.account_type,
                       account.status,
                       account.currency,
                       account.account_handle,
                       connection.provider AS connection_provider,
                       connection.status AS connection_status,
                       account.account_handle = account.internal_account_id::TEXT
                           AS canonical_account_handle,
                       account.cash_activity_coverage_start,
                       account.cash_activity_coverage_end,
                       account.cash_activity_gap_start,
                       account.cash_activity_gap_end,
                       account.owner_github_id AS account_owner_github_id,
                       connection.owner_github_id AS connection_owner_github_id,
                       account.account_id = state.selected_account_id AS selected,
                       COUNT(DISTINCT batch.batch_id) AS statement_batches,
                       COUNT(DISTINCT batch.batch_id) FILTER (
                           WHERE batch.publication_eligible = TRUE
                       ) AS eligible_batches,
                       COUNT(DISTINCT anchor.external_statement_id) AS anchors,
                       MIN(batch.coverage_start) FILTER (
                           WHERE batch.publication_eligible = TRUE
                       ) AS coverage_start,
                       MAX(batch.coverage_end) FILTER (
                           WHERE batch.publication_eligible = TRUE
                       ) AS coverage_end,
                       MIN(
                           (anchor.statement_date AT TIME ZONE
                               'America/New_York')::DATE
                       ) AS anchor_start,
                       MAX(
                           (anchor.statement_date AT TIME ZONE
                               'America/New_York')::DATE
                       ) AS anchor_end,
                       (
                           SELECT COUNT(*)
                           FROM portfolio_snapshots snapshot
                           WHERE snapshot.account_id = account.account_id
                             AND (snapshot.captured_at AT TIME ZONE
                                 'America/New_York')::TIME >= TIME '16:00'
                       ) AS after_close_valuations,
                       (
                           SELECT MIN(
                               (snapshot.captured_at AT TIME ZONE
                                   'America/New_York')::DATE
                           )
                           FROM portfolio_snapshots snapshot
                           WHERE snapshot.account_id = account.account_id
                             AND (snapshot.captured_at AT TIME ZONE
                                 'America/New_York')::TIME >= TIME '16:00'
                       ) AS valuation_start,
                       (
                           SELECT MAX(
                               (snapshot.captured_at AT TIME ZONE
                                   'America/New_York')::DATE
                           )
                           FROM portfolio_snapshots snapshot
                           WHERE snapshot.account_id = account.account_id
                             AND (snapshot.captured_at AT TIME ZONE
                                 'America/New_York')::TIME >= TIME '16:00'
                       ) AS valuation_end,
                       (
                           SELECT COUNT(*)
                           FROM cash_activities activity
                           WHERE activity.account_id = account.account_id
                             AND activity.is_external_flow = TRUE
                       ) AS provider_external_flows,
                       (
                           SELECT COUNT(*)
                           FROM statement_external_flows flow
                           WHERE flow.internal_account_id =
                               account.internal_account_id
                             AND flow.valuation_status = 'reported'
                             AND flow.amount IS NOT NULL
                       ) AS statement_external_flows
                FROM brokerage_accounts account
                LEFT JOIN broker_connections connection
                  ON connection.connection_id = account.connection_id
                LEFT JOIN service_connection_state state
                  ON state.provider = 'webull'
                LEFT JOIN statement_import_batches batch
                  ON batch.internal_account_id = account.internal_account_id
                LEFT JOIN statement_anchors anchor
                  ON anchor.account_id = account.account_id
                WHERE account.provider = 'webull'
                GROUP BY account.account_id, account.account_type,
                         account.status, account.currency,
                         account.account_handle, account.internal_account_id,
                         account.cash_activity_coverage_start,
                         account.cash_activity_coverage_end,
                         account.cash_activity_gap_start,
                         account.cash_activity_gap_end,
                         account.owner_github_id, connection.provider,
                         connection.status,
                         connection.owner_github_id, state.selected_account_id
                ORDER BY selected DESC, account.account_type
                """
            )
            rows = cursor.fetchall()
    except Exception:  # noqa: BLE001 - never expose database or account details.
        raise ReconciliationAuditError("database") from None
    return tuple(
        _safe_row(row, expected_owner_github_id, expected_account_handle_sha256)
        for row in rows
    )


def _owner_state(value: object, expected_owner_github_id: str) -> str:
    if value is None or str(value).strip() == "":
        return "unowned"
    if expected_owner_github_id and str(value) == expected_owner_github_id:
        return "matches"
    return "conflict"


def _safe_row(
    row: dict[str, object],
    expected_owner_github_id: str = "",
    expected_account_handle_sha256: str = "",
) -> dict[str, object]:
    def safe_date(value: object) -> str | None:
        if isinstance(value, datetime):
            return value.date().isoformat()
        return value.isoformat() if isinstance(value, date) else None

    return {
        "accountType": str(row.get("account_type") or "unknown")[:40],
        "status": str(row.get("status") or "unknown")[:40],
        "currency": str(row.get("currency") or "unknown")[:8],
        "connectionProvider": str(row.get("connection_provider") or "missing")[:40],
        "connectionStatus": str(row.get("connection_status") or "missing")[:40],
        "canonicalAccountHandle": row.get("canonical_account_handle") is True,
        "expectedAccountHandle": bool(expected_account_handle_sha256)
        and sha256(str(row.get("account_handle") or "").encode()).hexdigest()
        == expected_account_handle_sha256,
        "selected": row.get("selected") is True,
        "accountOwnerState": _owner_state(
            row.get("account_owner_github_id"), expected_owner_github_id
        ),
        "connectionOwnerState": _owner_state(
            row.get("connection_owner_github_id"), expected_owner_github_id
        ),
        "statementBatches": int(row.get("statement_batches") or 0),
        "eligibleBatches": int(row.get("eligible_batches") or 0),
        "anchors": int(row.get("anchors") or 0),
        "coverageStart": safe_date(row.get("coverage_start")),
        "coverageEnd": safe_date(row.get("coverage_end")),
        "anchorStart": safe_date(row.get("anchor_start")),
        "anchorEnd": safe_date(row.get("anchor_end")),
        "afterCloseValuations": int(row.get("after_close_valuations") or 0),
        "valuationStart": safe_date(row.get("valuation_start")),
        "valuationEnd": safe_date(row.get("valuation_end")),
        "providerExternalFlows": int(row.get("provider_external_flows") or 0),
        "statementExternalFlows": int(row.get("statement_external_flows") or 0),
        "providerCoverageStart": safe_date(row.get("cash_activity_coverage_start")),
        "providerCoverageEnd": safe_date(row.get("cash_activity_coverage_end")),
        "providerGapStart": safe_date(row.get("cash_activity_gap_start")),
        "providerGapEnd": safe_date(row.get("cash_activity_gap_end")),
    }


def main() -> int:
    try:
        accounts = reconciliation_audit(
            os.getenv("DATABASE_URL", "").strip(),
            os.getenv("PORTFOLIO_OWNER_GITHUB_ID", "").strip(),
            os.getenv("BROKER_STATEMENT_AUDIT_HANDLE_SHA256", "").strip(),
        )
    except ReconciliationAuditError as exc:
        print(f"Private reconciliation audit failed ({exc}).", file=sys.stderr)
        return 1
    print(json.dumps({"accounts": accounts}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
