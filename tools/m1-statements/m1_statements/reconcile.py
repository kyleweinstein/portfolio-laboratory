from __future__ import annotations

import calendar
import hashlib
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any

from .errors import SafeToolError
from .models import RECONCILIATION_SCHEMA_VERSION, ParsedStatement, ValidationIssue


def _date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007 - calendar date only
    except (TypeError, ValueError) as exc:
        raise SafeToolError("RECONCILIATION_DATE_INVALID", "Reconciliation input contains an invalid date.") from exc


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SafeToolError("RECONCILIATION_AMOUNT_INVALID", "Reconciliation input contains an invalid amount.") from exc


def _next_month(value: date) -> tuple[int, int]:
    return (value.year + (1 if value.month == 12 else 0), 1 if value.month == 12 else value.month + 1)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def validate_statement_set(
    statements: list[ParsedStatement],
    *,
    allow_gaps: bool,
    expected_start: str | None = None,
    expected_end: str | None = None,
) -> tuple[list[ParsedStatement], list[ValidationIssue], dict[str, Any]]:
    if not statements:
        raise SafeToolError("NO_STATEMENTS", "No supported PDF statements were found.")

    ordered = sorted(statements, key=lambda item: (item.source.statement_end, item.source.source_id))
    issues = [issue for statement in ordered for issue in statement.issues]
    fingerprints = {statement.source.account_fingerprint for statement in ordered}
    if len(fingerprints) != 1:
        issues.append(ValidationIssue(code="MULTIPLE_ACCOUNTS", severity="error"))

    unique_by_date: dict[str, ParsedStatement] = {}
    for statement in ordered:
        previous = unique_by_date.get(statement.anchor.date)
        if previous is None:
            unique_by_date[statement.anchor.date] = statement
            continue
        if (
            previous.anchor.net_liquidation_value != statement.anchor.net_liquidation_value
            or previous.anchor.signed_cash_balance != statement.anchor.signed_cash_balance
        ):
            issues.append(
                ValidationIssue(
                    code="CONFLICTING_STATEMENT_ANCHOR",
                    severity="error",
                    source_id=statement.source.source_id,
                    date=statement.anchor.date,
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    code="REDUNDANT_STATEMENT_PERIOD",
                    severity="warning",
                    source_id=statement.source.source_id,
                    date=statement.anchor.date,
                )
            )

    unique = sorted(unique_by_date.values(), key=lambda item: item.source.statement_end)
    contiguous = True
    for previous, current in pairwise(unique):
        previous_end = _date(previous.source.statement_end)
        current_end = _date(current.source.statement_end)
        expected_year, expected_month = _next_month(previous_end)
        if (current_end.year, current_end.month) != (expected_year, expected_month):
            contiguous = False
            issues.append(
                ValidationIssue(
                    code="MISSING_STATEMENT_MONTH",
                    severity="warning" if allow_gaps else "error",
                    date=_month_end(expected_year, expected_month).isoformat(),
                )
            )

    coverage_start = min(_date(item.source.statement_start) for item in unique)
    coverage_end = max(_date(item.source.statement_end) for item in unique)
    if expected_start and coverage_start > _date(expected_start):
        issues.append(
            ValidationIssue(code="EXPECTED_COVERAGE_START_MISSING", severity="error", date=expected_start)
        )
    if expected_end and coverage_end < _date(expected_end):
        issues.append(ValidationIssue(code="EXPECTED_COVERAGE_END_MISSING", severity="error", date=expected_end))

    coverage = {
        "start": coverage_start.isoformat(),
        "end": coverage_end.isoformat(),
        "statementCount": len(unique),
        "contiguousMonthlyCoverage": contiguous,
    }
    return unique, issues, coverage


def _within_tolerance(actual: Decimal, expected: Decimal) -> bool:
    absolute_tolerance = Decimal("0.01")
    relative_tolerance = abs(expected) * Decimal("0.000001")
    return abs(actual - expected) <= max(absolute_tolerance, relative_tolerance)


def _load_reconciliation(path: Path, account_handle: str) -> tuple[dict[str, Any], str]:
    try:
        payload = path.read_bytes()
        data = json.loads(payload)
    except Exception as exc:
        raise SafeToolError(
            "RECONCILIATION_READ_FAILED",
            "The private reconciliation export could not be read.",
        ) from exc
    if not isinstance(data, dict) or data.get("schemaVersion") != RECONCILIATION_SCHEMA_VERSION:
        raise SafeToolError(
            "RECONCILIATION_SCHEMA_INVALID",
            "The private reconciliation export uses an unsupported schema.",
        )
    if data.get("accountHandle") != account_handle:
        raise SafeToolError(
            "RECONCILIATION_ACCOUNT_MISMATCH",
            "The reconciliation export does not match the requested Portfolio Lab account handle.",
        )
    return data, "sha256:" + hashlib.sha256(payload).hexdigest()


def reconcile_with_private_export(
    statements: list[ParsedStatement],
    *,
    account_handle: str,
    reconciliation_path: Path | None,
) -> dict[str, Any]:
    if reconciliation_path is None:
        return {
            "status": "not_requested",
            "privateInputSourceId": None,
            "matchedAnchors": 0,
            "mismatchedAnchors": 0,
            "matchedFlows": 0,
            "mismatchedFlows": 0,
            "issues": [],
        }

    private_data, private_source_id = _load_reconciliation(reconciliation_path, account_handle)
    snapshot_rows = private_data.get("snapshots", [])
    flow_rows = private_data.get("externalFlows", [])
    if not isinstance(snapshot_rows, list) or not isinstance(flow_rows, list):
        raise SafeToolError(
            "RECONCILIATION_SCHEMA_INVALID",
            "The private reconciliation export has invalid collections.",
        )

    snapshots: dict[str, dict[str, Any]] = {}
    for row in snapshot_rows:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            raise SafeToolError(
                "RECONCILIATION_SCHEMA_INVALID",
                "The private reconciliation export has an invalid snapshot row.",
            )
        _date(row["date"])
        snapshots[row["date"]] = row

    matched_anchors = 0
    mismatched_anchors = 0
    issues: list[dict[str, str]] = []
    for statement in statements:
        row = snapshots.get(statement.anchor.date)
        if row is None:
            mismatched_anchors += 1
            issues.append({"code": "RECONCILIATION_ANCHOR_MISSING", "date": statement.anchor.date})
            continue
        nlv_matches = _within_tolerance(
            statement.anchor.net_liquidation_value,
            _decimal(row.get("netLiquidationValue")),
        )
        cash_matches = True
        if statement.anchor.signed_cash_balance is not None and row.get("signedCashBalance") is not None:
            cash_matches = _within_tolerance(
                statement.anchor.signed_cash_balance,
                _decimal(row.get("signedCashBalance")),
            )
        if nlv_matches and cash_matches:
            matched_anchors += 1
        else:
            mismatched_anchors += 1
            issues.append({"code": "RECONCILIATION_ANCHOR_MISMATCH", "date": statement.anchor.date})

    expected_flows: Counter[tuple[str, str, Decimal]] = Counter()
    for row in flow_rows:
        if not isinstance(row, dict):
            raise SafeToolError(
                "RECONCILIATION_SCHEMA_INVALID",
                "The private reconciliation export has an invalid flow row.",
            )
        flow_date = str(row.get("date", ""))
        _date(flow_date)
        expected_flows[(flow_date, str(row.get("kind", "")), _decimal(row.get("amount")))] += 1

    statement_flows: Counter[tuple[str, str, Decimal]] = Counter()
    unavailable_flow_count = 0
    for statement in statements:
        for flow in statement.flows:
            if flow.amount is None:
                unavailable_flow_count += 1
                continue
            statement_flows[(flow.date, flow.kind, flow.amount)] += 1

    overlap = statement_flows & expected_flows
    matched_flows = sum(overlap.values())
    mismatched_flows = sum((statement_flows - expected_flows).values()) + sum(
        (expected_flows - statement_flows).values()
    ) + unavailable_flow_count
    if mismatched_flows:
        issues.append({"code": "RECONCILIATION_FLOW_MISMATCH"})

    status = "passed" if mismatched_anchors == 0 and mismatched_flows == 0 else "failed"
    return {
        "status": status,
        "privateInputSourceId": private_source_id,
        "matchedAnchors": matched_anchors,
        "mismatchedAnchors": mismatched_anchors,
        "matchedFlows": matched_flows,
        "mismatchedFlows": mismatched_flows,
        "issues": issues,
    }
