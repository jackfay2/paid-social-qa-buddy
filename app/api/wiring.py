"""Assembles the orchestration service and Slack client from Settings.

Kept separate from server.py so the endpoint handler can monkeypatch these in
tests without standing up real BigQuery / Sheets / Slack clients.
"""

from __future__ import annotations

from app.adapters.bigquery import (
    BigQueryAccountResolver,
    BigQueryMetaClient,
    BigQueryMetaConfig,
    ResolverConfig,
)
from app.adapters.gemini import GeminiClient, GeminiConfig, StubGeminiClient
from app.adapters.sheets import GoogleSheetsClient, GoogleSheetsConfig
from app.adapters.slack import SlackClient, SlackConfig
from app.adapters.storage import FirestoreRunStore, InMemoryRunStore
from app.checks.registry import run_check
from app.config import Settings
from app.core.orchestration import SocialQAOrchestrationService

# Module-level so the local "memory" backend persists across requests in one
# process. Production uses Firestore and ignores this.
_IN_MEMORY_RUN_STORE = InMemoryRunStore()


def build_run_store(settings: Settings):
    if settings.qa_run_store_backend == "memory":
        return _IN_MEMORY_RUN_STORE
    return FirestoreRunStore(
        collection_name=settings.qa_firestore_collection_name,
        project=settings.gcp_project_id,
    )


def build_gemini_client(settings: Settings):
    """Return a real GeminiClient when an API key is configured; otherwise the
    stub (every text check → Review). Keeps the local/test path runnable
    without a key, and never auto-Passes when Gemini isn't reachable.
    """
    api_key = (settings.gemini_api_key or "").strip()
    if not api_key:
        return StubGeminiClient()
    return GeminiClient(
        config=GeminiConfig(
            api_key=api_key,
            model=settings.qa_gemini_model,
            timeout_seconds=settings.qa_gemini_timeout_seconds,
            confidence_threshold=settings.qa_gemini_confidence_threshold,
        )
    )


def build_orchestration_service(settings: Settings) -> SocialQAOrchestrationService:
    return SocialQAOrchestrationService(
        run_store=build_run_store(settings),
        resolver=BigQueryAccountResolver(
            config=ResolverConfig(project=settings.bq_meta_project)
        ),
        meta_client=BigQueryMetaClient(
            config=BigQueryMetaConfig(project=settings.bq_meta_project)
        ),
        sheet_client=GoogleSheetsClient(
            config=GoogleSheetsConfig(
                worksheet_name=settings.qa_sheets_worksheet_name,
                auth_mode=settings.qa_sheets_auth_mode,
                service_account_file=settings.google_sheets_service_account_file,
                service_account_json=settings.google_sheets_service_account_json,
            )
        ),
        check_runner=run_check,
        gemini_client=build_gemini_client(settings),
        qa_initial=settings.qa_bot_initial,
    )


def build_slack_client(settings: Settings) -> SlackClient | None:
    """Returns a SlackClient, or None when no bot token is configured (local
    runs without Slack). A None client means the worker skips the thread post."""
    if not settings.slack_bot_token.strip():
        return None
    return SlackClient(config=SlackConfig(bot_token=settings.slack_bot_token))
