"""Cloud Tasks OIDC token verification.

Cloud Tasks invokes the worker carrying an OIDC token signed for the worker's
service account, in the `Authorization: Bearer <token>` header. This module
verifies that token: valid Google signature, expected audience, and the expected
service-account email (with email_verified).

When auth isn't required (local runs set QA_CLOUD_TASKS_AUTH_REQUIRED=false),
verification is skipped. The token verifier is injectable so tests don't need
real Google-signed tokens.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskAuthSettings:
    auth_required: bool
    expected_audience: str = ""
    expected_service_account_email: str = ""


class TaskAuthError(Exception):
    """Raised when the Cloud Tasks OIDC token is missing or invalid."""


def verify_cloud_task_request(
    headers: Mapping[str, str],
    settings: TaskAuthSettings,
    *,
    verifier: Callable[[str, str], dict[str, Any]] | None = None,
) -> None:
    """Verify the incoming request's OIDC token. Raises TaskAuthError on failure.

    No-op when auth isn't required. `verifier(token, audience) -> claims` is
    injectable for tests; the default verifies against Google's public certs.
    """
    if not settings.auth_required:
        return

    # Fail-closed: when auth IS required, the audience AND the service-account
    # email MUST be configured. If either is empty, the checks below silently
    # degrade — passing audience=None tells the verifier to SKIP audience
    # verification, and an empty expected email skips the SA check — so the
    # worker would accept any validly-signed Google OIDC token. Refuse rather
    # than under-verify (defense-in-depth for a misconfigured deploy).
    if not settings.expected_audience:
        raise TaskAuthError(
            "auth_required is set but no OIDC audience is configured; refusing to "
            "verify (would accept any Google-signed token)."
        )
    if not settings.expected_service_account_email:
        raise TaskAuthError(
            "auth_required is set but no expected service-account email is "
            "configured; refusing to verify."
        )

    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth_header.startswith("Bearer "):
        raise TaskAuthError("Missing or malformed Authorization header.")
    token = auth_header[len("Bearer "):].strip()
    if not token:
        raise TaskAuthError("Empty bearer token.")

    verify = verifier or _default_verify
    try:
        claims = verify(token, settings.expected_audience)
    except Exception as exc:  # signature/expiry/audience failures
        raise TaskAuthError(f"OIDC token verification failed: {exc}") from exc

    if not claims.get("email_verified", False):
        raise TaskAuthError("Token email is not verified.")

    email = str(claims.get("email", ""))
    expected = settings.expected_service_account_email
    if expected and email != expected:
        raise TaskAuthError(
            f"Token service account {email!r} does not match expected {expected!r}."
        )


def _default_verify(token: str, audience: str) -> dict[str, Any]:
    """Verify a Google-signed OIDC token against the expected audience."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    request = google_requests.Request()
    # audience=None tells the library to skip the audience check; we pass the
    # configured audience when set so it's enforced in deployed environments.
    return id_token.verify_oauth2_token(token, request, audience=audience or None)
