import base64
import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient

from webull_service.adapters import (
    BrokerAdapterError,
    FakeWebullAdapter,
    PlaidHttpClient,
    PlaidM1InvestmentsAdapter,
)
from webull_service.config import Settings
from webull_service.main import create_app
from webull_service.models import CashSource
from webull_service.repository import MemoryRepository


class FakePlaidClient:
    webhook_private_key = ec.generate_private_key(ec.SECP256R1())

    def accounts_get(self, access_token):
        assert access_token == "access-token"
        return {"accounts": [self._account()]}

    def holdings_get(self, access_token):
        assert access_token == "access-token"
        return {
            "accounts": [self._account()],
            "securities": [
                {
                    "security_id": "security-1",
                    "ticker_symbol": "AAPL",
                    "type": "equity",
                    "iso_currency_code": "USD",
                }
            ],
            "holdings": [
                {
                    "account_id": "account-1",
                    "security_id": "security-1",
                    "quantity": 5,
                    "institution_price": 25,
                    "institution_value": 125,
                    "institution_price_as_of": "2026-08-28",
                    "cost_basis": 100,
                    "iso_currency_code": "USD",
                }
            ],
        }

    def investment_transactions_get(
        self, access_token, *, start_date, end_date, offset, count
    ):
        del start_date, end_date, count
        assert access_token == "access-token"
        rows = (
            [
                {
                    "account_id": "account-1",
                    "investment_transaction_id": "tx-1",
                    "date": "2026-08-01",
                    "amount": -50,
                    "subtype": "deposit",
                    "name": "Deposit",
                    "iso_currency_code": "USD",
                }
            ]
            if offset == 0
            else []
        )
        return {
            "investment_transactions": rows,
            "total_investment_transactions": 1,
        }

    def item_get(self, access_token):
        assert access_token == "access-token"
        return {"item": {"institution_id": "ins_m1"}}

    def institution_get_by_id(self, institution_id):
        assert institution_id == "ins_m1"
        return {
            "institution": {
                "name": "M1 Finance",
                "products": ["investments"],
            }
        }

    def link_token_create(
        self, *, client_user_id, redirect_uri=None, access_token=None
    ):
        del client_user_id, redirect_uri, access_token
        return {"link_token": "link-token", "expiration": "2026-08-30T22:00:00Z"}

    def exchange_public_token(self, public_token):
        assert public_token == "public-token"
        return {"access_token": "access-token", "item_id": "item-1"}

    def webhook_verification_key_get(self, key_id):
        assert key_id == "test-key"
        numbers = self.webhook_private_key.public_key().public_numbers()
        return {
            "key": {
                "alg": "ES256",
                "crv": "P-256",
                "kid": key_id,
                "kty": "EC",
                "use": "sig",
                "x": _base64url(numbers.x.to_bytes(32, "big")),
                "y": _base64url(numbers.y.to_bytes(32, "big")),
                "expired_at": None,
            }
        }

    @staticmethod
    def _account():
        return {
            "account_id": "account-1",
            "type": "investment",
            "subtype": "brokerage",
            "balances": {
                "current": 100,
                "iso_currency_code": "USD",
                "as_of": "2026-08-28",
            },
        }


def _settings() -> Settings:
    return Settings(
        database_url="",
        internal_api_token="token",
        portfolio_owner_github_id="123",
        webull_app_key="",
        webull_app_secret="",
        adapter_kind="fake",
        auto_migrate=False,
        plaid_m1_integration_enabled=True,
        plaid_client_id="client-id",
        plaid_secret="server-secret",
        broker_credential_encryption_key="test-encryption-key",
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer token",
        "x-portfolio-owner-github-id": "123",
    }


def test_plaid_adapter_derives_negative_margin_cash_without_available_balance() -> None:
    adapter = PlaidM1InvestmentsAdapter(
        client=FakePlaidClient(),
        access_token="access-token",
        expected_institution_id="ins_m1",
    )
    account = adapter.list_accounts()[0]
    balance = adapter.get_balance(account.account_id)
    positions = adapter.get_positions(account.account_id)
    activities = adapter.get_cash_activities(
        account.account_id,
        since=datetime(2026, 7, 1, tzinfo=UTC),
        until=datetime(2026, 8, 30, tzinfo=UTC),
    )

    assert account.provider.value == "plaid_m1"
    assert balance.cash == Decimal(-25)
    assert balance.cash_source is CashSource.DERIVED_RESIDUAL
    assert positions[0].average_cost == Decimal(20)
    assert activities[0].amount == Decimal(50)
    assert activities[0].is_external_flow is True
    assert not any("trade" in name.lower() for name in dir(adapter))


@pytest.mark.parametrize(
    ("field", "method"),
    [
        ("institution_value", "balance"),
        ("quantity", "positions"),
        ("institution_price", "positions"),
    ],
)
def test_plaid_missing_or_invalid_required_numerics_fail_closed(
    field: str, method: str
) -> None:
    class InvalidNumericPlaidClient(FakePlaidClient):
        def holdings_get(self, access_token):
            payload = super().holdings_get(access_token)
            payload["holdings"][0][field] = None if field != "quantity" else "NaN"
            return payload

    adapter = PlaidM1InvestmentsAdapter(
        client=InvalidNumericPlaidClient(),
        access_token="access-token",
        expected_institution_id="ins_m1",
    )
    account_id = adapter.list_accounts()[0].account_id

    with pytest.raises(BrokerAdapterError, match="Plaid did not return a valid"):
        if method == "balance":
            adapter.get_balance(account_id)
        else:
            adapter.get_positions(account_id)


@pytest.mark.parametrize("missing", [False, True])
def test_plaid_does_not_derive_residual_cash_without_a_common_as_of(
    missing: bool,
) -> None:
    class StalePlaidClient(FakePlaidClient):
        def holdings_get(self, access_token):
            payload = super().holdings_get(access_token)
            if missing:
                payload["accounts"][0]["balances"].pop("as_of")
            else:
                payload["holdings"][0]["institution_price_as_of"] = "2026-08-27"
            return payload

    adapter = PlaidM1InvestmentsAdapter(
        client=StalePlaidClient(),
        access_token="access-token",
        expected_institution_id="ins_m1",
    )
    account_id = adapter.list_accounts()[0].account_id

    balance = adapter.get_balance(account_id)

    assert balance.cash == Decimal(0)
    assert balance.cash_source is CashSource.UNAVAILABLE
    assert balance.as_of == datetime(1970, 1, 1, tzinfo=UTC)


def test_production_plaid_http_requires_explicit_feature_activation() -> None:
    with pytest.raises(ValueError, match="PLAID_M1_INTEGRATION_ENABLED"):
        PlaidHttpClient(
            client_id="client",
            secret="secret",
            environment="production",
            production_enabled=False,
        )


def test_plaid_link_exchange_stores_token_server_side_and_returns_status_only() -> None:
    repository = MemoryRepository()
    client = TestClient(
        create_app(
            settings=_settings(),
            adapter=FakeWebullAdapter(),
            plaid_client=FakePlaidClient(),
            repository=repository,
        )
    )
    link = client.post("/v1/providers/plaid/link-token", headers=_headers())
    assert link.status_code == 200
    assert link.json()["linkToken"] == "link-token"

    exchange = client.post(
        "/v1/providers/plaid/exchange",
        headers=_headers(),
        json={"publicToken": "public-token"},
    )
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["provider"] == "plaid_m1"
    assert exchange.json()["configured"] is True
    assert "access-token" not in exchange.text
    assert "public-token" not in exchange.text
    assert repository.has_provider_connection("plaid_m1") is True

    update = client.post("/v1/providers/plaid/update-link-token", headers=_headers())
    assert update.status_code == 200
    assert update.json()["linkToken"] == "link-token"


def test_plaid_exchange_rejects_m1_without_live_investments_support() -> None:
    class UnsupportedM1Client(FakePlaidClient):
        def institution_get_by_id(self, institution_id):
            payload = super().institution_get_by_id(institution_id)
            payload["institution"]["products"] = ["auth"]
            return payload

    repository = MemoryRepository()
    client = TestClient(
        create_app(
            settings=_settings(),
            adapter=FakeWebullAdapter(),
            plaid_client=UnsupportedM1Client(),
            repository=repository,
        )
    )

    response = client.post(
        "/v1/providers/plaid/exchange",
        headers=_headers(),
        json={"publicToken": "public-token"},
    )

    assert response.status_code == 502
    assert "does not currently support Plaid Investments" in response.json()["detail"]
    assert repository.has_provider_connection("plaid_m1") is False


def test_plaid_feature_flag_blocks_network_contracts() -> None:
    runtime = replace(_settings(), plaid_m1_integration_enabled=False)
    client = TestClient(
        create_app(
            settings=runtime,
            adapter=FakeWebullAdapter(),
            plaid_client=FakePlaidClient(),
            repository=MemoryRepository(),
        )
    )

    response = client.post("/v1/providers/plaid/link-token", headers=_headers())
    assert response.status_code == 503


def test_internal_webhook_verifies_signature_age_and_exact_raw_body() -> None:
    repository = MemoryRepository()
    client = TestClient(
        create_app(
            settings=_settings(),
            adapter=FakeWebullAdapter(),
            plaid_client=FakePlaidClient(),
            repository=repository,
        )
    )
    raw_body = json.dumps(
        {
            "webhook_type": "HOLDINGS",
            "webhook_code": "DEFAULT_UPDATE",
            "item_id": "item-1",
        },
        separators=(",", ":"),
    )
    signature = _signed_webhook(raw_body)
    accepted = client.post(
        "/v1/providers/plaid/webhook",
        headers=_headers(),
        json={"rawBody": raw_body, "signature": signature},
    )
    tampered = client.post(
        "/v1/providers/plaid/webhook",
        headers=_headers(),
        json={"rawBody": raw_body + " ", "signature": signature},
    )
    expired = client.post(
        "/v1/providers/plaid/webhook",
        headers=_headers(),
        json={
            "rawBody": raw_body,
            "signature": _signed_webhook(raw_body, issued_at=int(time.time()) - 301),
        },
    )
    assert accepted.json() == {"accepted": True, "syncRequired": True}
    assert "plaid_m1" in repository.provider_refresh_requests
    assert tampered.status_code == 502
    assert expired.status_code == 502


def _signed_webhook(raw_body: str, *, issued_at: int | None = None) -> str:
    header = _base64url(
        json.dumps(
            {"alg": "ES256", "kid": "test-key", "typ": "JWT"},
            separators=(",", ":"),
        ).encode()
    )
    claims = _base64url(
        json.dumps(
            {
                "iat": issued_at if issued_at is not None else int(time.time()),
                "request_body_sha256": hashlib.sha256(raw_body.encode()).hexdigest(),
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    der = FakePlaidClient.webhook_private_key.sign(
        signing_input,
        ec.ECDSA(hashes.SHA256()),
    )
    r, s = decode_dss_signature(der)
    signature = _base64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"{header}.{claims}.{signature}"


def _base64url(value: bytes | int) -> str:
    if isinstance(value, int):
        value = value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
