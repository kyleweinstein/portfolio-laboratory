from urllib.error import HTTPError

import pytest

from webull_service import cron_trigger


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_trigger_posts_to_private_service_with_proxy_identity() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    cron_trigger.trigger_scheduled_sync(
        service_url="http://webull-api.railway.internal:8000/",
        internal_api_token="private-test-token",
        owner_github_id="12345",
        opener=opener,
    )

    request = captured["request"]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert request.full_url == (
        "http://webull-api.railway.internal:8000/v1/scheduled-sync"
    )
    assert request.method == "POST"
    assert request.data == b""
    assert headers["authorization"] == "Bearer private-test-token"
    assert headers["x-portfolio-owner-github-id"] == "12345"
    assert captured["timeout"] == 360


@pytest.mark.parametrize(
    "service_url",
    [
        "https://attacker.example.test",
        "https://webull-api.railway.internal/unexpected-path",
    ],
)
def test_trigger_rejects_non_private_or_non_root_destinations_before_network(
    service_url,
) -> None:
    called = False

    def opener(*_args, **_kwargs):
        nonlocal called
        called = True
        return _Response()

    with pytest.raises(cron_trigger.ScheduledSyncTriggerError) as caught:
        cron_trigger.trigger_scheduled_sync(
            service_url=service_url,
            internal_api_token="must-not-leave-private-network",
            owner_github_id="12345",
            opener=opener,
        )

    assert called is False
    assert "must-not-leave-private-network" not in str(caught.value)


def test_main_skips_outside_market_window_without_network(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cron_trigger, "is_scheduled_sync_window", lambda: False)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("network must not be called outside the sync window")

    monkeypatch.setattr(cron_trigger, "_open_private_request", fail_if_called)

    assert cron_trigger.main() == 0
    assert "skipped outside" in capsys.readouterr().out


def test_main_runs_private_reconciliation_audit_before_market_window(
    monkeypatch,
) -> None:
    called = False

    def audit() -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setenv("BROKER_RECONCILIATION_AUDIT_ENABLED", "true")
    monkeypatch.setattr(cron_trigger, "reconciliation_audit_main", audit)
    monkeypatch.setattr(cron_trigger, "is_scheduled_sync_window", lambda: False)

    assert cron_trigger.main() == 0
    assert called is True


def test_main_reports_network_failure_without_sensitive_details(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cron_trigger, "is_scheduled_sync_window", lambda: True)
    monkeypatch.setenv("WEBULL_SERVICE_URL", "http://webull-api.railway.internal:8000")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "do-not-log-this-token")
    monkeypatch.setenv("PORTFOLIO_OWNER_GITHUB_ID", "sensitive-owner-id")

    def fail_request(*_args, **_kwargs):
        raise RuntimeError("do-not-log-this-token sensitive-owner-id")

    monkeypatch.setattr(cron_trigger, "_open_private_request", fail_request)

    assert cron_trigger.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Webull scheduled sync trigger failed (network).\n"


def test_main_reports_only_sanitized_http_status(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cron_trigger, "is_scheduled_sync_window", lambda: True)
    monkeypatch.setenv("WEBULL_SERVICE_URL", "http://webull-api.railway.internal:8000")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "do-not-log-this-token")
    monkeypatch.setenv("PORTFOLIO_OWNER_GITHUB_ID", "sensitive-owner-id")

    def fail_request(request, **_kwargs):
        raise HTTPError(
            request.full_url,
            502,
            "private-upstream-detail",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(cron_trigger, "_open_private_request", fail_request)

    assert cron_trigger.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Webull scheduled sync trigger failed (http_502).\n"
    assert "private-upstream-detail" not in captured.err
    assert "do-not-log-this-token" not in captured.err


def test_main_distinguishes_timeout_without_logging_details(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cron_trigger, "is_scheduled_sync_window", lambda: True)
    monkeypatch.setenv("WEBULL_SERVICE_URL", "http://webull-api.railway.internal:8000")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "do-not-log-this-token")
    monkeypatch.setenv("PORTFOLIO_OWNER_GITHUB_ID", "sensitive-owner-id")

    def time_out(*_args, **_kwargs):
        raise TimeoutError("private-timeout-detail")

    monkeypatch.setattr(cron_trigger, "_open_private_request", time_out)

    assert cron_trigger.main() == 1
    assert capsys.readouterr().err == (
        "Webull scheduled sync trigger failed (timeout).\n"
    )


def test_main_rejects_incomplete_configuration_without_network(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cron_trigger, "is_scheduled_sync_window", lambda: True)
    monkeypatch.delenv("WEBULL_SERVICE_URL", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.delenv("PORTFOLIO_OWNER_GITHUB_ID", raising=False)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("network must not be called without configuration")

    monkeypatch.setattr(cron_trigger, "_open_private_request", fail_if_called)

    assert cron_trigger.main() == 1
    assert capsys.readouterr().err == (
        "Webull scheduled sync trigger failed (configuration).\n"
    )
