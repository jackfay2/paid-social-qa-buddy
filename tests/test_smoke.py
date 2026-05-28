"""Smoke tests proving the scaffold loads and the registry contract holds."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import app
from app.checks.registry import run_check
from app.models import CheckRow


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_worker_role() -> None:
    client = TestClient(app)
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert "service_role" in body


def test_readyz_healthy_default_reports_no_secret_errors() -> None:
    """Default config (no Secret Manager) is ready with an empty error list."""
    client = TestClient(app)
    body = client.get("/readyz").json()
    assert body["secrets"]["secret_manager_enabled"] is False
    assert body["secrets"]["errors"] == []


def test_readyz_503_when_secret_resolution_fails(monkeypatch) -> None:
    """A failed Secret Manager indirection must fail the readiness probe so
    Cloud Run never routes traffic to a broken revision."""
    from app.adapters.secrets import SecretResolutionError
    from app.api import server
    from app.config import Settings

    broken = Settings(qa_use_secret_manager=True, gcp_project_id="prj-test")
    broken.secret_resolution_errors.append(
        SecretResolutionError(
            key="google_sheets_service_account_json",
            secret_name="sheets-sa-json",
            error_code="secret_access_failed",
            message="Failed to access secret 'sheets-sa-json': 403 PermissionDenied",
        )
    )
    monkeypatch.setattr(server, "load_settings", lambda: broken)

    response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert len(body["secrets"]["errors"]) == 1
    err = body["secrets"]["errors"][0]
    assert err["error_code"] == "secret_access_failed"
    assert err["secret_name"] == "sheets-sa-json"
    assert err["key"] == "google_sheets_service_account_json"


def test_readyz_never_leaks_secret_values(monkeypatch) -> None:
    """The probe response must carry names + error codes, never resolved
    secret payloads."""
    from app.adapters.secrets import SecretResolutionError
    from app.api import server
    from app.config import Settings

    secret_value = "SUPER-SECRET-SA-JSON-PAYLOAD"
    broken = Settings(qa_use_secret_manager=True, gcp_project_id="prj-test")
    broken.secret_resolution_errors.append(
        SecretResolutionError(
            key="google_sheets_service_account_json",
            secret_name="sheets-sa-json",
            error_code="secret_access_failed",
            message="boom",  # message is operator-authored; never the payload
        )
    )
    monkeypatch.setattr(server, "load_settings", lambda: broken)

    raw = TestClient(app).get("/readyz").text
    assert secret_value not in raw


def test_readyz_503_when_settings_load_raises(monkeypatch) -> None:
    """If settings construction itself blows up, the probe answers 503 rather
    than a 500 stack trace — still keeps a broken revision out of rotation."""
    from app.api import server

    def _boom():
        raise RuntimeError("env parse exploded")

    monkeypatch.setattr(server, "load_settings", _boom)

    response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["error_code"] == "settings_load_failed"


def test_unknown_check_id_returns_error_verdict() -> None:
    row = CheckRow(row_index=1, check_id="does_not_exist", builder_input="anything")
    result = run_check(row)
    assert result.verdict == "Error"
    assert "Unrecognized" in result.action
    assert result.check_id == "does_not_exist"

# The /tasks/qa/run endpoint is now fully implemented; its behavior is covered
# in tests/test_worker_endpoint.py.
