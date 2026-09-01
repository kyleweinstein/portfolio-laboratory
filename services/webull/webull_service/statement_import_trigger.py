from __future__ import annotations

import base64
import binascii
import json
import os
import sys
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .cron_trigger import (
    ScheduledSyncTriggerError,
    _open_private_request,
    _scheduled_sync_url,
)

_REQUEST_TIMEOUT_SECONDS = 60
_SAFE_STATEMENT_IMPORT_CODES = frozenset(
    {
        "STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE",
        "STATEMENT_IMPORT_AUTH_INVALID",
        "STATEMENT_IMPORT_BUNDLE_TOO_LARGE",
        "STATEMENT_IMPORT_DECRYPT_FAILED",
        "STATEMENT_IMPORT_INTEGRITY_FAILED",
        "STATEMENT_IMPORT_JSON_INVALID",
        "STATEMENT_IMPORT_KEY_INVALID",
        "STATEMENT_IMPORT_NOT_READY",
        "STATEMENT_IMPORT_PERSIST_FAILED",
        "STATEMENT_IMPORT_SCHEMA_INVALID",
    }
)


class StatementImportTriggerError(RuntimeError):
    """A deliberately generic private-import failure."""

    def __init__(self, category: str) -> None:
        super().__init__("The private statement import request failed.")
        self.category = category


def _http_error_category(exc: HTTPError) -> str:
    status_category = f"http_{exc.code}" if 100 <= exc.code <= 599 else "http_error"
    if exc.code != 422:
        return status_category
    try:
        decoded = json.loads(exc.read(4096))
    except (AttributeError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return status_category
    code = decoded.get("code") if isinstance(decoded, dict) else None
    if not isinstance(code, str) or code not in _SAFE_STATEMENT_IMPORT_CODES:
        return status_category
    return f"{status_category}_{code.lower()}"


def _statement_import_url(service_url: str) -> str:
    try:
        scheduled_url = _scheduled_sync_url(service_url)
    except ScheduledSyncTriggerError:
        raise StatementImportTriggerError("configuration") from None
    return scheduled_url.removesuffix("/v1/scheduled-sync") + "/v1/statement-imports"


def trigger_statement_import(
    *,
    service_url: str,
    internal_api_token: str,
    owner_github_id: str,
    encrypted_payload_base64: str,
    opener: Callable[..., Any] | None = None,
) -> None:
    if not internal_api_token or not owner_github_id or not encrypted_payload_base64:
        raise StatementImportTriggerError("configuration")
    try:
        payload = base64.b64decode(encrypted_payload_base64, validate=True)
    except (binascii.Error, ValueError):
        raise StatementImportTriggerError("payload") from None
    if not payload or len(payload) > 8 * 1024 * 1024:
        raise StatementImportTriggerError("payload")

    request = Request(
        _statement_import_url(service_url),
        data=payload,
        headers={
            "Authorization": f"Bearer {internal_api_token}",
            "x-portfolio-owner-github-id": owner_github_id,
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    try:
        open_request = opener if opener is not None else _open_private_request
        with open_request(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if not isinstance(status, int) or not 200 <= status < 300:
                category = f"http_{status}" if isinstance(status, int) else "http_error"
                raise StatementImportTriggerError(category)
    except StatementImportTriggerError:
        raise
    except HTTPError as exc:
        raise StatementImportTriggerError(_http_error_category(exc)) from None
    except TimeoutError:
        raise StatementImportTriggerError("timeout") from None
    except URLError as exc:
        category = "timeout" if isinstance(exc.reason, TimeoutError) else "network"
        raise StatementImportTriggerError(category) from None
    except Exception:  # noqa: BLE001 - never expose network or payload details.
        raise StatementImportTriggerError("network") from None


def main() -> int:
    try:
        trigger_statement_import(
            service_url=os.getenv("WEBULL_SERVICE_URL", "").strip(),
            internal_api_token=os.getenv("INTERNAL_API_TOKEN", "").strip(),
            owner_github_id=os.getenv("PORTFOLIO_OWNER_GITHUB_ID", "").strip(),
            encrypted_payload_base64=os.getenv(
                "BROKER_STATEMENT_IMPORT_PAYLOAD_B64", ""
            ).strip(),
        )
    except StatementImportTriggerError as exc:
        print(
            f"Private statement import failed ({exc.category}).",
            file=sys.stderr,
        )
        return 1
    print("Private statement import completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
