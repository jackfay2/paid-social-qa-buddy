"""Endpoint tests for /tasks/qa/run.

Monkeypatches the wiring functions so we exercise the handler (auth gate,
payload parsing, timeout path, Slack post + dedup, response shape) without
standing up real BigQuery / Sheets / Slack.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.adapters.slack import SlackPostError
from app.api import server, wiring
from app.core.orchestration import OrchestrationResult

_PAYLOAD = {
    "request_id": "req-1",
    "channel_id": "C123",
    "thread_ts": "1716334567.1",
    "sheet_url": "https://docs.google.com/spreadsheets/d/abc/edit",
    "account_id": "123456789",
    "campaign_id": "987654321",
    "campaign_name": "Test Campaign",
    "qa_app": "social",
}


@pytest.fixture(autouse=True)
def _auth_off(monkeypatch):
    # Local-style: skip the (unimplemented) Cloud Tasks OIDC verification.
    monkeypatch.setenv("QA_CLOUD_TASKS_AUTH_REQUIRED", "false")


def _fake_service(result: OrchestrationResult, run_store=None) -> MagicMock:
    service = MagicMock()
    service.run.return_value = result
    service.run_store = run_store if run_store is not None else MagicMock()
    if run_store is None:
        service.run_store.has_worker_notification.return_value = False
    return service


def _completed_result() -> OrchestrationResult:
    return OrchestrationResult(
        status="completed",
        message="QA complete for Test Campaign | Pass 1 | Fix 0 | Review 0 | N/A 0 | Error 0",
        run_id="run-1",
        summary_counts={"pass": 1, "fix": 0, "review": 0, "na": 0, "error": 0},
    )


def _client() -> TestClient:
    return TestClient(server.app)


# --- auth gate -------------------------------------------------------------


def test_auth_required_but_unimplemented_returns_503(monkeypatch) -> None:
    monkeypatch.setenv("QA_CLOUD_TASKS_AUTH_REQUIRED", "true")
    response = _client().post("/tasks/qa/run", json=_PAYLOAD)
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "task_auth_not_implemented"


# --- happy path ------------------------------------------------------------


def test_happy_path_runs_and_posts(monkeypatch) -> None:
    service = _fake_service(_completed_result())
    notifier = MagicMock()
    monkeypatch.setattr(wiring, "build_orchestration_service", lambda s: service)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: notifier)

    response = _client().post("/tasks/qa/run", json=_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["run_id"] == "run-1"
    # Orchestration ran and Slack got the summary.
    service.run.assert_called_once()
    notifier.post_thread_message.assert_called_once()


def test_accepts_legacy_customer_id_field(monkeypatch) -> None:
    """The existing envelope uses customer_id; the worker maps it to account_id."""
    captured = {}

    def fake_build(settings):
        service = _fake_service(_completed_result())

        def _run(req):
            captured["account_id"] = req.account_id
            return _completed_result()

        service.run.side_effect = _run
        return service

    monkeypatch.setattr(wiring, "build_orchestration_service", fake_build)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: None)

    payload = {**_PAYLOAD}
    del payload["account_id"]
    payload["customer_id"] = "555555555"

    response = _client().post("/tasks/qa/run", json=payload)
    assert response.status_code == 200
    assert captured["account_id"] == "555555555"


def test_numeric_account_id_coerced_to_string(monkeypatch) -> None:
    captured = {}

    def fake_build(settings):
        service = MagicMock()
        service.run_store = MagicMock()

        def _run(req):
            captured["account_id"] = req.account_id
            return _completed_result()

        service.run.side_effect = _run
        return service

    monkeypatch.setattr(wiring, "build_orchestration_service", fake_build)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: None)

    payload = {**_PAYLOAD, "account_id": 123456789}  # JSON integer
    response = _client().post("/tasks/qa/run", json=payload)
    assert response.status_code == 200
    assert captured["account_id"] == "123456789"


# --- Slack behavior --------------------------------------------------------


def test_no_slack_token_skips_post(monkeypatch) -> None:
    service = _fake_service(_completed_result())
    monkeypatch.setattr(wiring, "build_orchestration_service", lambda s: service)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: None)

    response = _client().post("/tasks/qa/run", json=_PAYLOAD)
    assert response.status_code == 200  # run still succeeds


def test_duplicate_notification_skips_post(monkeypatch) -> None:
    run_store = MagicMock()
    run_store.has_worker_notification.return_value = True
    service = _fake_service(_completed_result(), run_store=run_store)
    notifier = MagicMock()
    monkeypatch.setattr(wiring, "build_orchestration_service", lambda s: service)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: notifier)

    _client().post("/tasks/qa/run", json=_PAYLOAD)

    notifier.post_thread_message.assert_not_called()


def test_transient_slack_error_returns_500_for_retry(monkeypatch) -> None:
    service = _fake_service(_completed_result())
    notifier = MagicMock()
    notifier.post_thread_message.side_effect = SlackPostError("ratelimited")
    monkeypatch.setattr(wiring, "build_orchestration_service", lambda s: service)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: notifier)

    response = _client().post("/tasks/qa/run", json=_PAYLOAD)
    assert response.status_code == 500


def test_permanent_slack_error_does_not_fail_the_request(monkeypatch) -> None:
    service = _fake_service(_completed_result())
    notifier = MagicMock()
    notifier.post_thread_message.side_effect = SlackPostError("channel_not_found")
    monkeypatch.setattr(wiring, "build_orchestration_service", lambda s: service)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: notifier)

    response = _client().post("/tasks/qa/run", json=_PAYLOAD)
    # Run completed; a permanent Slack failure is logged, not raised.
    assert response.status_code == 200


# --- rejected/failed runs still return cleanly -----------------------------


def test_rejected_run_returns_200_with_status(monkeypatch) -> None:
    rejected = OrchestrationResult(
        status="rejected",
        message="The QA sheet isn't shared with the bot's service account.",
        run_id="run-2",
        error_code="sheet_permission_denied",
    )
    service = _fake_service(rejected)
    monkeypatch.setattr(wiring, "build_orchestration_service", lambda s: service)
    monkeypatch.setattr(wiring, "build_slack_client", lambda s: None)

    response = _client().post("/tasks/qa/run", json=_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["error_code"] == "sheet_permission_denied"
