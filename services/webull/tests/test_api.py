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
