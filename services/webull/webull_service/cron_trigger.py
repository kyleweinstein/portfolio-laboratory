from __future__ import annotations

import os
import sys
from collections.abc import Callable
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .scheduler import is_scheduled_sync_window

_REQUEST_TIMEOUT_SECONDS = 360


class ScheduledSyncTriggerError(RuntimeError):
    """A deliberately generic private-trigger failure."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _open_private_request(request: Request, *, timeout: int):
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def _scheduled_sync_url(service_url: str) -> str:
    candidate = service_url.strip()
    parsed = urlsplit(candidate)
    hostname = parsed.hostname or ""
    normalized_hostname = hostname.rstrip(".").lower()
    try:
        loopback = ip_address(normalized_hostname).is_loopback
    except ValueError:
        loopback = normalized_hostname == "localhost"
    private_railway_host = normalized_hostname.endswith(".railway.internal")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not (loopback or private_railway_host)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ScheduledSyncTriggerError(
            "The private Webull service URL is not configured correctly."
        )
    return f"{candidate.rstrip('/')}/v1/scheduled-sync"


def trigger_scheduled_sync(
    *,
    service_url: str,
    internal_api_token: str,
    owner_github_id: str,
    opener: Callable[..., Any] | None = None,
) -> None:
    if not internal_api_token or not owner_github_id:
        raise ScheduledSyncTriggerError(
            "The private Webull trigger configuration is incomplete."
        )

    try:
        request = Request(
            _scheduled_sync_url(service_url),
            data=b"",
            headers={
                "Authorization": f"Bearer {internal_api_token}",
                "x-portfolio-owner-github-id": owner_github_id,
            },
            method="POST",
        )
        open_request = opener if opener is not None else _open_private_request
        with open_request(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if not isinstance(status, int) or not 200 <= status < 300:
                raise ScheduledSyncTriggerError(
                    "The private Webull scheduled-sync request failed."
                )
    except ScheduledSyncTriggerError:
        raise
    except Exception:  # noqa: BLE001 - never expose network or HTTP exception details.
        raise ScheduledSyncTriggerError(
            "The private Webull scheduled-sync request failed."
        ) from None


def main() -> int:
    if not is_scheduled_sync_window():
        print(
            "Webull scheduled sync skipped outside the configured market-hours window."
        )
        return 0

    service_url = os.getenv("WEBULL_SERVICE_URL", "").strip()
    internal_api_token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    owner_github_id = os.getenv("PORTFOLIO_OWNER_GITHUB_ID", "").strip()
    try:
        trigger_scheduled_sync(
            service_url=service_url,
            internal_api_token=internal_api_token,
            owner_github_id=owner_github_id,
        )
    except ScheduledSyncTriggerError:
        print("Webull scheduled sync trigger failed.", file=sys.stderr)
        return 1

    print("Webull scheduled sync trigger completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
