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
                SELECT account.account_id, account.account_type,
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
            cursor.execute(
                """
                WITH selected AS (
                    SELECT account.account_id, account.internal_account_id
                    FROM brokerage_accounts account
                    JOIN service_connection_state state
                      ON state.provider = 'webull'
                     AND state.selected_account_id = account.account_id
                    WHERE account.provider = 'webull'
                ), ordered_anchors AS (
                    SELECT anchor.account_id, anchor.statement_date,
                           anchor.ending_equity,
                           ROW_NUMBER() OVER (
                               PARTITION BY anchor.account_id
                               ORDER BY anchor.statement_date
                           ) AS first_ordinal,
                           ROW_NUMBER() OVER (
                               PARTITION BY anchor.account_id
                               ORDER BY anchor.statement_date DESC
                           ) AS last_ordinal
                    FROM statement_anchors anchor
                    JOIN selected ON selected.account_id = anchor.account_id
                ), bounds AS (
                    SELECT account_id,
                           MAX(ending_equity) FILTER (
                               WHERE first_ordinal = 1
                           ) AS beginning_value,
                           MAX(ending_equity) FILTER (
                               WHERE last_ordinal = 1
                           ) AS ending_value,
                           MIN(statement_date) AS period_start,
                           MAX(statement_date) AS period_end
                    FROM ordered_anchors
                    GROUP BY account_id
                ), flows AS (
                    SELECT selected.account_id, flow.kind, flow.amount,
                           flow.flow_date,
                           COALESCE(
                               flow.flow_at,
                               (flow.flow_date + TIME '16:00') AT TIME ZONE
                                   'America/New_York'
                           ) AS occurred_at
                    FROM statement_external_flows flow
                    JOIN selected
                      ON selected.internal_account_id = flow.internal_account_id
                    WHERE flow.valuation_status = 'reported'
                      AND flow.amount IS NOT NULL
                ), period_flows AS (
                    SELECT flow.*, bounds.period_start, bounds.period_end,
                           EXTRACT(
                               EPOCH FROM (bounds.period_end - flow.occurred_at)
                           ) / NULLIF(
                               EXTRACT(
                                   EPOCH FROM (
                                       bounds.period_end - bounds.period_start
                                   )
                               ),
                               0
                           ) AS remaining_weight
                    FROM flows flow
                    JOIN bounds ON bounds.account_id = flow.account_id
                    WHERE flow.occurred_at > bounds.period_start
                      AND flow.occurred_at <= bounds.period_end
                ), aggregates AS (
                    SELECT bounds.account_id, bounds.beginning_value,
                           bounds.ending_value, bounds.period_start,
                           bounds.period_end,
                           COUNT(period_flows.amount) AS flow_count,
                           COALESCE(SUM(period_flows.amount), 0) AS net_flow,
                           COALESCE(
                               SUM(
                                   period_flows.amount *
                                   period_flows.remaining_weight
                               ),
                               0
                           ) AS weighted_flow,
                           COUNT(*) FILTER (
                               WHERE (
                                   period_flows.kind IN (
                                       'deposit', 'security_transfer_in'
                                   ) AND period_flows.amount < 0
                               ) OR (
                                   period_flows.kind IN (
                                       'withdrawal', 'security_transfer_out'
                                   ) AND period_flows.amount > 0
                               )
                           ) AS sign_mismatches,
                           COUNT(*) FILTER (
                               WHERE period_flows.kind = 'deposit'
                           ) AS deposits,
                           COUNT(*) FILTER (
                               WHERE period_flows.kind = 'withdrawal'
                           ) AS withdrawals,
                           COUNT(*) FILTER (
                               WHERE period_flows.kind = 'security_transfer_in'
                           ) AS security_transfers_in,
                           COUNT(*) FILTER (
                               WHERE period_flows.kind = 'security_transfer_out'
                           ) AS security_transfers_out,
                           MIN(period_flows.flow_date) AS flow_start,
                           MAX(period_flows.flow_date) AS flow_end
                    FROM bounds
                    LEFT JOIN period_flows
                      ON period_flows.account_id = bounds.account_id
                    GROUP BY bounds.account_id, bounds.beginning_value,
                             bounds.ending_value, bounds.period_start,
                             bounds.period_end
                )
                SELECT account_id, flow_count, sign_mismatches, deposits,
                       withdrawals, security_transfers_in,
                       security_transfers_out, flow_start, flow_end,
                       CASE WHEN beginning_value > 0 THEN
                           100 * (ending_value / beginning_value - 1)
                       END AS simple_return_percent,
                       CASE WHEN beginning_value > 0 THEN
                           100 * net_flow / beginning_value
                       END AS net_flow_percent_of_beginning,
                       CASE WHEN beginning_value + weighted_flow <> 0 THEN
                           100 * (
                               ending_value - beginning_value - net_flow
                           ) / (beginning_value + weighted_flow)
                       END AS modified_dietz_percent
                FROM aggregates
                """
            )
            performance_diagnostics = {
                row["account_id"]: row for row in cursor.fetchall()
            }
    except Exception:  # noqa: BLE001 - never expose database or account details.
        raise ReconciliationAuditError("database") from None
    return tuple(
        _safe_row(
            {**row, **performance_diagnostics.get(row["account_id"], {})},
            expected_owner_github_id,
            expected_account_handle_sha256,
        )
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

    def safe_percent(value: object) -> float | None:
        try:
            return round(float(value), 4) if value is not None else None
        except (TypeError, ValueError):
            return None

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
        "flowCount": int(row.get("flow_count") or 0),
        "flowSignMismatches": int(row.get("sign_mismatches") or 0),
        "deposits": int(row.get("deposits") or 0),
        "withdrawals": int(row.get("withdrawals") or 0),
        "securityTransfersIn": int(row.get("security_transfers_in") or 0),
        "securityTransfersOut": int(row.get("security_transfers_out") or 0),
        "flowStart": safe_date(row.get("flow_start")),
        "flowEnd": safe_date(row.get("flow_end")),
        "simpleReturnPercent": safe_percent(row.get("simple_return_percent")),
        "netFlowPercentOfBeginning": safe_percent(
            row.get("net_flow_percent_of_beginning")
        ),
        "modifiedDietzPercent": safe_percent(row.get("modified_dietz_percent")),
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
