"""Environment-driven configuration. 12-factor III: config in env.

Secrets policy: the SA JSON for Google Sheets (and other sensitive values
where applicable) lives in **Secret Manager**, never as a downloaded key file.
This mirrors Maya's prod pattern (Workspace blocks SA-JSON-file downloads for
end users by org policy). When `qa_use_secret_manager=True` and a
`*_secret_name` setting is configured, the loader fetches the secret at
startup and swaps the value into the matching plaintext field — code paths
read the plaintext field as if it had come from env.

Resolution failures are non-fatal at module import; they accumulate on
`Settings.secret_resolution_errors` so the server's `/readyz` and startup
logs can show all problems together rather than dying on the first one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.adapters.secrets import SecretManagerService, SecretResolutionError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # SecretResolutionError isn't pydantic-friendly; the
        # secret_resolution_errors list field needs this to round-trip.
        arbitrary_types_allowed=True,
    )

    # Service
    qa_log_level: str = "INFO"
    qa_service_role: str = "worker"
    qa_bot_initial: str = "QA-BOT"

    # GCP
    gcp_project_id: str = ""
    qa_use_secret_manager: bool = False
    qa_secret_manager_version: str = "latest"

    # Slack (worker posts to threads)
    slack_bot_token: str = ""

    # BigQuery (Meta data via Airbyte sync)
    bq_meta_project: str = "polaris-data-317717"

    # Peacock special case: its live Meta data is NOT in the standard sync; it's
    # in a standalone GCP project (unified cross-platform table). When a request
    # resolves to qa_peacock_client_id, the worker routes to PeacockMetaClient
    # reading the project/dataset/table below. Requires the worker SA to have
    # bigquery.dataViewer on qa_peacock_bq_project. Blank client_id disables it.
    qa_peacock_client_id: str = "C22848672"
    qa_peacock_bq_project: str = "nbc-287716"
    qa_peacock_bq_dataset: str = "prod_peacock_final_data"
    qa_peacock_bq_table: str = "creative_and_audience_data"
    # Phase B: the Airtable trafficking mirror (build-time spec). Joined to the
    # performance table per creative by distribution ID (Pamela, 2026-06-03):
    # perf.DistributionID <-> traf.Distribution_, with VersionNumber <-> Version_
    # as the fallback for un-reused historical creatives. Unlocks deterministic
    # creative dimensions (Frame_Size), flight dates, and pre-computed QC flags.
    # Blank table disables the trafficking merge (perf-only Peacock, Phase A).
    qa_peacock_trafficking_dataset: str = "AirTable_v2"
    qa_peacock_trafficking_table: str = "wp_live_trafficking"

    # Polaris (client directory lookup)
    polaris_api_url: str = "https://api.polaris.wpromote.com"
    polaris_api_token: str = ""

    # Cloud Tasks worker auth
    qa_cloud_tasks_auth_required: bool = True
    qa_cloud_tasks_oidc_audience: str = ""
    qa_cloud_tasks_service_account_email: str = ""

    # Firestore run tracking
    qa_run_store_backend: str = "firestore"
    qa_firestore_collection_name: str = "qa_runs"

    # Gemini
    gemini_api_key: str = ""
    qa_gemini_model: str = "gemini-2.5-flash"
    qa_gemini_confidence_threshold: float = 0.8
    # A batched call (up to TEXT_CHECK_AD_CAP ads x several text checks) on
    # gemini-2.5-flash takes ~30-40s with thinking on; even with thinking
    # disabled we leave generous headroom so a slow batch never times out and
    # fail-safes the whole job to Review. Worker hard-stop is 720s, so 60s is safe.
    qa_gemini_timeout_seconds: int = 60

    # Worker runtime — brief specifies a 12-minute hard stop (720s).
    qa_worker_max_runtime_seconds: int = 720

    # Sheets (gspread)
    qa_sheets_auth_mode: str = "adc"  # "adc" | "service_account" | "auto"
    qa_sheets_worksheet_name: str = ""  # blank -> first sheet
    google_sheets_service_account_file: str = ""
    google_sheets_service_account_json: str = ""

    # --- Secret Manager indirection ------------------------------------------
    # When qa_use_secret_manager=True, the *_SECRET_NAME env vars below are
    # resolved to their plaintext equivalents at load time and swapped into the
    # matching fields above. Settings keys map secret-name field -> destination
    # field; see _SECRET_MAP. Add new entries here when more secrets move.
    google_sheets_service_account_json_secret_name: str = ""

    # Populated by _resolve_secrets at load time — list of resolution failures
    # surfaced to /readyz and startup logs. Empty in tests that don't touch SM.
    secret_resolution_errors: list[SecretResolutionError] = []


# Map: which secret-name field on Settings fills which plaintext field.
# Adding a new secret = one entry here + one *_SECRET_NAME field above.
_SECRET_MAP: dict[str, str] = {
    "google_sheets_service_account_json_secret_name": "google_sheets_service_account_json",
}


@dataclass
class SettingsDiagnostics:
    """Snapshot of resolved-secret state for /readyz logging."""

    secret_manager_enabled: bool
    fetched_secret_keys: list[str] = field(default_factory=list)
    secret_resolution_errors: list[SecretResolutionError] = field(default_factory=list)


def load_settings() -> Settings:
    """Load env-driven settings, then resolve any Secret Manager indirections.

    Failures are captured on `settings.secret_resolution_errors` rather than
    raised — startup validation logs them and `/readyz` reports them, so a
    misconfigured secret name doesn't crash the whole process.
    """
    settings = Settings()
    _resolve_secrets(settings)
    return settings


def load_settings_diagnostics() -> SettingsDiagnostics:
    settings = load_settings()
    return diagnostics_from_settings(settings)


def diagnostics_from_settings(settings: Settings) -> SettingsDiagnostics:
    fetched = [
        dest
        for secret_field, dest in _SECRET_MAP.items()
        if getattr(settings, secret_field, "")
        and getattr(settings, dest, "")
        and not any(
            err.key == dest for err in settings.secret_resolution_errors
        )
    ]
    return SettingsDiagnostics(
        secret_manager_enabled=settings.qa_use_secret_manager,
        fetched_secret_keys=fetched,
        secret_resolution_errors=list(settings.secret_resolution_errors),
    )


def _resolve_secrets(
    settings: Settings,
    *,
    service: SecretManagerService | None = None,
) -> None:
    """Populate settings fields from Secret Manager.

    Idempotent: a destination field that's already non-blank (e.g. set
    directly via env) is NOT overwritten — explicit env always wins. That
    matches Maya's behavior and keeps the local-dev path simple (set the JSON
    via .env, don't bother with Secret Manager).

    `service` is injectable for tests.
    """
    if not settings.qa_use_secret_manager:
        return

    if not settings.gcp_project_id:
        settings.secret_resolution_errors.append(
            SecretResolutionError(
                key="gcp_project_id",
                secret_name="",
                error_code="secret_manager_project_missing",
                message="GCP_PROJECT_ID is required when qa_use_secret_manager=True.",
            )
        )
        return

    sm = service or SecretManagerService(
        project_id=settings.gcp_project_id,
        version=settings.qa_secret_manager_version,
    )

    for secret_field, dest_field in _SECRET_MAP.items():
        secret_name = (getattr(settings, secret_field, "") or "").strip()
        if not secret_name:
            continue  # no secret configured for this destination
        if (getattr(settings, dest_field, "") or "").strip():
            continue  # already set in env — explicit wins

        try:
            value = sm.access_secret(secret_name, key=dest_field)
        except SecretResolutionError as exc:
            settings.secret_resolution_errors.append(exc)
            continue
        setattr(settings, dest_field, value)
