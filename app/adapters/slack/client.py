"""Slack thread poster. Wraps chat.postMessage for the worker's summary posts.

The worker uses this to post the QA summary back to the original Slack thread
once a run completes (or to surface an error if it fails). One primitive:
post_thread_message(channel_id, thread_ts, text).

Error model:
  - SlackPostError carries a `.code` attribute used by orchestration to decide
    retry vs terminal. The categorization matches the Search side's
    _is_transient_slack_error helper: 5xx HTTP and a handful of named API
    errors are transient (retry the Cloud Task); everything else is terminal.

Auth header: `Authorization: Bearer <bot_token>` (NOT Token — Slack uses Bearer,
unlike Polaris). Bot token comes from the shared `SLACK_BOT_TOKEN` (same one
used by Maya's listener — Brad confirmed shared Slack app).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

_logger = logging.getLogger("paid_social_qa_buddy.slack")

_SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_DEFAULT_TIMEOUT_SECONDS = 15

# Slack API error codes that should trigger a Cloud Task retry rather than
# a permanent failure. Matches the Search side's classification.
_TRANSIENT_API_ERROR_CODES = frozenset({
    "ratelimited",
    "internal_error",
    "slack_api_error",
})


class SlackPostError(Exception):
    """Raised when a Slack post fails.

    `code` is a stable string the orchestration layer can switch on to decide
    retry vs terminal. Common values:
      - "missing_token"               — config error, terminal
      - "slack_api_error"             — network/transport error, transient
      - "slack_http_5xx" (any 5xx)    — server-side error, transient
      - "slack_http_4xx" (any 4xx)    — client error, terminal
      - "invalid_response"            — non-JSON response, terminal
      - Slack-returned codes like "channel_not_found", "ratelimited",
        "invalid_auth" — varied; see is_transient() for the rules.
    """

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        full = f"{code}: {message}" if message else code
        super().__init__(full)


@dataclass(frozen=True)
class SlackConfig:
    """Slack client settings.

    bot_token: xoxb-... token for the shared @qa-buddy app.
    timeout_seconds: Per-request timeout.
    """
    bot_token: str
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS


class SlackClient:
    """Concrete SlackClient backed by httpx against chat.postMessage."""

    def __init__(
        self,
        config: SlackConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not config.bot_token:
            raise SlackPostError(
                "missing_token", "SLACK_BOT_TOKEN is required."
            )
        self.config = config
        # Inject http_client in tests; default builds a fresh httpx.Client.
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)

    def post_thread_message(
        self, *, channel_id: str, thread_ts: str = "", text: str,
    ) -> None:
        """Post a message to a Slack channel, optionally in a thread.

        When `thread_ts` is non-blank the message is posted as a threaded reply;
        when blank it is posted as a top-level channel message (Slack rejects an
        empty thread_ts, so we omit the key rather than send "").

        Raises SlackPostError on any failure. Caller decides retry/terminal
        based on the error's `.code` (use is_transient() for the canonical
        classification).
        """
        headers = {
            "Authorization": f"Bearer {self.config.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {
            "channel": channel_id,
            "text": text,
        }
        if thread_ts and thread_ts.strip():
            body["thread_ts"] = thread_ts

        try:
            response = self._http.post(
                _SLACK_POST_MESSAGE_URL, headers=headers, json=body,
            )
        except httpx.RequestError as exc:
            raise SlackPostError("slack_api_error", str(exc)) from exc

        if response.status_code >= 500:
            raise SlackPostError(
                f"slack_http_{response.status_code}",
                "Slack server-side error.",
            )
        if response.status_code >= 400:
            raise SlackPostError(
                f"slack_http_{response.status_code}",
                f"Slack returned HTTP {response.status_code}.",
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise SlackPostError(
                "invalid_response", "Slack returned non-JSON response.",
            ) from exc

        if not isinstance(data, dict) or not data.get("ok"):
            error_code = ""
            if isinstance(data, dict):
                error_code = str(data.get("error") or "").strip()
            error_code = error_code or "unknown_slack_error"
            raise SlackPostError(error_code, "Slack API returned ok=false.")

        _logger.info(
            "slack_thread_message_posted",
            extra={
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "text_length": len(text),
            },
        )

    @staticmethod
    def is_transient(error: SlackPostError) -> bool:
        """Return True if the error should trigger a Cloud Task retry.

        Matches the Search side's _is_transient_slack_error logic so behavior
        is consistent across workers:
          - Any 5xx HTTP response is transient.
          - "ratelimited", "internal_error", "slack_api_error" are transient.
          - Everything else (4xx HTTP, invalid_auth, channel_not_found, etc.)
            is terminal.
        """
        code = error.code or ""
        if code.startswith("slack_http_5"):
            return True
        return code in _TRANSIENT_API_ERROR_CODES
