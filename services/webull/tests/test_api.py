from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from webull_service.adapters import FakeWebullAdapter
from webull_service.config import Settings
from webull_service.main import create_app
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


def test_official_live_calls_require_read_only_scope_confirmation() -> None:
    runtime_settings = replace(
        settings(),
        adapter_kind="official",
        webull_read_only_scope_confirmed=False,
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
    assert health["webullReadOnlyScopeConfirmed"] is False
    response = client.post("/v1/connect", headers=headers())
    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "WEBULL_READ_ONLY_SCOPE_CONFIRMED must be true before live Webull "
            "access is enabled."
        )
    }

    scheduled = client.post("/v1/scheduled-sync", headers=headers())
    assert scheduled.status_code == 503
    assert adapter.calls == []


def test_scheduled_sync_is_private_and_syncs_every_account() -> None:
    adapter = FakeWebullAdapter()
    repository = MemoryRepository()
    client = TestClient(
        create_app(settings=settings(), adapter=adapter, repository=repository)
    )

    assert client.post("/v1/scheduled-sync").status_code == 401
    response = client.post("/v1/scheduled-sync", headers=headers())

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["accountId"] == "fake-account-001"
    assert response.json()[0]["positionsWritten"] == 2
    assert repository.get_connection_state().connected is True


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
    assert disconnected.json() == {
        "connected": False,
        "accounts": [],
        "selectedAccountId": None,
        "lastSyncedAt": None,
        "dashboard": None,
    }


def test_statement_anchor_accepts_normalized_totals_not_documents() -> None:
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
    assert response.status_code == 200, response.text
    assert response.json()["externalStatementId"] == "2026-07"
