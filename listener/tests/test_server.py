"""Smoke tests for the listener entrypoint (search + social).

Run from listener/:  cd listener && pytest
Validates the app imports + wires, the health/readiness endpoints, the Slack
url_verification challenge, and signing-secret enforcement. The full
event→enqueue path is exercised by the deployed @-mention test (it would hit
real Cloud Tasks), so here we mock the listener to assert the handler dispatches.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.api import server


def _client() -> TestClient:
    return TestClient(server.app)


def test_healthz() -> None:
    r = _client().get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_readyz_reports_social_config(monkeypatch) -> None:
    monkeypatch.setenv("SOCIAL_CHANNEL_IDS", "C0B6ASW9R9V")
    monkeypatch.setenv("SOCIAL_QUEUE_NAME", "qa-buddy-runs-social-test")
    body = _client().get("/readyz").json()
    assert body["role"] == "listener"
    assert body["social_channel_ids"] == ["C0B6ASW9R9V"]
    assert body["social_queue"] == "qa-buddy-runs-social-test"


def test_url_verification_returns_challenge(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_EVENTS_AUTH_REQUIRED", "false")
    r = _client().post("/slack/events", json={"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json()["challenge"] == "abc123"


def test_signing_rejected_when_required(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_EVENTS_AUTH_REQUIRED", "true")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    r = _client().post(
        "/slack/events",
        headers={"x-slack-request-timestamp": str(int(time.time())), "x-slack-signature": "v0=bad"},
        json={"type": "url_verification", "challenge": "x"},
    )
    assert r.status_code == 401


def test_valid_signature_passes(monkeypatch) -> None:
    secret = "shhh"
    monkeypatch.setenv("SLACK_EVENTS_AUTH_REQUIRED", "true")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
    ts = str(int(time.time()))
    payload = json.dumps({"type": "url_verification", "challenge": "ok"})
    base = f"v0:{ts}:{payload}".encode()
    sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    r = _client().post(
        "/slack/events",
        headers={"x-slack-request-timestamp": ts, "x-slack-signature": sig,
                 "content-type": "application/json"},
        content=payload,
    )
    assert r.status_code == 200 and r.json()["challenge"] == "ok"


def test_event_callback_dispatches_to_handle_event(monkeypatch) -> None:
    """An app_mention event_callback builds the listener and calls handle_event."""
    monkeypatch.setenv("SLACK_EVENTS_AUTH_REQUIRED", "false")
    fake_listener = MagicMock()
    fake_listener.handle_event.return_value = True
    monkeypatch.setattr(server, "_build_listener", lambda: fake_listener)
    r = _client().post("/slack/events", json={
        "type": "event_callback",
        "event_id": "Ev1",
        "event": {"type": "app_mention", "channel": "C0B6ASW9R9V", "text": "@qa-buddy ..."},
    })
    assert r.status_code == 200 and r.json()["processed"] is True
    fake_listener.handle_event.assert_called_once()


def test_non_event_payload_ignored(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_EVENTS_AUTH_REQUIRED", "false")
    r = _client().post("/slack/events", json={"type": "something_else"})
    assert r.status_code == 200 and r.json()["processed"] is False
