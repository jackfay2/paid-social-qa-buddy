"""Settings-loader integration with Secret Manager.

Asserts the contract: when qa_use_secret_manager is true and a *_SECRET_NAME
is configured, the resolver pulls the secret value into the matching
plaintext field. Failures accumulate on secret_resolution_errors rather than
raising — startup validation surfaces them, but the process keeps running.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.secrets import SecretManagerService, SecretResolutionError
from app.config import (
    Settings,
    _resolve_secrets,
    diagnostics_from_settings,
)


def _service_returning(value: str) -> SecretManagerService:
    sm = MagicMock(spec=SecretManagerService)
    sm.access_secret.return_value = value
    return sm


# --- happy path ------------------------------------------------------------


def test_resolves_sheets_sa_json_into_settings() -> None:
    settings = Settings(
        qa_use_secret_manager=True,
        gcp_project_id="my-proj",
        google_sheets_service_account_json_secret_name="sheets-sa-json",
    )
    sm = _service_returning('{"type": "service_account"}')

    _resolve_secrets(settings, service=sm)

    assert settings.google_sheets_service_account_json == '{"type": "service_account"}'
    sm.access_secret.assert_called_once_with(
        "sheets-sa-json", key="google_sheets_service_account_json"
    )
    assert settings.secret_resolution_errors == []


def test_diagnostics_reports_fetched_keys() -> None:
    settings = Settings(
        qa_use_secret_manager=True,
        gcp_project_id="my-proj",
        google_sheets_service_account_json_secret_name="sheets-sa-json",
    )
    _resolve_secrets(settings, service=_service_returning("payload"))

    diag = diagnostics_from_settings(settings)
    assert diag.secret_manager_enabled is True
    assert "google_sheets_service_account_json" in diag.fetched_secret_keys
    assert diag.secret_resolution_errors == []


# --- disabled / no-op ------------------------------------------------------


def test_disabled_means_no_calls() -> None:
    settings = Settings(
        qa_use_secret_manager=False,
        gcp_project_id="my-proj",
        google_sheets_service_account_json_secret_name="sheets-sa-json",
    )
    sm = _service_returning("would-be-fetched")

    _resolve_secrets(settings, service=sm)

    assert settings.google_sheets_service_account_json == ""
    sm.access_secret.assert_not_called()


def test_no_secret_name_means_skipped() -> None:
    settings = Settings(
        qa_use_secret_manager=True,
        gcp_project_id="my-proj",
        # google_sheets_service_account_json_secret_name left blank
    )
    sm = _service_returning("never-called")

    _resolve_secrets(settings, service=sm)

    sm.access_secret.assert_not_called()
    assert settings.google_sheets_service_account_json == ""


def test_explicit_env_value_wins_over_secret() -> None:
    """If the plaintext field is set directly (e.g. via .env for local dev),
    Secret Manager is NOT consulted for that field. Explicit env always wins."""
    settings = Settings(
        qa_use_secret_manager=True,
        gcp_project_id="my-proj",
        google_sheets_service_account_json_secret_name="sheets-sa-json",
        google_sheets_service_account_json='{"already": "set"}',
    )
    sm = _service_returning("would-be-overridden")

    _resolve_secrets(settings, service=sm)

    assert settings.google_sheets_service_account_json == '{"already": "set"}'
    sm.access_secret.assert_not_called()


# --- failure handling ------------------------------------------------------


def test_missing_project_recorded_not_raised() -> None:
    settings = Settings(
        qa_use_secret_manager=True,
        gcp_project_id="",
        google_sheets_service_account_json_secret_name="sheets-sa-json",
    )
    sm = _service_returning("never-called")

    # Must not raise — failures land in secret_resolution_errors.
    _resolve_secrets(settings, service=sm)

    sm.access_secret.assert_not_called()
    assert len(settings.secret_resolution_errors) == 1
    err = settings.secret_resolution_errors[0]
    assert err.error_code == "secret_manager_project_missing"


def test_secret_access_failure_recorded_not_raised() -> None:
    settings = Settings(
        qa_use_secret_manager=True,
        gcp_project_id="my-proj",
        google_sheets_service_account_json_secret_name="sheets-sa-json",
    )
    sm = MagicMock(spec=SecretManagerService)
    sm.access_secret.side_effect = SecretResolutionError(
        key="google_sheets_service_account_json",
        secret_name="sheets-sa-json",
        error_code="secret_access_failed",
        message="boom",
    )

    _resolve_secrets(settings, service=sm)

    assert settings.google_sheets_service_account_json == ""
    assert len(settings.secret_resolution_errors) == 1
    assert settings.secret_resolution_errors[0].error_code == "secret_access_failed"


def test_diagnostics_excludes_failed_keys() -> None:
    """A failed resolution should NOT appear in fetched_secret_keys."""
    settings = Settings(
        qa_use_secret_manager=True,
        gcp_project_id="my-proj",
        google_sheets_service_account_json_secret_name="sheets-sa-json",
    )
    sm = MagicMock(spec=SecretManagerService)
    sm.access_secret.side_effect = SecretResolutionError(
        key="google_sheets_service_account_json",
        secret_name="sheets-sa-json",
        error_code="secret_access_failed",
        message="boom",
    )
    _resolve_secrets(settings, service=sm)

    diag = diagnostics_from_settings(settings)
    assert "google_sheets_service_account_json" not in diag.fetched_secret_keys
    assert len(diag.secret_resolution_errors) == 1
