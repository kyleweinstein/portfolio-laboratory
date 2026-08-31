from __future__ import annotations

from conftest import SYNTHETIC_ACCOUNT_HANDLE, write_synthetic_pdf

from m1_statements.cli import main


def test_cli_error_never_echoes_source_path_account_or_amount(tmp_path, capsys):
    input_dir = tmp_path / "private-account-folder"
    write_synthetic_pdf(
        input_dir / "statement-with-private-name.pdf",
        [
            "UNKNOWN BROKER",
            "SYNTHETIC TEST STATEMENT - NOT A REAL ACCOUNT",
            "Statement Period: 03/01/2026 - 03/31/2026",
            "Account Number: DO-NOT-ECHO-9999",
            "Ending Account Value: $987,654.32",
        ],
    )

    exit_code = main(
        [
            "ingest",
            str(input_dir),
            "--output-dir",
            str(tmp_path / "output"),
            "--account-handle",
            SYNTHETIC_ACCOUNT_HANDLE,
            "--mode",
            "private",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "UNSUPPORTED_STATEMENT_PROVIDER" in captured.err
    assert "DO-NOT-ECHO" not in captured.err
    assert "987,654.32" not in captured.err
    assert "statement-with-private-name" not in captured.err
    assert captured.out == ""


def test_legacy_account_ref_argument_cannot_echo_its_value(tmp_path, capsys):
    raw_value = "BROKER-ACCOUNT-DO-NOT-ECHO-1234"
    exit_code = main(
        [
            "ingest",
            str(tmp_path / "private-input"),
            "--output-dir",
            str(tmp_path / "private-output"),
            "--account-ref",
            raw_value,
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "ARGUMENTS_INVALID" in captured.err
    assert raw_value not in captured.err
    assert str(tmp_path) not in captured.err
    assert captured.out == ""
