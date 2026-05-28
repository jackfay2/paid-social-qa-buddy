"""Unit tests for SecretManagerService.

Mocks the Google client at the access_secret_version boundary so no GCP calls
happen. The contract under test: on success, return the decoded payload; on
any failure, raise SecretResolutionError with structured fields.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.secrets import SecretManagerService, SecretResolutionError


def _fake_response(payload: bytes) -> MagicMock:
    response = MagicMock()
    response.payload.data = payload
    return response


def _fake_client(payload: bytes | None = b"hello") -> MagicMock:
    client = MagicMock()
    if payload is None:
        # Simulate a response with no payload data — should raise.
        response = MagicMock()
        response.payload = None
        client.access_secret_version.return_value = response
    else:
        client.access_secret_version.return_value = _fake_response(payload)
    return client


# --- success path ----------------------------------------------------------


def test_access_secret_returns_decoded_string() -> None:
    client = _fake_client(b'{"type": "service_account"}')
    svc = SecretManagerService(project_id="my-proj", client=client)

    result = svc.access_secret("sheets-sa-json", key="google_sheets_service_account_json")

    assert result == '{"type": "service_account"}'
    client.access_secret_version.assert_called_once_with(
        name="projects/my-proj/secrets/sheets-sa-json/versions/latest"
    )


def test_access_secret_uses_configured_version() -> None:
    client = _fake_client(b"v2-payload")
    svc = SecretManagerService(project_id="my-proj", version="3", client=client)

    svc.access_secret("my-secret", key="anything")

    client.access_secret_version.assert_called_once_with(
        name="projects/my-proj/secrets/my-secret/versions/3"
    )


# --- failure paths ---------------------------------------------------------


def test_missing_project_raises_structured_error() -> None:
    svc = SecretManagerService(project_id="", client=MagicMock())
    with pytest.raises(SecretResolutionError) as excinfo:
        svc.access_secret("any-secret", key="target_field")
    assert excinfo.value.error_code == "secret_manager_project_missing"
    assert excinfo.value.key == "target_field"


def test_blank_secret_name_raises_structured_error() -> None:
    svc = SecretManagerService(project_id="my-proj", client=MagicMock())
    with pytest.raises(SecretResolutionError) as excinfo:
        svc.access_secret("", key="target_field")
    assert excinfo.value.error_code == "secret_name_missing"


def test_access_failure_wrapped_in_structured_error() -> None:
    client = MagicMock()
    client.access_secret_version.side_effect = RuntimeError("permission denied")
    svc = SecretManagerService(project_id="my-proj", client=client)

    with pytest.raises(SecretResolutionError) as excinfo:
        svc.access_secret("sa-json", key="google_sheets_service_account_json")
    assert excinfo.value.error_code == "secret_access_failed"
    assert "permission denied" in excinfo.value.message
    assert excinfo.value.__cause__ is not None  # original error preserved


def test_empty_payload_raises_structured_error() -> None:
    svc = SecretManagerService(project_id="my-proj", client=_fake_client(payload=None))
    with pytest.raises(SecretResolutionError) as excinfo:
        svc.access_secret("sa-json", key="google_sheets_service_account_json")
    assert excinfo.value.error_code == "secret_payload_missing"


def test_bad_utf8_payload_raises_structured_error() -> None:
    svc = SecretManagerService(
        project_id="my-proj",
        client=_fake_client(payload=b"\xff\xfe\x00\x00"),  # not valid UTF-8
    )
    with pytest.raises(SecretResolutionError) as excinfo:
        svc.access_secret("sa-json", key="google_sheets_service_account_json")
    assert excinfo.value.error_code == "secret_payload_decode_failed"


# --- error metadata ---------------------------------------------------------


def test_resolution_error_exposes_metadata() -> None:
    err = SecretResolutionError(
        key="target",
        secret_name="my-secret",
        error_code="ec",
        message="boom",
    )
    assert err.key == "target"
    assert err.secret_name == "my-secret"
    assert err.error_code == "ec"
    assert str(err) == "boom"
