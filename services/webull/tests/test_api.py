from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

from fastapi.testclient import TestClient

from webull_service.adapters import FakeWebullAdapter
from webull_service.config import Settings
from webull_service.main import _reconciled_performance_window, create_app
from webull_service.models import (
    BrokerageAccount,
    BrokerProvider,
    CashActivity,
    ValuationPoint,
)
from webull_service.repository import MemoryRepository


def settings() -> Settings:
    return Settings(
        database_url="",
        internal_api_token="test-internal-token",
        portfolio_owner_github_id="12345",
        webull_app_key="",
        webull_app_secret="",
        adapter_kind="fake",
        auto_migrate=False,
    )


def headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-internal-token",
        "x-portfolio-owner-github-id": "12345",
    }


def test_health_is_public_but_every_v1_route_requires_proxy_identity() -> None:
    client = TestClient(
        create_app(
            settings=settings(),
            adapter=FakeWebullAdapter(),
            repository=MemoryRepository(),
        )
    )

    assert client.get("/health").status_code == 200
    assert client.get("/v1/status").status_code == 401
    assert (
        client.get(
            "/v1/status", headers={"Authorization": "Bearer test-internal-token"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/v1/status",
            headers={
                "Authorization": "Bearer test-internal-token",
                "x-portfolio-owner-github-id": "wrong",
            },
        ).status_code
        == 403
    )


def test_missing_owner_configuration_keeps_health_public_but_protected_routes_closed() -> (
    None
):
    client = TestClient(
        create_app(
            settings=replace(settings(), portfolio_owner_github_id=""),
            adapter=FakeWebullAdapter(),
            repository=MemoryRepository(),
        )
    )

    assert client.get("/health").status_code == 200
    response = client.get("/v1/status", headers=headers())
    assert response.status_code == 503
    assert response.json() == {"detail": "PORTFOLIO_OWNER_GITHUB_ID is not configured."}


def test_official_live_calls_require_read_only_adapter_activation() -> None:
    runtime_settings = replace(
        settings(),
        adapter_kind="official",
        webull_read_only_adapter_enabled=False,
    )
    adapter = FakeWebullAdapter()
    client = TestClient(
        create_app(
            settings=runtime_settings,
            adapter=adapter,
            repository=MemoryRepository(),
        )
    )

    health = client.get("/health").json()
    assert health["webullReadOnlyAdapterEnabled"] is False
    response = client.post("/v1/connect", headers=headers())
    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "WEBULL_READ_ONLY_ADAPTER_ENABLED must be true before live Webull "
            "access is enabled."
        )
    }

    scheduled = client.post("/v1/scheduled-sync", headers=headers())
    assert scheduled.status_code == 503
    assert adapter.calls == []

    status = client.get("/v1/status", headers=headers())
    assert status.status_code == 200
    assert status.json()["nextAction"] == "configure"


def test_legacy_scope_flag_does_not_activate_application_adapter(monkeypatch) -> None:
    monkeypatch.delenv("WEBULL_READ_ONLY_ADAPTER_ENABLED", raising=False)
    monkeypatch.setenv("WEBULL_READ_ONLY_SCOPE_CONFIRMED", "true")

    assert Settings.from_env().webull_read_only_adapter_enabled is False


def test_application_adapter_marker_is_required_even_when_enabled() -> None:
    runtime_settings = replace(
        settings(),
        adapter_kind="official",
        webull_read_only_adapter_enabled=True,
    )

    class UnverifiedAdapter(FakeWebullAdapter):
        enforcement_mode = "unverified"

    adapter = UnverifiedAdapter()
    client = TestClient(
        create_app(
            settings=runtime_settings,
            adapter=adapter,
            repository=MemoryRepository(),
        )
    )

    assert client.get("/health").json()["webullReadOnlyAdapterEnabled"] is False
    response = client.post("/v1/connect", headers=headers())
    assert response.status_code == 503
    assert adapter.calls == []


def test_application_read_only_activation_allows_account_reads() -> None:
    runtime_settings = replace(
        settings(),
        adapter_kind="official",
        webull_read_only_adapter_enabled=True,
    )
    adapter = FakeWebullAdapter()
    client = TestClient(
        create_app(
            settings=runtime_settings,
            adapter=adapter,
            repository=MemoryRepository(),
        )
    )

    assert client.get("/health").json()["webullReadOnlyAdapterEnabled"] is True
    response = client.post("/v1/connect", headers=headers())
    assert response.status_code == 200
    assert response.json()["nextAction"] == "view_portfolio"
    assert {name for name, _ in adapter.calls} <= {
        "list_accounts",
        "get_balance",
        "get_positions",
        "get_cash_activities",
        "get_order_history",
        "probe",
    }


def test_scheduled_sync_is_private_and_syncs_every_account() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )

    assert client.post("/v1/scheduled-sync").status_code == 401
    skipped = client.post("/v1/scheduled-sync", headers=headers())
    assert skipped.status_code == 200
    assert skipped.json() == []
    assert adapter.calls == []

    connected = client.post("/v1/connect", headers=headers())
    assert connected.status_code == 200, connected.text
    response = client.post("/v1/scheduled-sync", headers=headers())

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["accountId"] == "fake-account-001"
    assert response.json()[0]["positionsWritten"] == 2
    assert response.json()[0]["cashActivitiesComplete"] is True
    assert repository.get_connection_state().connected is True


def test_scheduled_sync_returns_failure_for_a_connected_core_snapshot_error() -> None:
    adapter = FakeWebullAdapter()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=MemoryRepository())
    )
    assert client.post("/v1/connect", headers=headers()).status_code == 200
    adapter.fail_positions = True

    response = client.post("/v1/scheduled-sync", headers=headers())

    assert response.status_code == 502
    assert response.json() == {"detail": "One or more Webull account snapshots failed."}


def test_proxy_contract_connect_select_sync_dashboard_and_disconnect() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )

    connected = client.post("/v1/connect", headers=headers())
    assert connected.status_code == 200, connected.text
    payload = connected.json()
    assert payload["connected"] is True
    assert payload["selectedAccountId"] == "fake-account-001"
    assert payload["dashboard"]["portfolio"]["balance"]["equity"] == "15000.00"
    assert payload["dashboard"]["portfolio"]["positions"][0]["symbol"] == "AAPL"
    assert payload["lastSyncAttempt"]["status"] == "success"
    assert payload["lastSyncAttempt"]["cashActivitiesComplete"] is True
    assert payload["lastSyncAttempt"]["message"] is None
    assert client.get("/v1/accounts", headers=headers()).json() == payload["accounts"]

    synced = client.post("/v1/sync", headers=headers(), json={})
    assert synced.status_code == 200
    assert synced.json()["positionsWritten"] == 2

    backfill = client.post("/v1/backfill", headers=headers(), json={"days": 365})
    assert backfill.status_code == 200
    assert backfill.json()["orderHistoryAvailable"] is True

    assert client.get("/v1/activities", headers=headers()).status_code == 200
    assert (
        client.get("/v1/orders", headers=headers()).json()["orders"][0]["side"] == "BUY"
    )

    disconnected = client.delete("/v1/connect", headers=headers())
    assert disconnected.status_code == 200
    disconnected_payload = disconnected.json()
    assert disconnected_payload["connected"] is False
    assert disconnected_payload["verificationInProgress"] is False
    assert disconnected_payload["verification"]["state"] == "succeeded"
    assert disconnected_payload["nextAction"] == "start_verification"
    assert disconnected_payload["accounts"] == []
    assert disconnected_payload["selectedAccountId"] is None
    assert disconnected_payload["lastSyncedAt"] is None
    assert disconnected_payload["lastSyncAttempt"] is None
    assert disconnected_payload["dashboard"] is None


def test_incomplete_cash_activity_coverage_keeps_holdings_but_blocks_performance() -> (
    None
):
    adapter = FakeWebullAdapter()
    adapter.fail_cash_activities = True
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )

    response = client.post("/v1/connect", headers=headers())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["connected"] is True
    assert payload["dashboard"]["portfolio"]["positions"][0]["symbol"] == "AAPL"
    assert payload["dashboard"]["performance"] is None
    assert payload["lastSyncAttempt"]["status"] == "success"
    assert payload["lastSyncAttempt"]["cashActivitiesComplete"] is False
    assert payload["lastSyncAttempt"]["message"] == (
        "Current holdings were refreshed, but cash activity coverage is incomplete; "
        "performance remains unavailable."
    )
    issue_codes = {item["code"] for item in payload["dashboard"]["issues"]}
    assert "CASH_ACTIVITY_COVERAGE_INCOMPLETE" in issue_codes
    assert "PERFORMANCE_HISTORY_BUILDING" not in issue_codes


def test_failed_core_sync_is_visible_without_invalidating_prior_cash_coverage() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )
    assert client.post("/v1/connect", headers=headers()).status_code == 200
    adapter.fail_positions = True

    failed = client.post("/v1/sync", headers=headers(), json={})

    assert failed.status_code == 502
    status = client.get("/v1/status", headers=headers())
    assert status.status_code == 200
    payload = status.json()
    assert payload["lastSyncAttempt"]["status"] == "error"
    assert payload["lastSyncAttempt"]["cashActivitiesComplete"] is None
    assert payload["lastSyncAttempt"]["message"] == "Webull positions request failed."
    assert payload["dashboard"]["portfolio"] is not None
    assert payload["dashboard"]["performance"] is not None


def test_partial_backfill_marks_performance_coverage_incomplete() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )
    assert client.post("/v1/connect", headers=headers()).status_code == 200
    adapter.fail_cash_activities = True

    backfill = client.post("/v1/backfill", headers=headers(), json={"days": 365})

    assert backfill.status_code == 200, backfill.text
    assert backfill.json()["cashActivitiesAvailable"] is False
    assert backfill.json()["orderHistoryAvailable"] is True
    adapter.fail_cash_activities = False
    routine = client.post("/v1/sync", headers=headers(), json={})
    assert routine.status_code == 200, routine.text
    assert routine.json()["cashActivitiesComplete"] is False
    payload = client.get("/v1/status", headers=headers()).json()
    assert payload["lastSyncAttempt"]["status"] == "success"
    assert payload["lastSyncAttempt"]["cashActivitiesComplete"] is False
    assert payload["dashboard"]["performance"] is None
    assert "CASH_ACTIVITY_COVERAGE_INCOMPLETE" in {
        item["code"] for item in payload["dashboard"]["issues"]
    }


def test_reconciled_performance_uses_longest_covered_prefix() -> None:
    repository = MemoryRepository("12345")
    account_id = "private-account"
    start = datetime(2025, 12, 31, 21, tzinfo=UTC)
    covered_end = datetime(2026, 7, 31, 20, tzinfo=UTC)
    newer_uncovered = datetime(2026, 8, 28, 20, tzinfo=UTC)
    repository.accounts[account_id] = BrokerageAccount(
        account_id=account_id,
        provider=BrokerProvider.WEBULL,
        account_type="MARGIN",
        status="ACTIVE",
        currency="USD",
    )
    repository.cash_activity_coverage_ranges[account_id] = (start, covered_end)
    valuations = [
        ValuationPoint(at=start, value=Decimal(100), source="statement_anchor"),
        ValuationPoint(
            at=covered_end,
            value=Decimal(150),
            source="statement_anchor",
        ),
        ValuationPoint(at=newer_uncovered, value=Decimal(160)),
    ]
    flows = [
        CashActivity(
            account_id=account_id,
            external_activity_id="covered-flow",
            activity_type="WITHDRAWAL",
            occurred_at=datetime(2026, 5, 13, 16, 30, 9, tzinfo=UTC),
            amount=Decimal(-10),
            is_external_flow=True,
        ),
        CashActivity(
            account_id=account_id,
            external_activity_id="uncovered-flow",
            activity_type="DEPOSIT",
            occurred_at=datetime(2026, 8, 10, 16, tzinfo=UTC),
            amount=Decimal(5),
            is_external_flow=True,
        ),
    ]

    reconciled_values, reconciled_flows, complete = _reconciled_performance_window(
        repository,
        account_id,
        valuations,
        flows,
    )

    assert complete is True
    assert [point.at for point in reconciled_values] == [start, covered_end]
    assert [flow.external_activity_id for flow in reconciled_flows] == ["covered-flow"]


def test_connect_is_idempotent_and_returns_durable_overlapping_verification() -> None:
    class BlockingConnectAdapter(FakeWebullAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.release = Event()

        def list_accounts(self):
            self.started.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("Test verification was not released.")
            return super().list_accounts()

    adapter = BlockingConnectAdapter()
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(client.post, "/v1/connect", headers=headers())
        try:
            assert adapter.started.wait(timeout=2)
            status = client.get("/v1/status", headers=headers())
            assert status.status_code == 200
            assert status.json()["verificationInProgress"] is True
            attempt_id = status.json()["verification"]["attemptId"]
            assert status.json()["verification"]["state"] == "running"
            assert status.json()["verification"]["stage"] == "verifying_access"
            assert status.json()["nextAction"] == "wait"

            duplicate = client.post("/v1/connect", headers=headers())
            assert duplicate.status_code == 200
            assert duplicate.json()["verificationInProgress"] is True
            assert duplicate.json()["verification"]["attemptId"] == attempt_id
            assert duplicate.json()["nextAction"] == "wait"
        finally:
            adapter.release.set()

        connected = first.result(timeout=5)

    assert connected.status_code == 200, connected.text
    assert connected.json()["verificationInProgress"] is False
    assert connected.json()["verification"]["state"] == "succeeded"
    assert connected.json()["verification"]["stage"] == "complete"
    assert connected.json()["verification"]["error"] is None
    assert connected.json()["nextAction"] == "view_portfolio"
    broker_calls_after_first = tuple(
        call for call in adapter.calls if call != ("probe", "False")
    )

    repeated = client.post("/v1/connect", headers=headers())
    assert repeated.status_code == 200
    assert repeated.json()["connected"] is True
    assert repeated.json()["verificationInProgress"] is False
    assert tuple(call for call in adapter.calls if call != ("probe", "False")) == (
        broker_calls_after_first
    )


def test_failed_verification_is_sanitized_and_retryable() -> None:
    class UnexpectedFailureAdapter(FakeWebullAdapter):
        def list_accounts(self):
            raise RuntimeError("private-key-material-must-not-be-persisted")

    repository = MemoryRepository()
    client = TestClient(
        create_app(
            settings=settings(),
            adapter=UnexpectedFailureAdapter(),
            repository=repository,
        ),
        raise_server_exceptions=False,
    )

    failed = client.post("/v1/connect", headers=headers())
    assert failed.status_code == 500

    status = client.get("/v1/status", headers=headers())
    assert status.status_code == 200
    payload = status.json()
    assert payload["connected"] is False
    assert payload["verificationInProgress"] is False
    assert payload["verification"]["state"] == "failed"
    assert payload["verification"]["stage"] == "verifying_access"
    assert payload["verification"]["error"] == {
        "code": "verification_failed",
        "message": "Webull verification could not be completed.",
    }
    assert "private-key-material" not in str(payload)
    assert payload["nextAction"] == "retry_verification"


def test_status_reconciles_an_expired_attempt_as_timed_out() -> None:
    repository = MemoryRepository()
    attempt, _ = repository.begin_verification_attempt()
    repository.verification_attempts[-1] = attempt.model_copy(
        update={"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    client = TestClient(
        create_app(
            settings=settings(),
            adapter=FakeWebullAdapter(),
            repository=repository,
        )
    )

    status = client.get("/v1/status", headers=headers())

    assert status.status_code == 200
    payload = status.json()
    assert payload["verificationInProgress"] is False
    assert payload["verification"]["state"] == "timed_out"
    assert payload["verification"]["error"]["code"] == "verification_timeout"
    assert payload["nextAction"] == "retry_verification"


def test_connected_account_without_snapshot_requests_sync() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    repository.connect_accounts(adapter.list_accounts())
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )

    status = client.get("/v1/status", headers=headers())

    assert status.status_code == 200
    assert status.json()["connected"] is True
    assert status.json()["dashboard"]["portfolio"] is None
    assert status.json()["nextAction"] == "sync_account"


def test_raw_statement_anchor_route_is_not_exposed() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )
    client.post("/v1/connect", headers=headers())

    response = client.post(
        "/v1/statement-anchors",
        headers=headers(),
        json={
            "accountId": "fake-account-001",
            "externalStatementId": "2026-07",
            "statementDate": datetime(2026, 7, 31, 20, tzinfo=UTC).isoformat(),
            "endingEquity": str(Decimal(14500)),
            "endingCash": str(Decimal(2500)),
            "currency": "USD",
            "sourceSha256": "a" * 64,
        },
    )
    assert response.status_code == 404
    assert repository.anchors == {}
