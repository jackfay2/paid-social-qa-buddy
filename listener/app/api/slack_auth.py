from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import time
from typing import Mapping


class SlackAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class SlackAuthSettings:
    signing_secret: str
    auth_required: bool = True
    max_age_seconds: int = 300


def verify_slack_request(
    *,
    headers: Mapping[str, str],
    body: bytes,
    settings: SlackAuthSettings,
) -> None:
    if not settings.auth_required:
        return

    signing_secret = settings.signing_secret.strip()
    if not signing_secret:
        raise SlackAuthError("slack_signing_secret_missing")

    timestamp_raw = _get_header(headers, "x-slack-request-timestamp")
    signature = _get_header(headers, "x-slack-signature")
    if not timestamp_raw or not signature:
        raise SlackAuthError("slack_signature_headers_missing")

    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        raise SlackAuthError("slack_request_timestamp_invalid") from exc

    now = int(time.time())
    max_age = max(int(settings.max_age_seconds), 60)
    if abs(now - timestamp) > max_age:
        raise SlackAuthError("slack_request_timestamp_expired")

    expected = _build_signature(
        signing_secret=signing_secret, timestamp=timestamp, body=body
    )
    if not hmac.compare_digest(expected, signature):
        raise SlackAuthError("slack_signature_mismatch")


def _build_signature(*, signing_secret: str, timestamp: int, body: bytes) -> str:
    base = b"v0:" + str(timestamp).encode("utf-8") + b":" + body
    digest = hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def _get_header(headers: Mapping[str, str], key: str) -> str:
    for header_key, value in headers.items():
        if header_key.lower() == key.lower():
            return str(value).strip()
    return ""
