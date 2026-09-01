from __future__ import annotations

import base64

from webull_service import statement_import_trigger


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_trigger_posts_encrypted_bundle_only_to_private_service() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    encrypted = b"opaque-encrypted-statement-bundle"
    statement_import_trigger.trigger_statement_import(
        service_url="http://webull-sync.railway.internal:8000",
        internal_api_token="private-test-token",
        owner_github_id="12345",
        encrypted_payload_base64=base64.b64encode(encrypted).decode("ascii"),
        opener=opener,
    )

    request = captured["request"]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert request.full_url == (
        "http://webull-sync.railway.internal:8000/v1/statement-imports"
    )
    assert request.method == "POST"
    assert request.data == encrypted
    assert headers["content-type"] == "application/octet-stream"
    assert headers["authorization"] == "Bearer private-test-token"
    assert headers["x-portfolio-owner-github-id"] == "12345"
    assert captured["timeout"] == 60


def test_trigger_rejects_public_destination_and_malformed_payload() -> None:
    for service_url, payload in (
        ("https://attacker.example.test", base64.b64encode(b"private").decode()),
        ("http://webull-sync.railway.internal:8000", "not base64"),
    ):
        called = False

        def opener(*_args, **_kwargs):
            nonlocal called
            called = True
            return _Response()

        try:
            statement_import_trigger.trigger_statement_import(
                service_url=service_url,
                internal_api_token="private-test-token",
                owner_github_id="12345",
                encrypted_payload_base64=payload,
                opener=opener,
            )
        except statement_import_trigger.StatementImportTriggerError:
            pass
        else:
            raise AssertionError("The unsafe import request was accepted.")
        assert called is False


def test_main_logs_no_payload_or_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setenv("WEBULL_SERVICE_URL", "http://webull-sync.railway.internal:8000")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "private-test-token")
    monkeypatch.setenv("PORTFOLIO_OWNER_GITHUB_ID", "12345")
    monkeypatch.setenv("BROKER_STATEMENT_IMPORT_PAYLOAD_B64", "private-payload")

    assert statement_import_trigger.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Private statement import failed (payload).\n"
    assert "private-test-token" not in captured.err
    assert "private-payload" not in captured.err
