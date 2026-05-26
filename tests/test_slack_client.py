"""Unit tests for SlackClient.

Uses a mocked httpx Client so tests don't hit live Slack.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.adapters.slack.client import (
    SlackClient,
    SlackConfig,
    SlackPostError,
)


def _make_response(
    status_code: int = 200, json_data: dict | None = None,
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_data if json_data is not None else {"ok": True}
    return response


def _make_http_client(response: MagicMock) -> MagicMock:
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = response
    return http


def _make_client(http: MagicMock) -> SlackClient:
    return SlackClient(
        config=SlackConfig(bot_token="xoxb-test-token"),
        http_client=http,
    )


# --- Auth errors -----------------------------------------------------------


def test_raises_when_bot_token_missing() -> None:
    with pytest.raises(SlackPostError) as excinfo:
        SlackClient(config=SlackConfig(bot_token=""), http_client=MagicMock())
    assert excinfo.value.code == "missing_token"


# --- Auth header pattern ---------------------------------------------------


def test_uses_bearer_auth_header() -> None:
    """Slack uses Bearer, unlike Polaris which uses Token."""
    http = _make_http_client(_make_response(200, {"ok": True}))
    client = _make_client(http)

    client.post_thread_message(
        channel_id="C123",
        thread_ts="1716334567.123456",
        text="hi",
    )

    headers = http.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer xoxb-test-token"


# --- Success path ----------------------------------------------------------


def test_post_thread_message_success() -> None:
    http = _make_http_client(_make_response(200, {"ok": True, "ts": "1.2"}))
    client = _make_client(http)

    client.post_thread_message(
        channel_id="C123",
        thread_ts="1716334567.123456",
        text="QA complete | Pass 10 | Fix 1",
    )

    # Verify the body payload shape.
    body = http.post.call_args.kwargs["json"]
    assert body["channel"] == "C123"
    assert body["thread_ts"] == "1716334567.123456"
    assert body["text"] == "QA complete | Pass 10 | Fix 1"


# --- HTTP errors -----------------------------------------------------------


def test_http_500_raises_transient_error() -> None:
    http = _make_http_client(_make_response(500, {}))
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "slack_http_500"
    assert SlackClient.is_transient(excinfo.value) is True


def test_http_503_raises_transient_error() -> None:
    http = _make_http_client(_make_response(503, {}))
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "slack_http_503"
    assert SlackClient.is_transient(excinfo.value) is True


def test_http_400_raises_terminal_error() -> None:
    http = _make_http_client(_make_response(400, {}))
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "slack_http_400"
    assert SlackClient.is_transient(excinfo.value) is False


def test_http_403_raises_terminal_error() -> None:
    http = _make_http_client(_make_response(403, {}))
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "slack_http_403"
    assert SlackClient.is_transient(excinfo.value) is False


# --- Slack API errors (HTTP 200 but ok=false) -----------------------------


def test_slack_api_returns_error_raises_with_code() -> None:
    http = _make_http_client(
        _make_response(200, {"ok": False, "error": "channel_not_found"}),
    )
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "channel_not_found"
    assert SlackClient.is_transient(excinfo.value) is False


def test_slack_ratelimited_is_transient() -> None:
    http = _make_http_client(
        _make_response(200, {"ok": False, "error": "ratelimited"}),
    )
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "ratelimited"
    assert SlackClient.is_transient(excinfo.value) is True


def test_slack_internal_error_is_transient() -> None:
    http = _make_http_client(
        _make_response(200, {"ok": False, "error": "internal_error"}),
    )
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "internal_error"
    assert SlackClient.is_transient(excinfo.value) is True


def test_slack_invalid_auth_is_terminal() -> None:
    http = _make_http_client(
        _make_response(200, {"ok": False, "error": "invalid_auth"}),
    )
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "invalid_auth"
    assert SlackClient.is_transient(excinfo.value) is False


def test_missing_error_code_falls_back_to_unknown() -> None:
    http = _make_http_client(_make_response(200, {"ok": False}))
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "unknown_slack_error"


# --- Network / transport errors -------------------------------------------


def test_network_error_raises_transient_slack_api_error() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.side_effect = httpx.ConnectError("Connection refused")
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "slack_api_error"
    assert SlackClient.is_transient(excinfo.value) is True


def test_timeout_raises_transient_slack_api_error() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.side_effect = httpx.ReadTimeout("Timeout")
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "slack_api_error"
    assert SlackClient.is_transient(excinfo.value) is True


# --- Invalid response ------------------------------------------------------


def test_non_json_response_raises_invalid_response() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.side_effect = ValueError("Not JSON")

    http = _make_http_client(response)
    client = _make_client(http)

    with pytest.raises(SlackPostError) as excinfo:
        client.post_thread_message(channel_id="C1", thread_ts="t", text="hi")
    assert excinfo.value.code == "invalid_response"
    assert SlackClient.is_transient(excinfo.value) is False
