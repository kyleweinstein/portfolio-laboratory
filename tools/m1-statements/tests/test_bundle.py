from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from conftest import SYNTHETIC_ACCOUNT_HANDLE, apex_lines, m1_lines, write_synthetic_pdf
from cryptography.fernet import Fernet

from m1_statements.bundle import (
    OUTPUT_KEY_ENV,
    assert_private_location,
    build_private_bundle,
    cleanup_verified_pdfs,
)
from m1_statements.errors import SafeToolError


def _reconciliation_file(path: Path, *, mismatch: bool = False) -> Path:
    payload = {
        "schemaVersion": "portfolio-lab.broker-reconciliation.v2",
        "accountHandle": SYNTHETIC_ACCOUNT_HANDLE,
        "snapshots": [
            {
                "date": "2026-01-31",
                "netLiquidationValue": "125001.00" if mismatch else "125000.00",
                "signedCashBalance": "-5000.00",
            },
            {
                "date": "2026-02-28",
                "netLiquidationValue": "132500.00",
                "signedCashBalance": "2500.00",
            },
        ],
        "externalFlows": [
            {"date": "2026-01-08", "kind": "deposit", "amount": "10000.00"},
            {"date": "2026-01-19", "kind": "withdrawal", "amount": "-1500.00"},
            {"date": "2026-02-10", "kind": "security_transfer_in", "amount": "2000.00"},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _two_statement_input(path: Path) -> Path:
    write_synthetic_pdf(path / "one.pdf", m1_lines())
    write_synthetic_pdf(path / "two.pdf", apex_lines())
    return path


def test_private_bundle_writes_json_csv_and_reconciles(tmp_path, monkeypatch):
    monkeypatch.delenv(OUTPUT_KEY_ENV, raising=False)
    input_dir = _two_statement_input(tmp_path / "input")
    reconciliation = _reconciliation_file(tmp_path / "reconciliation.json")
    output_dir = tmp_path / "output"

    bundle, summary = build_private_bundle(
        input_dir=input_dir,
        output_dir=output_dir,
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        output_mode="private",
        reconciliation_path=reconciliation,
        allow_gaps=False,
        expected_start="2026-01-01",
        expected_end="2026-02-28",
    )

    assert summary["importReady"] is True
    assert summary["publicationReady"] is True
    assert summary["reconciliationStatus"] == "passed"
    assert bundle["schemaVersion"] == "portfolio-lab.m1-statement-backfill.v2"
    assert bundle["accountHandle"] == SYNTHETIC_ACCOUNT_HANDLE
    assert all(row["accountHandle"] == SYNTHETIC_ACCOUNT_HANDLE for row in bundle["anchors"])
    assert all(row["accountHandle"] == SYNTHETIC_ACCOUNT_HANDLE for row in bundle["externalFlows"])
    assert "accountRef" not in json.dumps(bundle)
    assert bundle["anchors"][0]["signedCashBalance"] == "-5000.00"
    assert bundle["validation"]["reconciliation"]["matchedAnchors"] == 2
    batch_dirs = [entry for entry in output_dir.iterdir() if entry.is_dir()]
    assert len(batch_dirs) == 1
    assert {item.name for item in batch_dirs[0].iterdir()} == {
        "normalized.private.json",
        "sources.private.csv",
        "anchors.private.csv",
        "external-flows.private.csv",
        "validation.private.json",
    }
    if os.name != "nt" and hasattr(stat, "S_IMODE"):
        for item in batch_dirs[0].iterdir():
            assert stat.S_IMODE(item.stat().st_mode) & 0o077 == 0


@pytest.mark.parametrize(
    "account_handle",
    [
        "12345678",
        "short",
        " leading-token",
        "trailing-token_",
        "broker.account.1234",
        "a" * 65,
    ],
)
def test_invalid_or_account_number_like_handle_is_rejected_without_echo(
    tmp_path,
    account_handle,
):
    with pytest.raises(SafeToolError) as exc_info:
        build_private_bundle(
            input_dir=tmp_path / "input",
            output_dir=tmp_path / "output",
            account_handle=account_handle,
            output_mode="private",
            reconciliation_path=None,
            allow_gaps=False,
            expected_start=None,
            expected_end=None,
        )

    assert exc_info.value.code == "ACCOUNT_HANDLE_INVALID"
    assert account_handle not in exc_info.value.safe_message
    assert not (tmp_path / "output").exists()


def test_exact_duplicate_pdf_is_hashed_and_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv(OUTPUT_KEY_ENV, raising=False)
    input_dir = tmp_path / "input"
    original = write_synthetic_pdf(input_dir / "one.pdf", m1_lines())
    (input_dir / "copy.pdf").write_bytes(original.read_bytes())

    bundle, summary = build_private_bundle(
        input_dir=input_dir,
        output_dir=tmp_path / "output",
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        output_mode="private",
        reconciliation_path=None,
        allow_gaps=False,
        expected_start=None,
        expected_end=None,
    )

    assert len(bundle["sources"]) == 1
    assert summary["duplicateSourceCount"] == 1
    assert summary["publicationReady"] is False


def test_encrypted_mode_leaves_no_plaintext_bundle(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(OUTPUT_KEY_ENV, key.decode("ascii"))
    input_dir = tmp_path / "input"
    write_synthetic_pdf(input_dir / "one.pdf", m1_lines())
    output_dir = tmp_path / "output"

    bundle, summary = build_private_bundle(
        input_dir=input_dir,
        output_dir=output_dir,
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        output_mode="encrypted",
        reconciliation_path=None,
        allow_gaps=False,
        expected_start=None,
        expected_end=None,
    )

    artifacts = list(output_dir.iterdir())
    assert len(artifacts) == 1
    assert artifacts[0].suffix == ".fernet"
    ciphertext = artifacts[0].read_bytes()
    assert b"125000.00" not in ciphertext
    decrypted = json.loads(Fernet(key).decrypt(ciphertext))
    assert decrypted["batchId"] == bundle["batchId"]
    assert summary["outputMode"] == "encrypted"


def test_reconciliation_report_never_echoes_mismatched_amounts(tmp_path, monkeypatch):
    monkeypatch.delenv(OUTPUT_KEY_ENV, raising=False)
    input_dir = _two_statement_input(tmp_path / "input")
    reconciliation = _reconciliation_file(tmp_path / "reconciliation.json", mismatch=True)

    bundle, summary = build_private_bundle(
        input_dir=input_dir,
        output_dir=tmp_path / "output",
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        output_mode="private",
        reconciliation_path=reconciliation,
        allow_gaps=False,
        expected_start=None,
        expected_end=None,
    )

    report_text = json.dumps(bundle["validation"])
    assert summary["reconciliationStatus"] == "failed"
    assert bundle["validation"]["publicationReady"] is False
    assert "125001.00" not in report_text
    assert "125000.00" not in report_text
    assert "RECONCILIATION_ANCHOR_MISMATCH" in report_text


def test_reconciliation_must_use_the_exact_same_opaque_handle(tmp_path, monkeypatch):
    monkeypatch.delenv(OUTPUT_KEY_ENV, raising=False)
    input_dir = _two_statement_input(tmp_path / "input")
    reconciliation = _reconciliation_file(tmp_path / "reconciliation.json")
    private_payload = json.loads(reconciliation.read_text(encoding="utf-8"))
    private_payload["accountHandle"] = "plh_other_owner_handle_0002"
    reconciliation.write_text(json.dumps(private_payload), encoding="utf-8")

    with pytest.raises(SafeToolError) as exc_info:
        build_private_bundle(
            input_dir=input_dir,
            output_dir=tmp_path / "output",
            account_handle=SYNTHETIC_ACCOUNT_HANDLE,
            output_mode="private",
            reconciliation_path=reconciliation,
            allow_gaps=False,
            expected_start=None,
            expected_end=None,
        )

    assert exc_info.value.code == "RECONCILIATION_ACCOUNT_MISMATCH"
    assert SYNTHETIC_ACCOUNT_HANDLE not in exc_info.value.safe_message
    assert private_payload["accountHandle"] not in exc_info.value.safe_message


def test_git_worktree_locations_are_rejected_without_writing():
    with pytest.raises(SafeToolError) as exc_info:
        assert_private_location(Path.cwd(), role="output")
    assert exc_info.value.code == "OUTPUT_INSIDE_GIT_WORKTREE"


def test_cleanup_is_dry_run_then_deletes_only_verified_pdfs(tmp_path, monkeypatch):
    monkeypatch.delenv(OUTPUT_KEY_ENV, raising=False)
    input_dir = _two_statement_input(tmp_path / "input")
    reconciliation = _reconciliation_file(tmp_path / "reconciliation.json")
    output_dir = tmp_path / "output"
    _, summary = build_private_bundle(
        input_dir=input_dir,
        output_dir=output_dir,
        account_handle=SYNTHETIC_ACCOUNT_HANDLE,
        output_mode="private",
        reconciliation_path=reconciliation,
        allow_gaps=False,
        expected_start=None,
        expected_end=None,
    )
    assert summary["publicationReady"] is True
    bundle_path = next(output_dir.glob("m1-backfill-*/normalized.private.json"))

    dry_run = cleanup_verified_pdfs(input_dir=input_dir, bundle_path=bundle_path, confirm_delete=False)
    assert dry_run["verifiedPdfCount"] == 2
    assert dry_run["deletedPdfCount"] == 0
    assert len(list(input_dir.glob("*.pdf"))) == 2

    result = cleanup_verified_pdfs(input_dir=input_dir, bundle_path=bundle_path, confirm_delete=True)
    assert result["deletedPdfCount"] == 2
    assert list(input_dir.glob("*.pdf")) == []
