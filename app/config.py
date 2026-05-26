"""Environment-driven configuration. 12-factor III: config in env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service
    qa_log_level: str = "INFO"
    qa_service_role: str = "worker"
    qa_bot_initial: str = "QA-BOT"

    # GCP
    gcp_project_id: str = ""
    qa_use_secret_manager: bool = False

    # Slack (worker posts to threads)
    slack_bot_token: str = ""

    # BigQuery (Meta data via Airbyte sync)
    bq_meta_project: str = "polaris-data-317717"

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
    qa_gemini_model: str = "gemini-2.0-flash"
    qa_gemini_confidence_threshold: float = 0.8
    qa_gemini_timeout_seconds: int = 15

    # Worker runtime
    qa_worker_max_runtime_seconds: int = 900

    # Sheets (gspread)
    qa_sheets_auth_mode: str = "adc"  # "adc" | "service_account"
    qa_sheets_worksheet_name: str = ""  # blank -> first sheet
    google_sheets_service_account_file: str = ""
    google_sheets_service_account_json: str = ""


def load_settings() -> Settings:
    return Settings()
