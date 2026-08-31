from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import (
    SYNTHETIC_ACCOUNT_HANDLE,
    apex_lines,
    apex_opening_closing_layout_lines,
    m1_lines,
    write_synthetic_pdf,
)

from m1_statements.errors import SafeToolError
from m1_statements.parser import parse_statement


def test_parses_m1_signed_margin_cash_and_only_external_flows(tmp_path):
    statement_path = write_synthetic_pdf(tmp_path / "statement.pdf", m1_lines())
    parsed = parse_statement(
        statement_path.read_bytes(),
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        fingerprint_key=b"f" * 32,
    )

    assert parsed.source.provider == "m1"
    assert parsed.source.statement_end == "2026-01-31"
    assert parsed.anchor.account_handle == SYNTHETIC_ACCOUNT_HANDLE
    assert parsed.anchor.net_liquidation_value == Decimal("125000.00")
    assert parsed.anchor.cash_balance == Decimal("2000.00")
    assert parsed.anchor.margin_debit_balance == Decimal("7000.00")
    assert parsed.anchor.signed_cash_balance == Decimal("-5000.00")
    assert [(flow.kind, flow.amount) for flow in parsed.flows] == [
        ("deposit", Decimal("10000.00")),
        ("withdrawal", Decimal("-1500.00")),
    ]


def test_parses_apex_directional_security_transfer(tmp_path):
    statement_path = write_synthetic_pdf(tmp_path / "statement.pdf", apex_lines())
    parsed = parse_statement(
        statement_path.read_bytes(),
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        fingerprint_key=b"f" * 32,
    )

    assert parsed.source.provider == "apex"
    assert parsed.anchor.signed_cash_balance == Decimal("2500.00")
    assert len(parsed.flows) == 1
    assert parsed.flows[0].account_handle == SYNTHETIC_ACCOUNT_HANDLE
    assert parsed.flows[0].kind == "security_transfer_in"
    assert parsed.flows[0].amount == Decimal("2000.00")


def test_full_and_masked_account_identifiers_share_only_a_private_fingerprint(tmp_path):
    m1_path = write_synthetic_pdf(tmp_path / "m1.pdf", m1_lines())
    apex_path = write_synthetic_pdf(tmp_path / "apex.pdf", apex_lines())
    key = b"f" * 32
    m1 = parse_statement(m1_path.read_bytes(), account_handle=SYNTHETIC_ACCOUNT_HANDLE, fingerprint_key=key)
    apex = parse_statement(apex_path.read_bytes(), account_handle=SYNTHETIC_ACCOUNT_HANDLE, fingerprint_key=key)

    assert m1.source.account_fingerprint == apex.source.account_fingerprint
    assert m1.source.account_fingerprint.startswith("hmac-sha256:")
    assert "0001" not in m1.source.account_fingerprint


def test_parses_current_apex_opening_closing_layout_and_uses_closing_column(tmp_path):
    statement_path = write_synthetic_pdf(tmp_path / "statement.pdf", apex_opening_closing_layout_lines())
    parsed = parse_statement(
        statement_path.read_bytes(),
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        fingerprint_key=b"f" * 32,
    )

    assert parsed.source.provider == "apex"
    assert parsed.source.statement_start == "2026-07-01"
    assert parsed.source.statement_end == "2026-07-31"
    assert parsed.anchor.net_liquidation_value == Decimal("132500.00")
    assert parsed.anchor.cash_balance == Decimal("2500.00")
    assert parsed.anchor.signed_cash_balance == Decimal("2500.00")


def test_apex_opening_closing_fallback_requires_two_columns(tmp_path):
    lines = apex_opening_closing_layout_lines()
    lines[5] = "Cash account                         $2,500.00"
    lines[6] = "TOTAL PRICED PORTFOLIO             $132,500.00"
    statement_path = write_synthetic_pdf(tmp_path / "statement.pdf", lines)

    with pytest.raises(SafeToolError) as error:
        parse_statement(
            statement_path.read_bytes(),
            account_handle=SYNTHETIC_ACCOUNT_HANDLE,
            fingerprint_key=b"f" * 32,
        )

    assert error.value.code == "ENDING_VALUE_MISSING"


def test_apex_balance_table_fallback_is_not_applied_to_m1_provider(tmp_path):
    lines = apex_opening_closing_layout_lines()
    lines[0] = "M1 FINANCE LLC"
    statement_path = write_synthetic_pdf(tmp_path / "statement.pdf", lines)

    with pytest.raises(SafeToolError) as error:
        parse_statement(
            statement_path.read_bytes(),
            account_handle=SYNTHETIC_ACCOUNT_HANDLE,
            fingerprint_key=b"f" * 32,
        )

    assert error.value.code == "ENDING_VALUE_MISSING"
