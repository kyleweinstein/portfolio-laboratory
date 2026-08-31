from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Literal

SCHEMA_VERSION = "portfolio-lab.m1-statement-backfill.v2"
RECONCILIATION_SCHEMA_VERSION = "portfolio-lab.broker-reconciliation.v2"
PARSER_VERSION = "m1-apex-text-v2"

Provider = Literal["m1", "apex"]
FlowKind = Literal["deposit", "withdrawal", "security_transfer_in", "security_transfer_out", "security_transfer_unknown"]
Severity = Literal["warning", "error"]


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    provider: Provider
    parser_version: str
    page_count: int
    statement_start: str
    statement_end: str
    account_fingerprint: str


@dataclass(frozen=True)
class StatementAnchor:
    account_handle: str
    source_id: str
    date: str
    currency: str
    net_liquidation_value: Decimal
    cash_balance: Decimal | None
    margin_debit_balance: Decimal | None
    signed_cash_balance: Decimal | None
    quality: Literal["statement_reconciled"] = "statement_reconciled"


@dataclass(frozen=True)
class ExternalFlow:
    account_handle: str
    source_id: str
    sequence: int
    date: str
    kind: FlowKind
    amount: Decimal | None
    currency: str
    valuation_status: Literal["reported", "unavailable"]
    evidence: Literal["statement_activity"] = "statement_activity"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Severity
    source_id: str | None = None
    date: str | None = None


@dataclass
class ParsedStatement:
    source: SourceDocument
    anchor: StatementAnchor
    flows: list[ExternalFlow] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


def to_private_dict(value: Any) -> Any:
    """Serialize dataclasses for the private import artifact.

    Decimal amounts are strings to avoid floating-point drift in backend imports.
    """

    if isinstance(value, Decimal):
        return decimal_text(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_private_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_private_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_private_dict(item) for item in value]
    return value
