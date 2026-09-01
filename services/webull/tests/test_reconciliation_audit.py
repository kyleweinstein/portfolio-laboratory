from __future__ import annotations

from datetime import date
from hashlib import sha256

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
    }
    rendered = str(projected)
    assert "private-account-id" not in rendered
    assert "private-internal-id" not in rendered
    assert "private-value" not in rendered
    assert "private-account-handle" not in rendered
    assert "expected-owner" not in rendered


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
