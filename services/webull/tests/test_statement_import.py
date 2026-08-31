from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from webull_service.adapters import FakeWebullAdapter
from webull_service.config import Settings
from webull_service.main import create_app
from webull_service.models import BrokerageAccount, BrokerProvider, CashActivity
from webull_service.repository import MemoryRepository
from webull_service.statement_import import (
    SCHEMA_VERSION,
    PreparedStatementImport,
    StatementImportDisposition,
    StatementImportError,
    decrypt_statement_bundle,
    import_statement_bundle,
    prepare_statement_import,
)

ACCOUNT_HANDLE = "plh_synthetic_owner_handle_0001"
SOURCE_ID = "sha256:" + "a" * 64
FINGERPRINT = "hmac-sha256:" + "b" * 64
RECONCILIATION_SOURCE_ID = "sha256:" + "c" * 64


def _bundle() -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "batchId": "00000000-0000-4000-8000-000000000001",
        "generatedAt": "2026-02-28T22:15:00Z",
        "accountHandle": ACCOUNT_HANDLE,
        "sources": [
            {
                "sourceId": SOURCE_ID,
                "provider": "m1",
                "parserVersion": "m1-apex-text-v1",
                "pageCount": 4,
                "statementStart": "2026-02-01",
                "statementEnd": "2026-02-28",
                "accountFingerprint": FINGERPRINT,
            }
        ],
        "anchors": [
            {
                "accountHandle": ACCOUNT_HANDLE,
                "sourceId": SOURCE_ID,
                "date": "2026-02-28",
                "currency": "USD",
                "netLiquidationValue": "132500.00",
                "cashBalance": "2500.00",
                "marginDebitBalance": None,
                "signedCashBalance": "2500.00",
                "quality": "statement_reconciled",
            }
        ],
        "externalFlows": [
            {
                "accountHandle": ACCOUNT_HANDLE,
                "sourceId": SOURCE_ID,
                "sequence": 1,
                "date": "2026-02-10",
                "kind": "security_transfer_in",
                "amount": "2000.00",
                "currency": "USD",
                "valuationStatus": "reported",
                "evidence": "statement_activity",
            }
        ],
        "validation": {
            "importReady": True,
            "publicationReady": True,
            "errorCount": 0,
            "warningCount": 0,
            "duplicateSourceCount": 0,
            "coverage": {
                "start": "2026-02-01",
                "end": "2026-02-28",
                "statementCount": 1,
                "contiguousMonthlyCoverage": True,
            },
            "issues": [],
            "reconciliation": {
                "status": "passed",
                "privateInputSourceId": RECONCILIATION_SOURCE_ID,
                "matchedAnchors": 1,
                "mismatchedAnchors": 0,
                "matchedFlows": 1,
                "mismatchedFlows": 0,
                "issues": [],
            },
        },
    }


def _encrypted(bundle: dict[str, object], key: bytes) -> bytes:
    return Fernet(key).encrypt(
        (json.dumps(bundle, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )


def _with_account_handle(
    bundle: dict[str, object],
    account_handle: str,
) -> dict[str, object]:
    bundle["accountHandle"] = account_handle
    for key in ("anchors", "externalFlows"):
        rows = bundle[key]
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, dict)
            row["accountHandle"] = account_handle
    return bundle


def _api_settings(key: bytes, **changes: object) -> Settings:
    base = Settings(
        database_url="",
        internal_api_token="private-test-token",
        portfolio_owner_github_id="123456789",
        webull_app_key="",
        webull_app_secret="",
        adapter_kind="fake",
        auto_migrate=False,
        m1_statement_import_enabled=True,
        m1_statement_output_key=key.decode("ascii"),
    )
    return replace(base, **changes)


def _api_headers(content_type: str = "application/octet-stream") -> dict[str, str]:
    return {
        "Authorization": "Bearer private-test-token",
        "x-portfolio-owner-github-id": "123456789",
        "Content-Type": content_type,
    }


def _statement_api(
    key: bytes,
    *,
    repository_owner: str = "123456789",
    **settings_changes: object,
) -> tuple[TestClient, MemoryRepository, str]:
    repository = MemoryRepository(repository_owner)
    account = BrokerageAccount(
        account_id="plaid_m1:synthetic-private-account",
        provider=BrokerProvider.PLAID_M1,
        account_type="ROTH",
        status="ACTIVE",
        currency="USD",
    )
    repository.connect_accounts([account])
    account_handle = repository.account_handles[account.account_id]
    client = TestClient(
        create_app(
            settings=_api_settings(key, **settings_changes),
            adapter=FakeWebullAdapter(),
            repository=repository,
        )
    )
    return client, repository, account_handle


def _assert_safe_error(
    exc_info: pytest.ExceptionInfo[StatementImportError],
    expected_code: str,
    *private_values: str,
) -> None:
    assert exc_info.value.code == expected_code
    rendered = repr(exc_info.value) + str(exc_info.value)
    for value in private_values:
        assert value not in rendered


def test_valid_bundle_invokes_one_atomic_callback_and_returns_amount_free_receipt():
    key = Fernet.generate_key()
    encrypted = _encrypted(_bundle(), key)
    calls: list[tuple[str, PreparedStatementImport]] = []

    def commit(owner_id: str, prepared: PreparedStatementImport):
        calls.append((owner_id, prepared))
        assert prepared.bundle.anchors[0].net_liquidation_value == Decimal("132500.00")
        assert len(prepared.decrypted_payload_sha256) == 64
        return StatementImportDisposition.IMPORTED

    receipt = import_statement_bundle(
        encrypted_payload=encrypted,
        m1_statement_output_key=key,
        owner_github_id="123456789",
        atomic_commit=commit,
    )

    assert len(calls) == 1
    assert calls[0][0] == "123456789"
    assert receipt.account_handle == ACCOUNT_HANDLE
    assert receipt.source_document_count == 1
    assert receipt.anchor_count == 1
    assert receipt.external_flow_count == 1
    assert receipt.import_status is StatementImportDisposition.IMPORTED
    assert receipt.publication_eligible is True
    serialized = json.dumps(receipt.model_dump(mode="json", by_alias=True))
    assert "132500.00" not in serialized
    assert "2500.00" not in serialized
    assert "netLiquidationValue" not in serialized
    assert "amount" not in serialized.lower()


def test_already_imported_disposition_is_preserved():
    key = Fernet.generate_key()
    receipt = import_statement_bundle(
        encrypted_payload=_encrypted(_bundle(), key),
        m1_statement_output_key=key,
        owner_github_id="123456789",
        atomic_commit=lambda _owner, _prepared: (
            StatementImportDisposition.ALREADY_IMPORTED
        ),
    )
    assert receipt.import_status is StatementImportDisposition.ALREADY_IMPORTED


def test_key_is_explicit_and_environment_is_never_consulted(monkeypatch):
    correct_key = Fernet.generate_key()
    wrong_key = Fernet.generate_key()
    monkeypatch.setenv("M1_STATEMENT_OUTPUT_KEY", correct_key.decode())
    encrypted = _encrypted(_bundle(), correct_key)

    with pytest.raises(StatementImportError) as exc_info:
        decrypt_statement_bundle(encrypted, wrong_key)

    _assert_safe_error(
        exc_info,
        "STATEMENT_IMPORT_DECRYPT_FAILED",
        correct_key.decode(),
        wrong_key.decode(),
    )


@pytest.mark.parametrize("key", [b"short", "not-ascii-\u2603"])
def test_invalid_key_is_sanitized(key):
    with pytest.raises(StatementImportError) as exc_info:
        decrypt_statement_bundle(b"private-token", key)
    _assert_safe_error(exc_info, "STATEMENT_IMPORT_KEY_INVALID", str(key))


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda bundle: bundle.update(
                {"schemaVersion": "portfolio-lab.m1-statement-backfill.v1"}
            ),
            "STATEMENT_IMPORT_SCHEMA_INVALID",
        ),
        (
            lambda bundle: bundle["anchors"][0].update(  # type: ignore[index,union-attr]
                {"netLiquidationValue": 132500.00}
            ),
            "STATEMENT_IMPORT_SCHEMA_INVALID",
        ),
        (
            lambda bundle: bundle.update({"unexpectedPrivateField": "secret"}),
            "STATEMENT_IMPORT_SCHEMA_INVALID",
        ),
        (
            lambda bundle: bundle["anchors"][0].update(  # type: ignore[index,union-attr]
                {"accountHandle": "plh_other_owner_handle_0002"}
            ),
            "STATEMENT_IMPORT_INTEGRITY_FAILED",
        ),
        (
            lambda bundle: bundle["validation"].update(  # type: ignore[union-attr]
                {"errorCount": 1}
            ),
            "STATEMENT_IMPORT_INTEGRITY_FAILED",
        ),
        (
            lambda bundle: bundle["anchors"][0].update(  # type: ignore[index,union-attr]
                {"signedCashBalance": "2499.99"}
            ),
            "STATEMENT_IMPORT_INTEGRITY_FAILED",
        ),
        (
            lambda bundle: bundle["externalFlows"][0].update(  # type: ignore[index,union-attr]
                {"amount": "-2000.00"}
            ),
            "STATEMENT_IMPORT_INTEGRITY_FAILED",
        ),
    ],
)
def test_invalid_schema_and_integrity_fail_with_deterministic_errors(
    mutate,
    expected_code,
):
    key = Fernet.generate_key()
    bundle = copy.deepcopy(_bundle())
    mutate(bundle)

    with pytest.raises(StatementImportError) as exc_info:
        prepare_statement_import(_encrypted(bundle, key), key)

    _assert_safe_error(
        exc_info,
        expected_code,
        ACCOUNT_HANDLE,
        "132500.00",
        "2500.00",
    )


def test_duplicate_json_keys_and_non_object_roots_are_rejected():
    key = Fernet.generate_key()
    duplicate_json = (
        b'{"schemaVersion":"portfolio-lab.m1-statement-backfill.v2",'
        b'"schemaVersion":"private-duplicate"}'
    )
    non_object_json = b"[]"

    for payload in (duplicate_json, non_object_json):
        with pytest.raises(StatementImportError) as exc_info:
            prepare_statement_import(Fernet(key).encrypt(payload), key)
        _assert_safe_error(
            exc_info,
            "STATEMENT_IMPORT_JSON_INVALID",
            "private-duplicate",
        )


def test_bundle_with_blocking_validation_issue_is_not_imported():
    key = Fernet.generate_key()
    bundle = _bundle()
    validation = bundle["validation"]
    assert isinstance(validation, dict)
    validation.update(
        {
            "importReady": False,
            "publicationReady": False,
            "errorCount": 1,
            "issues": [
                {
                    "code": "EXTERNAL_FLOW_VALUE_UNAVAILABLE",
                    "severity": "error",
                    "sourceId": SOURCE_ID,
                    "date": "2026-02-10",
                }
            ],
        }
    )

    with pytest.raises(StatementImportError) as exc_info:
        prepare_statement_import(_encrypted(bundle, key), key)
    _assert_safe_error(exc_info, "STATEMENT_IMPORT_NOT_READY", ACCOUNT_HANDLE)


def test_repository_exception_and_invalid_disposition_are_sanitized():
    key = Fernet.generate_key()
    encrypted = _encrypted(_bundle(), key)

    def crashing_commit(_owner: str, _prepared: PreparedStatementImport):
        raise RuntimeError("private account 998877 has balance 132500.00")

    with pytest.raises(StatementImportError) as crash_info:
        import_statement_bundle(
            encrypted_payload=encrypted,
            m1_statement_output_key=key,
            owner_github_id="123456789",
            atomic_commit=crashing_commit,
        )
    _assert_safe_error(
        crash_info,
        "STATEMENT_IMPORT_PERSIST_FAILED",
        "998877",
        "132500.00",
    )

    with pytest.raises(StatementImportError) as status_info:
        import_statement_bundle(
            encrypted_payload=encrypted,
            m1_statement_output_key=key,
            owner_github_id="123456789",
            atomic_commit=lambda _owner, _prepared: "imported",  # type: ignore[return-value]
        )
    _assert_safe_error(status_info, "STATEMENT_IMPORT_PERSIST_FAILED", ACCOUNT_HANDLE)


def test_owner_context_is_validated_before_decrypting_private_data():
    key = Fernet.generate_key()
    encrypted = _encrypted(_bundle(), key)

    with pytest.raises(StatementImportError) as exc_info:
        import_statement_bundle(
            encrypted_payload=encrypted,
            m1_statement_output_key=key,
            owner_github_id="not-an-owner-id",
            atomic_commit=lambda _owner, _prepared: StatementImportDisposition.IMPORTED,
        )

    _assert_safe_error(exc_info, "STATEMENT_IMPORT_AUTH_INVALID", ACCOUNT_HANDLE)


def test_private_binary_endpoint_commits_atomically_and_is_idempotent():
    key = Fernet.generate_key()
    client, repository, account_handle = _statement_api(key)
    encrypted = _encrypted(_with_account_handle(_bundle(), account_handle), key)

    first = client.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=encrypted,
    )
    repeated = client.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=encrypted,
    )

    assert first.status_code == 200, first.text
    assert repeated.status_code == 200, repeated.text
    assert first.json()["importStatus"] == "imported"
    assert repeated.json()["importStatus"] == "already_imported"
    assert len(repository.statement_import_batches) == 1
    assert len(repository.statement_import_sources) == 1
    assert len(repository.statement_external_flows) == 1
    rendered = first.text + repeated.text
    assert "132500.00" not in rendered
    assert "2500.00" not in rendered
    assert "2000.00" not in rendered
    assert "amount" not in rendered.lower()


def test_private_binary_endpoint_is_fail_closed_and_has_no_raw_anchor_route():
    key = Fernet.generate_key()
    client, repository, account_handle = _statement_api(key)
    encrypted = _encrypted(_with_account_handle(_bundle(), account_handle), key)

    assert client.post("/v1/statement-imports", content=encrypted).status_code == 401
    assert (
        client.post(
            "/v1/statement-imports",
            headers=_api_headers("application/json"),
            content=encrypted,
        ).status_code
        == 415
    )
    disabled, _, _ = _statement_api(key, m1_statement_import_enabled=False)
    assert (
        disabled.post(
            "/v1/statement-imports",
            headers=_api_headers(),
            content=encrypted,
        ).status_code
        == 404
    )
    missing_key, _, _ = _statement_api(key, m1_statement_output_key="")
    assert (
        missing_key.post(
            "/v1/statement-imports",
            headers=_api_headers(),
            content=encrypted,
        ).status_code
        == 503
    )
    raw_anchor = client.post(
        "/v1/statement-anchors",
        headers=_api_headers("application/json"),
        json={"private": "payload"},
    )
    assert raw_anchor.status_code == 404
    assert repository.statement_import_batches == {}


def test_repository_rejects_cross_owner_case_changes_and_hash_collisions():
    key = Fernet.generate_key()
    client, repository, account_handle = _statement_api(key)
    initial = _with_account_handle(_bundle(), account_handle)
    imported = client.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=_encrypted(initial, key),
    )
    assert imported.status_code == 200

    changed_source = _with_account_handle(_bundle(), account_handle)
    changed_source["batchId"] = "00000000-0000-4000-8000-000000000002"
    flows = changed_source["externalFlows"]
    assert isinstance(flows, list) and isinstance(flows[0], dict)
    flows[0]["amount"] = "2100.00"
    collision = client.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=_encrypted(changed_source, key),
    )
    assert collision.status_code == 422
    assert collision.json()["code"] == "STATEMENT_IMPORT_INTEGRITY_FAILED"
    assert len(repository.statement_import_batches) == 1
    assert len(repository.statement_external_flows) == 1

    different_case = account_handle.upper()
    assert different_case != account_handle
    wrong_handle = _with_account_handle(_bundle(), different_case)
    wrong_handle["batchId"] = "00000000-0000-4000-8000-000000000003"
    unavailable = client.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=_encrypted(wrong_handle, key),
    )
    assert unavailable.status_code == 422
    assert unavailable.json()["code"] == "STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE"

    cross_owner, cross_repository, cross_handle = _statement_api(
        key,
        repository_owner="987654321",
    )
    cross_bundle = _with_account_handle(_bundle(), cross_handle)
    cross_owner_response = cross_owner.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=_encrypted(cross_bundle, key),
    )
    assert cross_owner_response.status_code == 422
    assert cross_owner_response.json()["code"] == "STATEMENT_IMPORT_ACCOUNT_UNAVAILABLE"
    assert cross_repository.statement_import_batches == {}


def test_plaid_flow_precedence_and_unvalued_flow_publication_block():
    key = Fernet.generate_key()
    client, repository, account_handle = _statement_api(key)
    account_id = next(iter(repository.accounts))
    repository.activities[(account_id, "plaid-transaction-1")] = CashActivity(
        account_id=account_id,
        external_activity_id="plaid-transaction-1",
        activity_type="TRANSFER",
        occurred_at=datetime(2026, 2, 10, 17, tzinfo=UTC),
        amount=Decimal("2000.00"),
        currency="USD",
        status="POSTED",
        is_external_flow=True,
    )
    imported = client.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=_encrypted(
            _with_account_handle(_bundle(), account_handle),
            key,
        ),
    )
    assert imported.status_code == 200
    _, effective_flows = repository.get_performance_inputs(account_id)
    assert [flow.external_activity_id for flow in effective_flows] == [
        "plaid-transaction-1"
    ]
    assert repository.cash_activity_coverage_spans(
        account_id,
        start=datetime(2026, 2, 1, 17, tzinfo=UTC),
        end=datetime(2026, 2, 28, 21, tzinfo=UTC),
    )

    unvalued_client, unvalued_repository, unvalued_handle = _statement_api(key)
    unvalued = _with_account_handle(_bundle(), unvalued_handle)
    flows = unvalued["externalFlows"]
    assert isinstance(flows, list) and isinstance(flows[0], dict)
    flows[0].update(
        {
            "kind": "security_transfer_unknown",
            "amount": None,
            "valuationStatus": "unavailable",
        }
    )
    response = unvalued_client.post(
        "/v1/statement-imports",
        headers=_api_headers(),
        content=_encrypted(unvalued, key),
    )
    assert response.status_code == 200, response.text
    assert response.json()["publicationEligible"] is False
    unvalued_account_id = next(iter(unvalued_repository.accounts))
    _, arithmetic_flows = unvalued_repository.get_performance_inputs(
        unvalued_account_id
    )
    assert arithmetic_flows == []
    assert not unvalued_repository.cash_activity_coverage_spans(
        unvalued_account_id,
        start=datetime(2026, 2, 1, 17, tzinfo=UTC),
        end=datetime(2026, 2, 28, 21, tzinfo=UTC),
    )
