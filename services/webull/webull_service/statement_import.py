from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as Date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, ClassVar, Literal, Protocol
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

SCHEMA_VERSION = "portfolio-lab.m1-statement-backfill.v2"
M1_STATEMENT_OUTPUT_KEY_NAME = "M1_STATEMENT_OUTPUT_KEY"
MAX_ENCRYPTED_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_DECRYPTED_BUNDLE_BYTES = 12 * 1024 * 1024

_ACCOUNT_HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{6,62}[A-Za-z0-9])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MONEY_RE = re.compile(r"^-?\d{1,18}\.\d{2}$")
_SOURCE_ID_RE = r"^sha256:[a-f0-9]{64}$"
_FINGERPRINT_RE = r"^hmac-sha256:[a-f0-9]{64}$"
_CODE_RE = r"^[A-Z][A-Z0-9_]{1,79}$"


class StatementImportError(RuntimeError):
    """Deterministic error whose text is always safe for logs and owner responses."""

    _MESSAGES: ClassVar[dict[str, str]] = {
        "STATEMENT_IMPORT_AUTH_INVALID": "The statement import owner context is invalid.",
        "STATEMENT_IMPORT_BUNDLE_TOO_LARGE": "The encrypted statement bundle exceeds the import limit.",
        "STATEMENT_IMPORT_KEY_INVALID": "The statement import encryption key is invalid.",
        "STATEMENT_IMPORT_DECRYPT_FAILED": "The encrypted statement bundle could not be decrypted.",
        "STATEMENT_IMPORT_JSON_INVALID": "The statement import bundle is not valid canonical JSON.",
        "STATEMENT_IMPORT_SCHEMA_INVALID": "The statement import bundle uses an invalid or unsupported schema.",
        "STATEMENT_IMPORT_INTEGRITY_FAILED": "The statement import bundle failed integrity validation.",
        "STATEMENT_IMPORT_NOT_READY": "The statement import bundle is not ready to import.",
        "STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE": "The statement import account is unavailable.",
        "STATEMENT_IMPORT_PERSIST_FAILED": "The statement import could not be committed atomically.",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "STATEMENT_IMPORT_INTEGRITY_FAILED"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)


class _StrictImportModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=False,
    )


def _parse_exact_date(value: Any) -> Date:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise ValueError("invalid date")
    try:
        parsed = Date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid date") from exc
    if parsed.isoformat() != value:
        raise ValueError("invalid date")
    return parsed


def _parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    return parsed.astimezone(UTC)


def _parse_uuid(value: Any) -> UUID:
    if not isinstance(value, str):
        raise TypeError("invalid UUID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid UUID") from exc
    if str(parsed) != value:
        raise ValueError("invalid UUID")
    return parsed


def _parse_money(value: Any) -> Decimal:
    if not isinstance(value, str) or _MONEY_RE.fullmatch(value) is None:
        raise ValueError("invalid decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if not parsed.is_finite() or format(parsed, "f") != value:
        raise ValueError("invalid decimal")
    return parsed


def _validate_account_handle(value: str) -> str:
    if _ACCOUNT_HANDLE_RE.fullmatch(value) is None or value.isdecimal():
        raise ValueError("invalid account handle")
    return value


class StatementSource(_StrictImportModel):
    source_id: StrictStr = Field(alias="sourceId", pattern=_SOURCE_ID_RE)
    provider: Literal["m1", "apex"]
    parser_version: StrictStr = Field(
        alias="parserVersion", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$"
    )
    page_count: StrictInt = Field(alias="pageCount", ge=1, le=10_000)
    statement_start: Date = Field(alias="statementStart")
    statement_end: Date = Field(alias="statementEnd")
    account_fingerprint: StrictStr = Field(
        alias="accountFingerprint", pattern=_FINGERPRINT_RE
    )

    @field_validator("statement_start", "statement_end", mode="before")
    @classmethod
    def parse_statement_date(cls, value: Any) -> Date:
        return _parse_exact_date(value)


class StatementAnchorImport(_StrictImportModel):
    account_handle: StrictStr = Field(
        alias="accountHandle", min_length=8, max_length=64
    )
    source_id: StrictStr = Field(alias="sourceId", pattern=_SOURCE_ID_RE)
    date: Date
    currency: Literal["USD"]
    net_liquidation_value: Decimal = Field(alias="netLiquidationValue")
    cash_balance: Decimal | None = Field(alias="cashBalance")
    margin_debit_balance: Decimal | None = Field(alias="marginDebitBalance")
    signed_cash_balance: Decimal | None = Field(alias="signedCashBalance")
    quality: Literal["statement_reconciled"]

    @field_validator("account_handle")
    @classmethod
    def validate_handle(cls, value: str) -> str:
        return _validate_account_handle(value)

    @field_validator("date", mode="before")
    @classmethod
    def parse_anchor_date(cls, value: Any) -> Date:
        return _parse_exact_date(value)

    @field_validator(
        "net_liquidation_value",
        "cash_balance",
        "margin_debit_balance",
        "signed_cash_balance",
        mode="before",
    )
    @classmethod
    def parse_anchor_money(cls, value: Any) -> Decimal | None:
        return None if value is None else _parse_money(value)


class StatementExternalFlowImport(_StrictImportModel):
    account_handle: StrictStr = Field(
        alias="accountHandle", min_length=8, max_length=64
    )
    source_id: StrictStr = Field(alias="sourceId", pattern=_SOURCE_ID_RE)
    sequence: StrictInt = Field(ge=1, le=100_000)
    date: Date
    kind: Literal[
        "deposit",
        "withdrawal",
        "security_transfer_in",
        "security_transfer_out",
        "security_transfer_unknown",
    ]
    amount: Decimal | None
    currency: Literal["USD"]
    valuation_status: Literal["reported", "unavailable"] = Field(
        alias="valuationStatus"
    )
    evidence: Literal["statement_activity"]

    @field_validator("account_handle")
    @classmethod
    def validate_handle(cls, value: str) -> str:
        return _validate_account_handle(value)

    @field_validator("date", mode="before")
    @classmethod
    def parse_flow_date(cls, value: Any) -> Date:
        return _parse_exact_date(value)

    @field_validator("amount", mode="before")
    @classmethod
    def parse_flow_money(cls, value: Any) -> Decimal | None:
        return None if value is None else _parse_money(value)


class StatementValidationIssue(_StrictImportModel):
    code: StrictStr = Field(pattern=_CODE_RE)
    severity: Literal["warning", "error"]
    source_id: StrictStr | None = Field(
        default=None, alias="sourceId", pattern=_SOURCE_ID_RE
    )
    date: Date | None = None

    @field_validator("date", mode="before")
    @classmethod
    def parse_issue_date(cls, value: Any) -> Date | None:
        return None if value is None else _parse_exact_date(value)


class StatementCoverage(_StrictImportModel):
    start: Date
    end: Date
    statement_count: StrictInt = Field(alias="statementCount", ge=1, le=10_000)
    contiguous_monthly_coverage: StrictBool = Field(alias="contiguousMonthlyCoverage")

    @field_validator("start", "end", mode="before")
    @classmethod
    def parse_coverage_date(cls, value: Any) -> Date:
        return _parse_exact_date(value)


class ReconciliationIssue(_StrictImportModel):
    code: StrictStr = Field(pattern=_CODE_RE)
    date: Date | None = None

    @field_validator("date", mode="before")
    @classmethod
    def parse_issue_date(cls, value: Any) -> Date | None:
        return None if value is None else _parse_exact_date(value)


class StatementReconciliation(_StrictImportModel):
    status: Literal["not_requested", "passed", "failed"]
    private_input_source_id: StrictStr | None = Field(
        alias="privateInputSourceId", pattern=_SOURCE_ID_RE
    )
    matched_anchors: StrictInt = Field(alias="matchedAnchors", ge=0, le=10_000)
    mismatched_anchors: StrictInt = Field(alias="mismatchedAnchors", ge=0, le=10_000)
    matched_flows: StrictInt = Field(alias="matchedFlows", ge=0, le=1_000_000)
    mismatched_flows: StrictInt = Field(alias="mismatchedFlows", ge=0, le=1_000_000)
    issues: tuple[ReconciliationIssue, ...] = Field(max_length=10_000)


class StatementValidation(_StrictImportModel):
    import_ready: StrictBool = Field(alias="importReady")
    publication_ready: StrictBool = Field(alias="publicationReady")
    error_count: StrictInt = Field(alias="errorCount", ge=0, le=100_000)
    warning_count: StrictInt = Field(alias="warningCount", ge=0, le=100_000)
    duplicate_source_count: StrictInt = Field(
        alias="duplicateSourceCount", ge=0, le=100_000
    )
    coverage: StatementCoverage
    issues: tuple[StatementValidationIssue, ...] = Field(max_length=100_000)
    reconciliation: StatementReconciliation


class StatementBackfillBundleV2(_StrictImportModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    batch_id: UUID = Field(alias="batchId")
    generated_at: datetime = Field(alias="generatedAt")
    account_handle: StrictStr = Field(
        alias="accountHandle", min_length=8, max_length=64
    )
    sources: tuple[StatementSource, ...] = Field(min_length=1, max_length=10_000)
    anchors: tuple[StatementAnchorImport, ...] = Field(min_length=1, max_length=10_000)
    external_flows: tuple[StatementExternalFlowImport, ...] = Field(
        alias="externalFlows", max_length=1_000_000
    )
    validation: StatementValidation

    @field_validator("batch_id", mode="before")
    @classmethod
    def parse_batch_id(cls, value: Any) -> UUID:
        return _parse_uuid(value)

    @field_validator("generated_at", mode="before")
    @classmethod
    def parse_generated_at(cls, value: Any) -> datetime:
        return _parse_utc_timestamp(value)

    @field_validator("account_handle")
    @classmethod
    def validate_handle(cls, value: str) -> str:
        return _validate_account_handle(value)


class StatementImportDisposition(str, Enum):
    IMPORTED = "imported"
    ALREADY_IMPORTED = "already_imported"


class StatementImportReceipt(_StrictImportModel):
    """Amount-free result safe for an owner status response after redaction policy."""

    batch_id: UUID = Field(alias="batchId")
    account_handle: StrictStr = Field(
        alias="accountHandle", min_length=8, max_length=64
    )
    source_document_count: StrictInt = Field(alias="sourceDocumentCount", ge=1)
    anchor_count: StrictInt = Field(alias="anchorCount", ge=1)
    external_flow_count: StrictInt = Field(alias="externalFlowCount", ge=0)
    coverage_start: Date = Field(alias="coverageStart")
    coverage_end: Date = Field(alias="coverageEnd")
    import_status: StatementImportDisposition = Field(alias="importStatus")
    publication_eligible: StrictBool = Field(alias="publicationEligible")


@dataclass(frozen=True)
class StatementImportCommitResult:
    """Repository-confirmed amount-free coverage and eligibility."""

    disposition: StatementImportDisposition
    coverage_start: Date
    coverage_end: Date
    publication_eligible: bool


@dataclass(frozen=True)
class PreparedStatementImport:
    """Validated private values passed once to the atomic repository callback."""

    bundle: StatementBackfillBundleV2
    decrypted_payload_sha256: str


class AtomicStatementImportCallback(Protocol):
    """Resolve the owner-scoped handle and commit every record in one transaction."""

    def __call__(
        self,
        owner_github_id: str,
        prepared: PreparedStatementImport,
    ) -> StatementImportCommitResult: ...


def statement_source_content_hashes(
    bundle: StatementBackfillBundleV2,
) -> dict[str, str]:
    """Return stable semantic hashes without retaining raw document content."""

    anchor_by_source = {anchor.source_id: anchor for anchor in bundle.anchors}
    flows_by_source: defaultdict[str, list[StatementExternalFlowImport]] = defaultdict(
        list
    )
    for flow in bundle.external_flows:
        flows_by_source[flow.source_id].append(flow)

    result: dict[str, str] = {}
    for source in bundle.sources:
        content = {
            "source": source.model_dump(mode="json", by_alias=True),
            "anchor": anchor_by_source[source.source_id].model_dump(
                mode="json", by_alias=True
            ),
            "externalFlows": [
                flow.model_dump(mode="json", by_alias=True)
                for flow in sorted(
                    flows_by_source[source.source_id],
                    key=lambda item: item.sequence,
                )
            ],
        }
        canonical = json.dumps(
            content,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        result[source.source_id] = hashlib.sha256(canonical).hexdigest()
    return result


def statement_publication_eligible(bundle: StatementBackfillBundleV2) -> bool:
    """Apply service-side coverage gates in addition to producer validation."""

    return bool(
        bundle.validation.publication_ready
        and bundle.validation.coverage.contiguous_monthly_coverage
        and all(
            flow.valuation_status == "reported" and flow.amount is not None
            for flow in bundle.external_flows
        )
    )


def decrypt_statement_bundle(
    encrypted_payload: bytes,
    m1_statement_output_key: str | bytes,
) -> bytes:
    """Decrypt a tool artifact without reading secrets from process environment."""

    if not isinstance(encrypted_payload, bytes) or not encrypted_payload:
        raise StatementImportError("STATEMENT_IMPORT_DECRYPT_FAILED")
    if len(encrypted_payload) > MAX_ENCRYPTED_BUNDLE_BYTES:
        raise StatementImportError("STATEMENT_IMPORT_BUNDLE_TOO_LARGE")
    if not isinstance(m1_statement_output_key, (str, bytes)):
        raise StatementImportError("STATEMENT_IMPORT_KEY_INVALID")
    try:
        key = (
            m1_statement_output_key.encode("ascii")
            if isinstance(m1_statement_output_key, str)
            else m1_statement_output_key
        )
        if len(key) != 44:
            raise ValueError
        fernet = Fernet(key)
    except (UnicodeEncodeError, ValueError, TypeError):
        raise StatementImportError("STATEMENT_IMPORT_KEY_INVALID") from None
    try:
        decrypted = fernet.decrypt(encrypted_payload)
    except InvalidToken:
        raise StatementImportError("STATEMENT_IMPORT_DECRYPT_FAILED") from None
    if len(decrypted) > MAX_DECRYPTED_BUNDLE_BYTES:
        raise StatementImportError("STATEMENT_IMPORT_BUNDLE_TOO_LARGE")
    return decrypted


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _load_json(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise StatementImportError("STATEMENT_IMPORT_JSON_INVALID") from None
    if not isinstance(parsed, dict):
        raise StatementImportError("STATEMENT_IMPORT_JSON_INVALID")
    return parsed


def _validate_bundle_integrity(bundle: StatementBackfillBundleV2) -> None:
    if bundle.batch_id.version != 4:
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
    source_by_id = {source.source_id: source for source in bundle.sources}
    if len(source_by_id) != len(bundle.sources):
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
    if len({source.account_fingerprint for source in bundle.sources}) != 1:
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
    if any(source.statement_start > source.statement_end for source in bundle.sources):
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")

    anchor_keys: set[tuple[str, Date]] = set()
    anchored_source_ids: set[str] = set()
    for anchor in bundle.anchors:
        source = source_by_id.get(anchor.source_id)
        key = (anchor.source_id, anchor.date)
        cash_evidence_available = (
            anchor.cash_balance is not None or anchor.margin_debit_balance is not None
        )
        expected_signed_cash = (
            (anchor.cash_balance or Decimal(0))
            - (anchor.margin_debit_balance or Decimal(0))
            if cash_evidence_available
            else None
        )
        if (
            anchor.account_handle != bundle.account_handle
            or source is None
            or anchor.date != source.statement_end
            or key in anchor_keys
            or anchor.source_id in anchored_source_ids
            or (
                anchor.margin_debit_balance is not None
                and anchor.margin_debit_balance < 0
            )
            or anchor.signed_cash_balance != expected_signed_cash
        ):
            raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
        anchor_keys.add(key)
        anchored_source_ids.add(anchor.source_id)

    flow_keys: set[tuple[str, int]] = set()
    sequences_by_source: defaultdict[str, list[int]] = defaultdict(list)
    for flow in bundle.external_flows:
        source = source_by_id.get(flow.source_id)
        key = (flow.source_id, flow.sequence)
        if (
            flow.account_handle != bundle.account_handle
            or source is None
            or flow.source_id not in anchored_source_ids
            or not source.statement_start <= flow.date <= source.statement_end
            or key in flow_keys
            or (flow.valuation_status == "reported") != (flow.amount is not None)
            or (
                flow.amount is not None
                and flow.kind in {"deposit", "security_transfer_in"}
                and flow.amount < 0
            )
            or (
                flow.amount is not None
                and flow.kind in {"withdrawal", "security_transfer_out"}
                and flow.amount > 0
            )
        ):
            raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
        flow_keys.add(key)
        sequences_by_source[flow.source_id].append(flow.sequence)
    if any(
        sorted(sequences) != list(range(1, len(sequences) + 1))
        for sequences in sequences_by_source.values()
    ):
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")

    coverage = bundle.validation.coverage
    anchored_sources = [source_by_id[source_id] for source_id in anchored_source_ids]
    if (
        coverage.start != min(source.statement_start for source in anchored_sources)
        or coverage.end != max(anchor.date for anchor in bundle.anchors)
        or coverage.statement_count != len(bundle.anchors)
        or coverage.start > coverage.end
    ):
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")

    issue_counts = Counter(issue.severity for issue in bundle.validation.issues)
    validation = bundle.validation
    if any(
        issue.source_id is not None and issue.source_id not in source_by_id
        for issue in validation.issues
    ):
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
    if (
        validation.error_count != issue_counts["error"]
        or validation.warning_count != issue_counts["warning"]
        or validation.import_ready != (validation.error_count == 0)
        or validation.publication_ready
        != (validation.import_ready and validation.reconciliation.status == "passed")
    ):
        raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")

    reconciliation = validation.reconciliation
    if reconciliation.status == "not_requested":
        if (
            reconciliation.private_input_source_id is not None
            or reconciliation.matched_anchors
            or reconciliation.mismatched_anchors
            or reconciliation.matched_flows
            or reconciliation.mismatched_flows
            or reconciliation.issues
        ):
            raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
    else:
        if reconciliation.private_input_source_id is None:
            raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
        if reconciliation.matched_anchors + reconciliation.mismatched_anchors != len(
            bundle.anchors
        ):
            raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")
        if reconciliation.status == "passed" and (
            reconciliation.mismatched_anchors
            or reconciliation.mismatched_flows
            or reconciliation.matched_flows != len(bundle.external_flows)
            or reconciliation.issues
        ):
            raise StatementImportError("STATEMENT_IMPORT_INTEGRITY_FAILED")

    if not validation.import_ready:
        raise StatementImportError("STATEMENT_IMPORT_NOT_READY")


def prepare_statement_import(
    encrypted_payload: bytes,
    m1_statement_output_key: str | bytes,
) -> PreparedStatementImport:
    decrypted = decrypt_statement_bundle(encrypted_payload, m1_statement_output_key)
    raw_bundle = _load_json(decrypted)
    try:
        bundle = StatementBackfillBundleV2.model_validate(raw_bundle)
    except ValidationError:
        raise StatementImportError("STATEMENT_IMPORT_SCHEMA_INVALID") from None
    _validate_bundle_integrity(bundle)
    return PreparedStatementImport(
        bundle=bundle,
        decrypted_payload_sha256=hashlib.sha256(decrypted).hexdigest(),
    )


def import_statement_bundle(
    *,
    encrypted_payload: bytes,
    m1_statement_output_key: str | bytes,
    owner_github_id: str,
    atomic_commit: AtomicStatementImportCallback,
) -> StatementImportReceipt:
    """Validate once, invoke one atomic callback, and emit an amount-free receipt."""

    if (
        not isinstance(owner_github_id, str)
        or re.fullmatch(r"[1-9]\d{0,31}", owner_github_id) is None
    ):
        raise StatementImportError("STATEMENT_IMPORT_AUTH_INVALID")
    prepared = prepare_statement_import(encrypted_payload, m1_statement_output_key)
    try:
        commit_result = atomic_commit(owner_github_id, prepared)
    except StatementImportError:
        raise
    except Exception:  # noqa: BLE001 - the repository boundary must never leak values
        raise StatementImportError("STATEMENT_IMPORT_PERSIST_FAILED") from None
    if isinstance(commit_result, StatementImportDisposition):
        # Compatibility for pure boundary callbacks; production repositories
        # return independently recomputed coverage below.
        commit_result = StatementImportCommitResult(
            disposition=commit_result,
            coverage_start=prepared.bundle.validation.coverage.start,
            coverage_end=prepared.bundle.validation.coverage.end,
            publication_eligible=statement_publication_eligible(prepared.bundle),
        )
    if (
        not isinstance(commit_result, StatementImportCommitResult)
        or not isinstance(commit_result.disposition, StatementImportDisposition)
        or not isinstance(commit_result.coverage_start, Date)
        or not isinstance(commit_result.coverage_end, Date)
        or commit_result.coverage_start > commit_result.coverage_end
        or not isinstance(commit_result.publication_eligible, bool)
    ):
        raise StatementImportError("STATEMENT_IMPORT_PERSIST_FAILED")

    bundle = prepared.bundle
    return StatementImportReceipt.model_validate(
        {
            "batchId": str(bundle.batch_id),
            "accountHandle": bundle.account_handle,
            "sourceDocumentCount": len(bundle.sources),
            "anchorCount": len(bundle.anchors),
            "externalFlowCount": len(bundle.external_flows),
            "coverageStart": commit_result.coverage_start,
            "coverageEnd": commit_result.coverage_end,
            "importStatus": commit_result.disposition,
            "publicationEligible": commit_result.publication_eligible,
        }
    )
