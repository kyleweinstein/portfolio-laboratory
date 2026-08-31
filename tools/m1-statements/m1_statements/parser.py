from __future__ import annotations

import hashlib
import hmac
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader

from .errors import SafeToolError
from .models import (
    PARSER_VERSION,
    ExternalFlow,
    ParsedStatement,
    SourceDocument,
    StatementAnchor,
    ValidationIssue,
)

_DATE_TOKEN = r"(?:\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})(?!\d)|[A-Za-z]+\s+\d{1,2},\s+\d{4})"
_MONEY_TOKEN = r"(?:\(\s*)?-?\$?\s*\d[\d,]*\.\d{2}\s*\)?(?:\s*(?:CR|DR))?"
_PERIOD_RE = re.compile(
    rf"(?is)(?:statement\s+period|for\s+the\s+period)\s*:?\s*({_DATE_TOKEN})\s*(?:-|to|through)\s*({_DATE_TOKEN})"
)
_PERIOD_END_RE = re.compile(rf"(?is)(?:period\s+ending|statement\s+ending)\s*:?\s*({_DATE_TOKEN})")
_BARE_PERIOD_RE = re.compile(
    rf"(?im)^\s*({_DATE_TOKEN})\s*(?:-|\u2013|\u2014|to|through)\s*({_DATE_TOKEN})\s*$"
)
_ACCOUNT_RE = re.compile(
    r"(?im)^\s*(?:account\s+(?:number|no\.?|id)|acct\.?\s*(?:number|no\.?|#)?)"
    r"\s*(?:[:#.\u00ad\ufffd]+\s*)?"
    r"([A-Z0-9*X]+(?:[-\u00ad\ufffd][A-Z0-9*X]+)+|[A-Z0-9*X]{4,})"
)
_CURRENCY_RE = re.compile(r"(?im)^\s*currency\s*:?\s*([A-Z]{3})\s*$")
_ENDING_VALUE_RE = re.compile(
    rf"(?im)^\s*(?:ending\s+account\s+value|net\s+account\s+value|closing\s+account\s+value|total\s+account\s+value)\s*:?\s*({_MONEY_TOKEN})\s*$"
)
_CASH_RE = re.compile(
    rf"(?im)^\s*(?:ending\s+cash(?:\s+balance)?|cash\s+(?:balance|&\s+cash\s+equivalents|and\s+cash\s+equivalents))\s*:?\s*({_MONEY_TOKEN})\s*$"
)
_MARGIN_RE = re.compile(
    rf"(?im)^\s*(?:ending\s+)?margin\s+(?:debit\s+)?balance\s*:?\s*({_MONEY_TOKEN})\s*$"
)
_MONEY_RE = re.compile(_MONEY_TOKEN, re.IGNORECASE)
_APEX_BALANCE_HEADER_RE = re.compile(r"(?i)\bOPENING\s+BALANCE\b.*\bCLOSING\s+BALANCE\b")
_APEX_CASH_ACCOUNT_RE = re.compile(r"(?i)^\s*CASH\s+ACCOUNT\b")
_APEX_TOTAL_PRICED_PORTFOLIO_RE = re.compile(r"(?i)^\s*TOTAL\s+PRICED\s+PORTFOLIO\b")
_FLOW_DATE_RE = re.compile(r"^\s*(\d{1,2}/\d{1,2}(?:/(?:\d{2}|\d{4}))?)\b(.*)$")
_TRAILING_MONEY_RE = re.compile(rf"({_MONEY_TOKEN})\s*$", re.IGNORECASE)

_IN_PATTERNS = (
    "ach deposit",
    "cash deposit",
    "deposit",
    "contribution",
    "wire received",
    "incoming wire",
    "transfer in",
    "journal in",
    "securities received",
    "acat in",
)
_OUT_PATTERNS = (
    "ach withdrawal",
    "cash withdrawal",
    "withdrawal",
    "distribution",
    "wire sent",
    "outgoing wire",
    "transfer out",
    "journal out",
    "securities delivered",
    "acat out",
)
_TRANSFER_PATTERNS = ("transfer", "journal", "acat", "securities received", "securities delivered")
_INTERNAL_PATTERNS = ("dividend", "interest", "fee", "tax withholding", "buy", "sell", "trade")


def sha256_source(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _parse_date(token: str, *, statement_end: date | None = None) -> date:
    compact = " ".join(token.strip().split())
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(compact, fmt).date()  # noqa: DTZ007 - statements contain calendar dates
        except ValueError:
            pass
    if statement_end is not None:
        for fmt in ("%m/%d",):
            try:
                partial = datetime.strptime(compact, fmt).date()  # noqa: DTZ007 - calendar date only
                year = statement_end.year
                candidate = date(year, partial.month, partial.day)
                if candidate > statement_end and candidate.month > statement_end.month:
                    candidate = date(year - 1, partial.month, partial.day)
                return candidate
            except ValueError:
                pass
    raise SafeToolError("UNSUPPORTED_DATE", "A statement contains an unsupported date format.")


def _parse_money(token: str) -> Decimal:
    normalized = token.strip().upper()
    negative = normalized.startswith(("(", "-")) or normalized.endswith("DR")
    normalized = re.sub(r"[\s$(),]", "", normalized)
    normalized = normalized.removesuffix("CR").removesuffix("DR")
    normalized = normalized.removeprefix("-")
    try:
        value = Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise SafeToolError("UNSUPPORTED_AMOUNT", "A statement contains an unsupported amount format.") from exc
    return -value if negative else value


def _find_money(pattern: re.Pattern[str], text: str) -> Decimal | None:
    match = pattern.search(text)
    return _parse_money(match.group(1)) if match else None


def _find_apex_closing_balance(text: str, row_pattern: re.Pattern[str]) -> Decimal | None:
    """Return a closing-column value from Apex's opening/closing balance table.

    Requiring the Apex table header, a nearby exact row label, and both numeric
    columns prevents similarly named portfolio totals elsewhere in a statement
    from being mistaken for the statement anchor.
    """

    lines = text.splitlines()
    for header_index, line in enumerate(lines):
        if not _APEX_BALANCE_HEADER_RE.search(line):
            continue
        for candidate in lines[header_index + 1 : header_index + 26]:
            if not row_pattern.search(candidate):
                continue
            amounts = [match.group(0) for match in _MONEY_RE.finditer(candidate)]
            if len(amounts) < 2:
                return None
            return _parse_money(amounts[-1])
    return None


def _detect_provider(text: str) -> str:
    upper = text.upper()
    if "APEX CLEARING" in upper:
        return "apex"
    if "M1 FINANCE" in upper or re.search(r"(?m)^\s*M1\s+(?:INVEST|BROKERAGE|ACCOUNT)", upper):
        return "m1"
    raise SafeToolError(
        "UNSUPPORTED_STATEMENT_PROVIDER",
        "A PDF was not recognized as an M1- or Apex-issued brokerage statement.",
    )


def _extract_period(text: str) -> tuple[date, date]:
    match = _PERIOD_RE.search(text)
    if match:
        start = _parse_date(match.group(1))
        end = _parse_date(match.group(2))
    else:
        match = _BARE_PERIOD_RE.search(text)
        if match:
            start = _parse_date(match.group(1))
            end = _parse_date(match.group(2))
        else:
            match = _PERIOD_END_RE.search(text)
            if not match:
                raise SafeToolError("STATEMENT_PERIOD_MISSING", "A statement period could not be identified.")
            end = _parse_date(match.group(1))
            start = end.replace(day=1)
    if start > end:
        raise SafeToolError("STATEMENT_PERIOD_INVALID", "A statement period has an invalid date order.")
    return start, end


def _extract_account_fingerprint(text: str, key: bytes) -> str:
    match = _ACCOUNT_RE.search(text)
    if not match:
        raise SafeToolError("ACCOUNT_IDENTIFIER_MISSING", "A statement account identifier could not be identified.")
    unmasked = re.sub(r"[^A-Z0-9]", "", match.group(1).upper().replace("X", "").replace("*", ""))
    if len(unmasked) < 4:
        raise SafeToolError(
            "ACCOUNT_IDENTIFIER_INSUFFICIENT",
            "A statement account identifier does not contain enough unmasked characters for private matching.",
        )
    private_match_token = unmasked[-4:]
    digest = hmac.new(key, private_match_token.encode("utf-8"), hashlib.sha256).hexdigest()
    return "hmac-sha256:" + digest


def _extract_currency(text: str) -> str:
    match = _CURRENCY_RE.search(text)
    if match and match.group(1).upper() != "USD":
        raise SafeToolError("UNSUPPORTED_CURRENCY", "Only USD brokerage statements can be imported.")
    return "USD"


def _flow_kind(description: str) -> tuple[str, int] | None:
    lower = " ".join(description.lower().split())
    if any(token in lower for token in _INTERNAL_PATTERNS):
        return None
    in_match = next((token for token in _IN_PATTERNS if token in lower), None)
    out_match = next((token for token in _OUT_PATTERNS if token in lower), None)
    if in_match and not out_match:
        if any(token in lower for token in _TRANSFER_PATTERNS):
            return "security_transfer_in", 1
        return "deposit", 1
    if out_match and not in_match:
        if any(token in lower for token in _TRANSFER_PATTERNS):
            return "security_transfer_out", -1
        return "withdrawal", -1
    if any(token in lower for token in _TRANSFER_PATTERNS):
        return "security_transfer_unknown", 0
    return None


def _extract_flows(
    text: str,
    *,
    account_handle: str,
    source_id: str,
    statement_start: date,
    statement_end: date,
) -> tuple[list[ExternalFlow], list[ValidationIssue]]:
    flows: list[ExternalFlow] = []
    issues: list[ValidationIssue] = []
    for line in text.splitlines():
        date_match = _FLOW_DATE_RE.match(line)
        if not date_match:
            continue
        description = date_match.group(2)
        classification = _flow_kind(description)
        if classification is None:
            continue
        occurred_on = _parse_date(date_match.group(1), statement_end=statement_end)
        kind, sign = classification
        amount_match = _TRAILING_MONEY_RE.search(description)
        amount = _parse_money(amount_match.group(1)) if amount_match else None
        if amount is not None and sign:
            amount = abs(amount) * sign
        if kind == "security_transfer_unknown":
            issues.append(
                ValidationIssue(
                    code="AMBIGUOUS_SECURITY_TRANSFER",
                    severity="error",
                    source_id=source_id,
                    date=occurred_on.isoformat(),
                )
            )
        if amount is None:
            issues.append(
                ValidationIssue(
                    code="EXTERNAL_FLOW_VALUE_UNAVAILABLE",
                    severity="error",
                    source_id=source_id,
                    date=occurred_on.isoformat(),
                )
            )
        if occurred_on < statement_start or occurred_on > statement_end:
            issues.append(
                ValidationIssue(
                    code="EXTERNAL_FLOW_OUTSIDE_PERIOD",
                    severity="error",
                    source_id=source_id,
                    date=occurred_on.isoformat(),
                )
            )
        flows.append(
            ExternalFlow(
                account_handle=account_handle,
                source_id=source_id,
                sequence=len(flows) + 1,
                date=occurred_on.isoformat(),
                kind=kind,
                amount=amount,
                currency="USD",
                valuation_status="reported" if amount is not None else "unavailable",
            )
        )
    return flows, issues


def parse_statement(
    payload: bytes,
    *,
    account_handle: str,
    fingerprint_key: bytes,
) -> ParsedStatement:
    """Parse one statement without exposing source text or raw values in errors."""

    source_id = sha256_source(payload)
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        if reader.is_encrypted:
            raise SafeToolError("ENCRYPTED_PDF", "An encrypted statement must be unlocked before import.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except SafeToolError:
        raise
    except Exception as exc:
        raise SafeToolError("PDF_READ_FAILED", "A PDF could not be read safely.") from exc

    text = "\n".join(pages)
    if not text.strip():
        raise SafeToolError(
            "PDF_TEXT_MISSING",
            "A statement has no extractable text. OCR it locally before importing.",
        )

    provider = _detect_provider(text)
    statement_start, statement_end = _extract_period(text)
    currency = _extract_currency(text)
    account_fingerprint = _extract_account_fingerprint(text, fingerprint_key)
    ending_value = _find_money(_ENDING_VALUE_RE, text)
    if ending_value is None and provider == "apex":
        ending_value = _find_apex_closing_balance(text, _APEX_TOTAL_PRICED_PORTFOLIO_RE)
    if ending_value is None:
        raise SafeToolError("ENDING_VALUE_MISSING", "A statement ending account value could not be identified.")

    cash_balance = _find_money(_CASH_RE, text)
    if cash_balance is None and provider == "apex":
        cash_balance = _find_apex_closing_balance(text, _APEX_CASH_ACCOUNT_RE)
    margin_debit = _find_money(_MARGIN_RE, text)
    if margin_debit is not None:
        margin_debit = abs(margin_debit)
    if cash_balance is None and margin_debit is None:
        signed_cash = None
    else:
        signed_cash = (cash_balance or Decimal("0.00")) - (margin_debit or Decimal("0.00"))

    source = SourceDocument(
        source_id=source_id,
        provider=provider,  # type: ignore[arg-type]
        parser_version=PARSER_VERSION,
        page_count=len(pages),
        statement_start=statement_start.isoformat(),
        statement_end=statement_end.isoformat(),
        account_fingerprint=account_fingerprint,
    )
    anchor = StatementAnchor(
        account_handle=account_handle,
        source_id=source_id,
        date=statement_end.isoformat(),
        currency=currency,
        net_liquidation_value=ending_value,
        cash_balance=cash_balance,
        margin_debit_balance=margin_debit,
        signed_cash_balance=signed_cash,
    )
    flows, issues = _extract_flows(
        text,
        account_handle=account_handle,
        source_id=source_id,
        statement_start=statement_start,
        statement_end=statement_end,
    )
    if signed_cash is None:
        issues.append(
            ValidationIssue(
                code="SIGNED_CASH_UNAVAILABLE",
                severity="warning",
                source_id=source_id,
                date=statement_end.isoformat(),
            )
        )
    if ending_value <= 0:
        issues.append(
            ValidationIssue(
                code="NONPOSITIVE_ENDING_VALUE",
                severity="warning",
                source_id=source_id,
                date=statement_end.isoformat(),
            )
        )
    return ParsedStatement(source=source, anchor=anchor, flows=flows, issues=issues)
