"""Thin wrapper around Google Cloud Secret Manager.

The worker fetches selected secrets at startup so concrete values never live
in env vars (12-factor III, Wpromote-org policy on SA JSON keys: Workspace
blocks the download of SA key files for end users, so the JSON lives in Secret
Manager and the worker fetches it via its SA at runtime).

Mirrors Maya's `app.adapters.secrets.SecretManagerService` pattern. Kept small:
one method, one error class. The settings loader catches `SecretResolutionError`
and records the failure on the Settings object so startup validation can
surface a clear "secret X failed to resolve" message in logs.

Lazy import of the Google client so tests + the in-memory dev path don't
need GCP libs to import this module.
"""

from __future__ import annotations

from dataclasses import dataclass


class SecretResolutionError(Exception):
    """Raised when a configured secret cannot be fetched. Carries enough
    structured context (`key`, `secret_name`, `error_code`) that the settings
    diagnostic can log all failures at startup without losing the original
    cause."""

    def __init__(
        self,
        *,
        key: str,
        secret_name: str,
        error_code: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.key = key
        self.secret_name = secret_name
        self.error_code = error_code
        self.message = message


@dataclass
class SecretManagerService:
    """Accesses a single secret value from Google Cloud Secret Manager.

    Args:
        project_id: GCP project that owns the secret.
        version: secret version to fetch ("latest" by default).
        client: optional pre-built `SecretManagerServiceClient` for tests.
    """

    project_id: str
    version: str = "latest"
    client: object | None = None

    def access_secret(self, secret_name: str, *, key: str) -> str:
        """Fetch the secret payload as a UTF-8 string.

        `key` is the destination setting key — only used for error context so
        the caller knows which Settings field the failure belonged to.
        Raises `SecretResolutionError` on any failure (missing project, denied
        IAM, secret not found, etc.) — never returns "" silently.
        """
        if not self.project_id:
            raise SecretResolutionError(
                key=key,
                secret_name=secret_name,
                error_code="secret_manager_project_missing",
                message="GCP project_id is required to resolve secrets.",
            )
        if not secret_name:
            raise SecretResolutionError(
                key=key,
                secret_name=secret_name,
                error_code="secret_name_missing",
                message=f"Secret name for '{key}' is blank.",
            )

        client = self.client or self._build_client()
        resource = f"projects/{self.project_id}/secrets/{secret_name}/versions/{self.version}"
        try:
            response = client.access_secret_version(name=resource)
        except Exception as exc:  # noqa: BLE001 — wrap into our error type
            raise SecretResolutionError(
                key=key,
                secret_name=secret_name,
                error_code="secret_access_failed",
                message=f"Failed to access secret '{secret_name}': {exc}",
            ) from exc

        payload = getattr(getattr(response, "payload", None), "data", None)
        if payload is None:
            raise SecretResolutionError(
                key=key,
                secret_name=secret_name,
                error_code="secret_payload_missing",
                message=f"Secret '{secret_name}' has no payload data.",
            )
        try:
            return payload.decode("utf-8")
        except (AttributeError, UnicodeDecodeError) as exc:
            raise SecretResolutionError(
                key=key,
                secret_name=secret_name,
                error_code="secret_payload_decode_failed",
                message=f"Secret '{secret_name}' payload could not be decoded as UTF-8: {exc}",
            ) from exc

    @staticmethod
    def _build_client():  # pragma: no cover — exercised only against real GCP
        from google.cloud import secretmanager

        return secretmanager.SecretManagerServiceClient()
