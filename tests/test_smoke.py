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


def test_unknown_check_id_returns_error_verdict() -> None:
    row = CheckRow(row_index=1, check_id="does_not_exist", builder_input="anything")
    result = run_check(row)
    assert result.verdict == "Error"
    assert "Unrecognized" in result.action
    assert result.check_id == "does_not_exist"


def test_task_endpoint_returns_not_implemented_until_orchestration_lands() -> None:
    client = TestClient(app)
    response = client.post("/tasks/qa/run", json={})
    assert response.status_code == 501
    body = response.json()
    assert body["detail"]["error_code"] == "not_implemented"
