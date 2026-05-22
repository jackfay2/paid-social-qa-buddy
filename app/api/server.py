"""FastAPI app entry point. Worker role only — listener lives in the Search repo."""

from __future__ import annotations

import logging
import signal

from fastapi import FastAPI, HTTPException, Request

from app.config import load_settings
from app.logging_config import configure_logging

_BOOT_SETTINGS = load_settings()
configure_logging(_BOOT_SETTINGS.qa_log_level)
_LOGGER = logging.getLogger("paid_social_qa_buddy.worker")

app = FastAPI(
    title="Paid Social QA Buddy — Meta Worker",
    description=(
        "Meta-platform QA worker. Receives tasks from the qa-buddy-runs-social Cloud "
        "Tasks queue (enqueued by the shared listener in Maya's repo), reads Meta data "
        "from BigQuery, runs checks, writes results back to the QA sheet, and posts a "
        "summary to the original Slack thread."
    ),
    version="0.1.0",
)


def _shutdown_handler(signum: int, frame: object) -> None:
    """SIGTERM handler. 12-factor IX: maximize robustness via graceful shutdown."""
    _LOGGER.info(
        "shutdown_signal_received",
        extra={"signal": signum, "stage": "lifecycle"},
    )
    # Cloud Run sends SIGTERM ~10 seconds before kill. Uvicorn handles connection
    # draining; in-flight work should mark its run as interrupted in Firestore so
    # Cloud Tasks can retry cleanly.


signal.signal(signal.SIGTERM, _shutdown_handler)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, object]:
    settings = load_settings()
    return {
        "status": "ready",
        "service_role": settings.qa_service_role,
        "config": {
            "bq_meta_project": settings.bq_meta_project,
            "polaris_api_url": settings.polaris_api_url,
            "qa_run_store_backend": settings.qa_run_store_backend,
        },
    }


@app.post("/tasks/qa/run")
def qa_run_task(request: Request) -> dict[str, str]:
    """Stub worker endpoint. Real orchestration lands when adapters are wired."""
    raise HTTPException(
        status_code=501,
        detail={
            "error_code": "not_implemented",
            "message": (
                "Worker scaffolded but orchestration is not wired yet. "
                "Phase 1 implementation pending: BigQuery adapter, Polaris adapter, "
                "check registry, sheet I/O, Slack notifier."
            ),
        },
    )
