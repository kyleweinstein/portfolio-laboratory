from __future__ import annotations

from datetime import date

from webull_service.reconciliation_audit import _safe_row


def test_audit_projection_contains_no_account_identifiers_or_values() -> None:
    projected = _safe_row(
        {
            "account_id": "private-account-id",
            "internal_account_id": "private-internal-id",
            "account_type": "MARGIN",
            "status": "ACTIVE",
            "selected": True,
            "statement_batches": 1,
            "eligible_batches": 1,
            "anchors": 2,
            "coverage_start": date(2025, 12, 1),
            "coverage_end": date(2026, 7, 31),
            "ending_equity": "private-value",
        }
    )

    assert projected == {
        "accountType": "MARGIN",
        "status": "ACTIVE",
        "selected": True,
        "statementBatches": 1,
        "eligibleBatches": 1,
        "anchors": 2,
        "coverageStart": "2025-12-01",
        "coverageEnd": "2026-07-31",
    }
    rendered = str(projected)
    assert "private-account-id" not in rendered
    assert "private-internal-id" not in rendered
    assert "private-value" not in rendered
