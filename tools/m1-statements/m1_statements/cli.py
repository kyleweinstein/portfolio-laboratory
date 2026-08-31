from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .bundle import build_private_bundle, cleanup_verified_pdfs, create_output_key_file
from .errors import UNEXPECTED_ERROR_MESSAGE, SafeToolError


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise SafeToolError("ARGUMENTS_INVALID", "The command arguments are invalid.")


def _parser() -> argparse.ArgumentParser:
    parser = _PrivateArgumentParser(
        prog="portfolio-lab-m1-statements",
        description="Create a private Portfolio Lab backfill bundle from downloaded M1/Apex PDF statements.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser("ingest", help="Extract, validate, reconcile, and normalize statements.")
    ingest.add_argument("input_dir", type=Path, help="Private directory containing downloaded PDFs.")
    ingest.add_argument("--output-dir", required=True, type=Path, help="Private output directory outside Git.")
    ingest.add_argument(
        "--account-handle",
        required=True,
        help="Opaque handle copied from Portfolio Lab owner management. Never pass a brokerage account number.",
    )
    ingest.add_argument("--mode", choices=("auto", "encrypted", "private"), default="auto")
    ingest.add_argument("--reconcile", type=Path, help="Optional private broker/Plaid reconciliation export.")
    ingest.add_argument("--allow-gaps", action="store_true", help="Downgrade missing statement months to warnings.")
    ingest.add_argument("--expected-start", help="Required coverage start in YYYY-MM-DD form.")
    ingest.add_argument("--expected-end", help="Required coverage end in YYYY-MM-DD form.")
    create_key = subparsers.add_parser("create-key", help="Create a private Fernet output key without printing it.")
    create_key.add_argument("output_file", type=Path, help="New key file outside every Git worktree.")
    cleanup = subparsers.add_parser("cleanup", help="Remove PDFs only after a publication-ready bundle verifies them.")
    cleanup.add_argument("input_dir", type=Path, help="Private statement directory outside every Git worktree.")
    cleanup.add_argument("--bundle", required=True, type=Path, help="Publication-ready private JSON/Fernet bundle.")
    cleanup.add_argument(
        "--confirm-delete",
        action="store_true",
        help="Delete verified PDFs. Without this flag, perform a dry run.",
    )
    return parser


def _safe_error(code: str, message: str) -> None:
    print(json.dumps({"event": "m1_statement_backfill_error", "code": code, "message": message}), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    try:
        args = _parser().parse_args(argv)
        if args.command == "ingest":
            _, safe_summary = build_private_bundle(
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                account_handle=args.account_handle,
                output_mode=args.mode,
                reconciliation_path=args.reconcile,
                allow_gaps=args.allow_gaps,
                expected_start=args.expected_start,
                expected_end=args.expected_end,
            )
            print(json.dumps(safe_summary, sort_keys=True))
            return 0 if safe_summary["importReady"] else 3
        if args.command == "create-key":
            create_output_key_file(args.output_file)
            print(json.dumps({"event": "m1_statement_output_key_created"}, sort_keys=True))
            return 0
        if args.command == "cleanup":
            safe_summary = cleanup_verified_pdfs(
                input_dir=args.input_dir,
                bundle_path=args.bundle,
                confirm_delete=args.confirm_delete,
            )
            print(json.dumps(safe_summary, sort_keys=True))
            return 0
        _safe_error("COMMAND_INVALID", "The requested command is not supported.")
        return 2
    except SafeToolError as exc:
        _safe_error(exc.code, exc.safe_message)
        return exc.exit_code
    except Exception:  # noqa: BLE001 - the privacy boundary must sanitize every unexpected parser failure
        _safe_error("UNEXPECTED_ERROR", UNEXPECTED_ERROR_MESSAGE)
        return 2
