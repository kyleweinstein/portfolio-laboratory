from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path

from webull_service.reconciliation_audit import _safe_row


def test_audit_projection_contains_no_account_identifiers_or_values() -> None:
    projected = _safe_row(
        {
            "account_id": "private-account-id",
            "internal_account_id": "private-internal-id",
            "account_type": "MARGIN",
            "status": "ACTIVE",
            "currency": "USD",
            "account_handle": "private-account-handle",
            "connection_provider": "webull",
            "connection_status": "READY",
            "canonical_account_handle": True,
            "cash_activity_coverage_start": date(2026, 7, 18),
            "cash_activity_coverage_end": date(2026, 8, 31),
            "cash_activity_gap_start": date(2026, 7, 18),
            "cash_activity_gap_end": date(2026, 8, 31),
            "account_owner_github_id": "expected-owner",
            "connection_owner_github_id": None,
            "selected": True,
            "statement_batches": 1,
            "eligible_batches": 1,
            "anchors": 2,
            "coverage_start": date(2025, 12, 1),
            "coverage_end": date(2026, 7, 31),
            "anchor_start": date(2025, 12, 31),
            "anchor_end": date(2026, 7, 31),
            "after_close_valuations": 3,
            "valuation_start": date(2026, 8, 25),
            "valuation_end": date(2026, 8, 28),
            "provider_external_flows": 0,
            "statement_external_flows": 7,
            "flow_count": 7,
            "sign_mismatches": 0,
            "deposits": 4,
            "withdrawals": 2,
            "security_transfers_in": 1,
            "security_transfers_out": 0,
            "flow_start": date(2026, 1, 2),
            "flow_end": date(2026, 7, 30),
            "simple_return_percent": "600.12567",
            "net_flow_percent_of_beginning": "500.11111",
            "modified_dietz_percent": "22.34567",
            "ending_equity": "private-value",
        },
        "expected-owner",
        sha256(b"private-account-handle").hexdigest(),
    )

    assert projected == {
        "accountType": "MARGIN",
        "status": "ACTIVE",
        "currency": "USD",
        "connectionProvider": "webull",
        "connectionStatus": "READY",
        "canonicalAccountHandle": True,
        "expectedAccountHandle": True,
        "selected": True,
        "accountOwnerState": "matches",
        "connectionOwnerState": "unowned",
        "statementBatches": 1,
        "eligibleBatches": 1,
        "anchors": 2,
        "coverageStart": "2025-12-01",
        "coverageEnd": "2026-07-31",
        "anchorStart": "2025-12-31",
        "anchorEnd": "2026-07-31",
        "afterCloseValuations": 3,
        "valuationStart": "2026-08-25",
        "valuationEnd": "2026-08-28",
        "providerExternalFlows": 0,
        "statementExternalFlows": 7,
        "providerCoverageStart": "2026-07-18",
        "providerCoverageEnd": "2026-08-31",
        "providerGapStart": "2026-07-18",
        "providerGapEnd": "2026-08-31",
        "flowCount": 7,
        "flowSignMismatches": 0,
        "deposits": 4,
        "withdrawals": 2,
        "securityTransfersIn": 1,
        "securityTransfersOut": 0,
        "flowStart": "2026-01-02",
        "flowEnd": "2026-07-30",
        "simpleReturnPercent": 600.1257,
        "netFlowPercentOfBeginning": 500.1111,
        "modifiedDietzPercent": 22.3457,
    }
    rendered = str(projected)
    assert "private-account-id" not in rendered
    assert "private-internal-id" not in rendered
    assert "private-value" not in rendered
    assert "private-account-handle" not in rendered
    assert "expected-owner" not in rendered


def test_audit_query_keeps_account_key_private_but_available_for_diagnostics() -> None:
    source = (
        Path(__file__).parents[1] / "webull_service" / "reconciliation_audit.py"
    ).read_text(encoding="utf-8")

    assert "SELECT account.account_id, account.account_type" in source
    assert 'performance_diagnostics.get(row["account_id"], {})' in source


def test_audit_owner_state_fails_closed_without_exposing_ids() -> None:
    projected = _safe_row(
        {
            "account_owner_github_id": "another-owner",
            "connection_owner_github_id": "expected-owner",
        },
        "expected-owner",
    )

    assert projected["accountOwnerState"] == "conflict"
    assert projected["connectionOwnerState"] == "matches"
    assert "another-owner" not in str(projected)
    assert "expected-owner" not in str(projected)
