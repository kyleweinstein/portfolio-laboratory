from datetime import timedelta

from fastapi.testclient import TestClient

from webull_service.adapters import FakeWebullAdapter
from webull_service.config import Settings
from webull_service.main import create_app
from webull_service.models import utc_now
from webull_service.repository import MemoryRepository


def _settings(*, encryption_key: str = "discord-private-encryption-key") -> Settings:
    return Settings(
        database_url="",
        internal_api_token="internal-token-long-enough",
        portfolio_owner_github_id="123",
        webull_app_key="",
        webull_app_secret="",
        adapter_kind="fake",
        auto_migrate=False,
        discord_session_encryption_key=encryption_key,
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer internal-token-long-enough",
        "x-portfolio-owner-github-id": "123",
    }


def test_discord_session_material_stays_in_private_server_store() -> None:
    repository = MemoryRepository()
    client = TestClient(
        create_app(
            settings=_settings(),
            adapter=FakeWebullAdapter(),
            repository=repository,
        )
    )
    session_id = "A" * 43
    now = utc_now()
    payload = {
        "sessionId": session_id,
        "session": {
            "discordId": "111122223333444455",
            "username": "member",
            "displayName": "Portfolio Member",
            "guildId": "987654321098765432",
            "accessToken": "private-access-token",
            "refreshToken": "private-refresh-token",
            "tokenExpiresAt": (now + timedelta(hours=1)).isoformat(),
            "membershipCheckedAt": now.isoformat(),
            "expiresAt": (now + timedelta(hours=12)).isoformat(),
        },
    }

    written = client.post(
        "/v1/discord-sessions/write", headers=_headers(), json=payload
    )
    assert written.json() == {"stored": True}
    assert session_id not in repository.discord_sessions

    loaded = client.post(
        "/v1/discord-sessions/read",
        headers=_headers(),
        json={"sessionId": session_id},
    )
    assert loaded.status_code == 200
    assert loaded.json()["session"]["accessToken"] == "private-access-token"

    deleted = client.post(
        "/v1/discord-sessions/delete",
        headers=_headers(),
        json={"sessionId": session_id},
    )
    assert deleted.json() == {"deleted": True}
    assert client.post(
        "/v1/discord-sessions/read",
        headers=_headers(),
        json={"sessionId": session_id},
    ).json() == {"session": None}


def test_discord_session_store_requires_a_distinct_encryption_key() -> None:
    client = TestClient(
        create_app(
            settings=_settings(encryption_key=""),
            adapter=FakeWebullAdapter(),
            repository=MemoryRepository(),
        )
    )

    response = client.post(
        "/v1/discord-sessions/read",
        headers=_headers(),
        json={"sessionId": "A" * 43},
    )

    assert response.status_code == 503
    assert "encryption" in response.json()["detail"].lower()
