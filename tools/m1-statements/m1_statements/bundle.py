from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import secrets
import tempfile
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .errors import SafeToolError
from .models import SCHEMA_VERSION, ParsedStatement, ValidationIssue, decimal_text
from .parser import parse_statement, sha256_source
from .reconcile import reconcile_with_private_export, validate_statement_set

OUTPUT_KEY_ENV = "M1_STATEMENT_OUTPUT_KEY"
FINGERPRINT_KEY_ENV = "M1_STATEMENT_FINGERPRINT_KEY"


def _inside_git_worktree(path: Path) -> bool:
    resolved = path.resolve()
    current = resolved if resolved.is_dir() else resolved.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return True
    return False


def assert_private_location(path: Path, *, role: str) -> None:
    if _inside_git_worktree(path):
        raise SafeToolError(
            f"{role.upper()}_INSIDE_GIT_WORKTREE",
            f"The {role.lower()} location must be outside every Git worktree.",
        )


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise SafeToolError(
            "PRIVATE_DIRECTORY_PERMISSIONS_FAILED",
            "Private output directory permissions could not be restricted.",
        ) from exc


def _atomic_private_write(path: Path, payload: bytes) -> None:
    _secure_directory(path.parent)
    descriptor, temp_name = tempfile.mkstemp(prefix=".m1-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        path.chmod(0o600)
    except Exception as exc:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, SafeToolError):
            raise
        raise SafeToolError(
            "PRIVATE_OUTPUT_WRITE_FAILED",
            "Private output could not be written atomically.",
        ) from exc


def create_output_key_file(path: Path) -> None:
    assert_private_location(path, role="key")
    if path.exists():
        raise SafeToolError("KEY_FILE_EXISTS", "The private key file already exists and was not replaced.")
    _atomic_private_write(path, Fernet.generate_key() + b"\n")


def _load_verified_bundle(path: Path) -> dict[str, Any]:
    assert_private_location(path, role="bundle")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SafeToolError("BUNDLE_READ_FAILED", "The private backfill bundle could not be read.") from exc
    if path.name.endswith(".fernet"):
        fernet = _fernet_from_environment()
        if fernet is None:
            raise SafeToolError("OUTPUT_KEY_MISSING", f"Encrypted input requires {OUTPUT_KEY_ENV}.")
        try:
            payload = fernet.decrypt(payload)
        except InvalidToken as exc:
            raise SafeToolError("BUNDLE_DECRYPT_FAILED", "The private backfill bundle could not be decrypted.") from exc
    try:
        bundle = json.loads(payload)
    except Exception as exc:
        raise SafeToolError("BUNDLE_READ_FAILED", "The private backfill bundle could not be read.") from exc
    if not isinstance(bundle, dict) or bundle.get("schemaVersion") != SCHEMA_VERSION:
        raise SafeToolError("BUNDLE_SCHEMA_INVALID", "The private backfill bundle uses an unsupported schema.")
    validation = bundle.get("validation")
    if not isinstance(validation, dict) or validation.get("publicationReady") is not True:
        raise SafeToolError(
            "BUNDLE_NOT_PUBLICATION_READY",
            "Statement PDFs cannot be removed until validation and private reconciliation both pass.",
        )
    return bundle


def cleanup_verified_pdfs(*, input_dir: Path, bundle_path: Path, confirm_delete: bool) -> dict[str, Any]:
    """Delete only PDFs proven to be represented by a publication-ready bundle."""

    assert_private_location(input_dir, role="input")
    bundle = _load_verified_bundle(bundle_path)
    if not input_dir.is_dir():
        raise SafeToolError("INPUT_DIRECTORY_MISSING", "The private statement input directory does not exist.")
    candidates = sorted(candidate for candidate in input_dir.rglob("*") if candidate.suffix.lower() == ".pdf")
    if not candidates:
        raise SafeToolError("NO_PDF_FILES", "The private statement input directory contains no PDF files.")
    source_ids = {
        row.get("sourceId")
        for row in bundle.get("sources", [])
        if isinstance(row, dict) and isinstance(row.get("sourceId"), str)
    }
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            raise SafeToolError("UNSAFE_INPUT_FILE", "The statement directory contains an unsafe PDF entry.")
        try:
            source_id = sha256_source(candidate.read_bytes())
        except OSError as exc:
            raise SafeToolError("PDF_READ_FAILED", "A PDF could not be read safely.") from exc
        if source_id not in source_ids:
            raise SafeToolError(
                "UNVERIFIED_PDF_PRESENT",
                "At least one PDF is not represented by the publication-ready bundle; nothing was removed.",
            )
    if confirm_delete:
        deleted = 0
        for candidate in candidates:
            try:
                candidate.unlink()
                deleted += 1
            except OSError as exc:
                raise SafeToolError(
                    "VERIFIED_PDF_DELETE_FAILED",
                    "A verified PDF could not be removed; inspect the private input directory.",
                ) from exc
    else:
        deleted = 0
    return {
        "event": "m1_statement_cleanup_complete" if confirm_delete else "m1_statement_cleanup_dry_run",
        "verifiedPdfCount": len(candidates),
        "deletedPdfCount": deleted,
    }


def _decode_key(raw: str, *, code: str, description: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as exc:
        raise SafeToolError(code, description) from exc
    if len(decoded) < 32:
        raise SafeToolError(code, description)
    return decoded


def _fernet_from_environment() -> Fernet | None:
    raw = os.environ.get(OUTPUT_KEY_ENV)
    if not raw:
        return None
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, InvalidToken) as exc:
        raise SafeToolError(
            "OUTPUT_KEY_INVALID",
            f"{OUTPUT_KEY_ENV} is not a valid Fernet key.",
        ) from exc


def _fingerprint_key(output_root: Path, *, fernet: Fernet | None) -> bytes:
    explicit = os.environ.get(FINGERPRINT_KEY_ENV)
    if explicit:
        return _decode_key(
            explicit,
            code="FINGERPRINT_KEY_INVALID",
            description=f"{FINGERPRINT_KEY_ENV} must be URL-safe base64 containing at least 32 bytes.",
        )
    output_key = os.environ.get(OUTPUT_KEY_ENV)
    if fernet is not None and output_key:
        import hashlib

        decoded = _decode_key(
            output_key,
            code="OUTPUT_KEY_INVALID",
            description=f"{OUTPUT_KEY_ENV} is not a valid Fernet key.",
        )
        return hashlib.sha256(b"portfolio-lab:m1-account-fingerprint:v1\x00" + decoded).digest()

    key_path = output_root / ".fingerprint.key"
    if key_path.exists():
        try:
            key = key_path.read_bytes()
        except OSError as exc:
            raise SafeToolError(
                "FINGERPRINT_KEY_READ_FAILED",
                "The local private fingerprint key could not be read.",
            ) from exc
        if len(key) != 32:
            raise SafeToolError(
                "FINGERPRINT_KEY_INVALID",
                "The local private fingerprint key is invalid.",
            )
        return key
    key = secrets.token_bytes(32)
    _atomic_private_write(key_path, key)
    return key


_ACCOUNT_HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{6,62}[A-Za-z0-9])$")


def _validate_account_handle(account_handle: str) -> str:
    """Accept only an opaque handle copied from Portfolio Lab owner management.

    Handles remain case-sensitive and are never parsed as database UUIDs. Requiring
    an ASCII token with a non-numeric marker rejects common raw account numbers while
    still accepting current UUID-shaped and prefixed Portfolio Lab handles.
    """

    if (
        not isinstance(account_handle, str)
        or _ACCOUNT_HANDLE_RE.fullmatch(account_handle) is None
        or account_handle.isdecimal()
    ):
        raise SafeToolError(
            "ACCOUNT_HANDLE_INVALID",
            "The account handle must be copied exactly from Portfolio Lab owner management; brokerage account numbers are not accepted.",
        )
    return account_handle


def _read_statements(
    input_dir: Path,
    *,
    account_handle: str,
    fingerprint_key: bytes,
) -> tuple[list[ParsedStatement], int]:
    if not input_dir.is_dir():
        raise SafeToolError("INPUT_DIRECTORY_MISSING", "The private statement input directory does not exist.")
    candidates = sorted(candidate for candidate in input_dir.rglob("*") if candidate.suffix.lower() == ".pdf")
    if not candidates:
        raise SafeToolError("NO_PDF_FILES", "The private statement input directory contains no PDF files.")

    statements: list[ParsedStatement] = []
    seen_sources: set[str] = set()
    duplicate_count = 0
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            raise SafeToolError("UNSAFE_INPUT_FILE", "The statement directory contains an unsafe PDF entry.")
        try:
            payload = candidate.read_bytes()
        except OSError as exc:
            raise SafeToolError("PDF_READ_FAILED", "A PDF could not be read safely.") from exc
        source_id = sha256_source(payload)
        if source_id in seen_sources:
            duplicate_count += 1
            continue
        seen_sources.add(source_id)
        statements.append(
            parse_statement(payload, account_handle=account_handle, fingerprint_key=fingerprint_key)
        )
    return statements, duplicate_count


def _camel_issue(issue: ValidationIssue) -> dict[str, Any]:
    return {
        "code": issue.code,
        "severity": issue.severity,
        "sourceId": issue.source_id,
        "date": issue.date,
    }


def _source_row(statement: ParsedStatement) -> dict[str, Any]:
    source = statement.source
    return {
        "sourceId": source.source_id,
        "provider": source.provider,
        "parserVersion": source.parser_version,
        "pageCount": source.page_count,
        "statementStart": source.statement_start,
        "statementEnd": source.statement_end,
        "accountFingerprint": source.account_fingerprint,
    }


def _anchor_row(statement: ParsedStatement) -> dict[str, Any]:
    anchor = statement.anchor
    return {
        "accountHandle": anchor.account_handle,
        "sourceId": anchor.source_id,
        "date": anchor.date,
        "currency": anchor.currency,
        "netLiquidationValue": decimal_text(anchor.net_liquidation_value),
        "cashBalance": decimal_text(anchor.cash_balance),
        "marginDebitBalance": decimal_text(anchor.margin_debit_balance),
        "signedCashBalance": decimal_text(anchor.signed_cash_balance),
        "quality": anchor.quality,
    }


def _flow_rows(statements: Iterable[ParsedStatement]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for statement in statements:
        for flow in statement.flows:
            rows.append(
                {
                    "accountHandle": flow.account_handle,
                    "sourceId": flow.source_id,
                    "sequence": flow.sequence,
                    "date": flow.date,
                    "kind": flow.kind,
                    "amount": decimal_text(flow.amount),
                    "currency": flow.currency,
                    "valuationStatus": flow.valuation_status,
                    "evidence": flow.evidence,
                }
            )
    return rows


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if value is None else value for key, value in row.items()})
    return stream.getvalue().encode("utf-8")


def build_private_bundle(
    *,
    input_dir: Path,
    output_dir: Path,
    account_handle: str,
    output_mode: str,
    reconciliation_path: Path | None,
    allow_gaps: bool,
    expected_start: str | None,
    expected_end: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a normalized private bundle and return only a safe console summary."""

    assert_private_location(input_dir, role="input")
    assert_private_location(output_dir, role="output")
    if reconciliation_path is not None:
        assert_private_location(reconciliation_path, role="reconciliation")
    validated_account_handle = _validate_account_handle(account_handle)
    _secure_directory(output_dir)

    fernet = _fernet_from_environment()
    if output_mode not in {"auto", "encrypted", "private"}:
        raise SafeToolError("OUTPUT_MODE_INVALID", "The output mode must be auto, encrypted, or private.")
    resolved_mode = "encrypted" if (output_mode == "encrypted" or (output_mode == "auto" and fernet)) else "private"
    if resolved_mode == "encrypted" and fernet is None:
        raise SafeToolError(
            "OUTPUT_KEY_MISSING",
            f"Encrypted output requires {OUTPUT_KEY_ENV}.",
        )

    fingerprint_key = _fingerprint_key(output_dir, fernet=fernet)
    statements, duplicate_count = _read_statements(
        input_dir,
        account_handle=validated_account_handle,
        fingerprint_key=fingerprint_key,
    )
    all_source_statements = statements
    statements, validation_issues, coverage = validate_statement_set(
        statements,
        allow_gaps=allow_gaps,
        expected_start=expected_start,
        expected_end=expected_end,
    )
    reconciliation = reconcile_with_private_export(
        statements,
        account_handle=validated_account_handle,
        reconciliation_path=reconciliation_path,
    )
    errors = sum(issue.severity == "error" for issue in validation_issues)
    warnings = sum(issue.severity == "warning" for issue in validation_issues)
    import_ready = errors == 0
    publication_ready = import_ready and reconciliation["status"] == "passed"
    batch_id = str(uuid.uuid4())
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_rows = [_source_row(statement) for statement in all_source_statements]
    anchor_rows = [_anchor_row(statement) for statement in statements]
    flow_rows = _flow_rows(statements)
    validation = {
        "importReady": import_ready,
        "publicationReady": publication_ready,
        "errorCount": errors,
        "warningCount": warnings,
        "duplicateSourceCount": duplicate_count,
        "coverage": coverage,
        "issues": [_camel_issue(issue) for issue in validation_issues],
        "reconciliation": reconciliation,
    }
    bundle = {
        "schemaVersion": SCHEMA_VERSION,
        "batchId": batch_id,
        "generatedAt": generated_at,
        "accountHandle": validated_account_handle,
        "sources": source_rows,
        "anchors": anchor_rows,
        "externalFlows": flow_rows,
        "validation": validation,
    }
    serialized = json.dumps(bundle, indent=2, sort_keys=True).encode("utf-8") + b"\n"

    if resolved_mode == "encrypted":
        assert fernet is not None
        output_path = output_dir / f"m1-backfill-{batch_id}.json.fernet"
        _atomic_private_write(output_path, fernet.encrypt(serialized))
        artifact_count = 1
    else:
        batch_dir = output_dir / f"m1-backfill-{batch_id}"
        _secure_directory(batch_dir)
        _atomic_private_write(batch_dir / "normalized.private.json", serialized)
        _atomic_private_write(
            batch_dir / "sources.private.csv",
            _csv_bytes(
                source_rows,
                [
                    "sourceId",
                    "provider",
                    "parserVersion",
                    "pageCount",
                    "statementStart",
                    "statementEnd",
                    "accountFingerprint",
                ],
            ),
        )
        _atomic_private_write(
            batch_dir / "anchors.private.csv",
            _csv_bytes(
                anchor_rows,
                [
                    "accountHandle",
                    "sourceId",
                    "date",
                    "currency",
                    "netLiquidationValue",
                    "cashBalance",
                    "marginDebitBalance",
                    "signedCashBalance",
                    "quality",
                ],
            ),
        )
        _atomic_private_write(
            batch_dir / "external-flows.private.csv",
            _csv_bytes(
                flow_rows,
                [
                    "accountHandle",
                    "sourceId",
                    "sequence",
                    "date",
                    "kind",
                    "amount",
                    "currency",
                    "valuationStatus",
                    "evidence",
                ],
            ),
        )
        _atomic_private_write(
            batch_dir / "validation.private.json",
            json.dumps(validation, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        artifact_count = 5

    safe_summary = {
        "event": "m1_statement_backfill_complete",
        "batchId": batch_id,
        "outputMode": resolved_mode,
        "artifactCount": artifact_count,
        "statementCount": len(statements),
        "sourceDocumentCount": len(all_source_statements),
        "externalFlowCount": len(flow_rows),
        "duplicateSourceCount": duplicate_count,
        "errorCount": errors,
        "warningCount": warnings,
        "importReady": import_ready,
        "publicationReady": publication_ready,
        "reconciliationStatus": reconciliation["status"],
    }
    return bundle, safe_summary
