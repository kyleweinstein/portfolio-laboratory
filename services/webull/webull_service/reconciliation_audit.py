from __future__ import annotations

import json
import os
import sys
from datetime import date

import psycopg
from psycopg.rows import dict_row


class ReconciliationAuditError(RuntimeError):
    """A deliberately generic private audit failure."""


def reconciliation_audit(
    database_url: str,
    expected_owner_github_id: str,
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
                       ) AS coverage_end
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
                         account.status, account.owner_github_id,
                         connection.owner_github_id, state.selected_account_id
                ORDER BY selected DESC, account.account_type
                """
            )
            rows = cursor.fetchall()
    except Exception:  # noqa: BLE001 - never expose database or account details.
        raise ReconciliationAuditError("database") from None
    return tuple(_safe_row(row, expected_owner_github_id) for row in rows)


def _owner_state(value: object, expected_owner_github_id: str) -> str:
    if value is None or str(value).strip() == "":
        return "unowned"
    if expected_owner_github_id and str(value) == expected_owner_github_id:
        return "matches"
    return "conflict"


def _safe_row(
    row: dict[str, object],
    expected_owner_github_id: str = "",
) -> dict[str, object]:
    def safe_date(value: object) -> str | None:
        return value.isoformat() if isinstance(value, date) else None

    return {
        "accountType": str(row.get("account_type") or "unknown")[:40],
        "status": str(row.get("status") or "unknown")[:40],
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
    }


def main() -> int:
    try:
        accounts = reconciliation_audit(
            os.getenv("DATABASE_URL", "").strip(),
            os.getenv("PORTFOLIO_OWNER_GITHUB_ID", "").strip(),
        )
    except ReconciliationAuditError as exc:
        print(f"Private reconciliation audit failed ({exc}).", file=sys.stderr)
        return 1
    print(json.dumps({"accounts": accounts}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
