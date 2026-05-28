"""FastAPI app entry point. Worker role only — the listener lives in Maya's repo.

The /tasks/qa/run endpoint is invoked by the qa-buddy-runs-social Cloud Tasks
queue. It parses the payload, runs orchestration under a timeout, posts the
result to the Slack thread (idempotently), and returns. A run always ends with
a clear outcome (handoff §5.4).
"""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.adapters.slack import SlackClient, SlackPostError
from app.api import wiring
from app.api.models import SocialTaskRequest, SocialTaskResponse
from app.api.task_auth import (
    TaskAuthError,
    TaskAuthSettings,
    verify_cloud_task_request,
)
from app.config import diagnostics_from_settings, load_settings
from app.core.orchestration import OrchestrationRequest, OrchestrationResult
from app.logging_config import configure_logging

_BOOT_SETTINGS = load_settings()
configure_logging(_BOOT_SETTINGS.qa_log_level)
_LOGGER = logging.getLogger("paid_social_qa_buddy.worker")

app = FastAPI(
    title="Paid Social QA Buddy — Meta Worker",
    description=(
        "Meta-platform QA worker. Receives tasks from the qa-buddy-runs-social "
        "Cloud Tasks queue, reads Meta data from BigQuery, runs checks, writes "
        "results to the QA sheet, and posts a summary to the Slack thread."
    ),
    version="0.1.0",
)


def _shutdown_handler(signum: int, frame: object) -> None:
    """SIGTERM handler. 12-factor IX: graceful shutdown on Cloud Run recycle."""
    _LOGGER.info("shutdown_signal_received", extra={"signal": signum})


signal.signal(signal.SIGTERM, _shutdown_handler)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness probe. Cloud Run gates traffic on this (CLAUDE.md: trust
    /readyz for rollout health, not CLI success text).

    Returns 503 when startup config is broken — most importantly when a Secret
    Manager indirection failed to resolve. Without this, a revision with a bad
    secret name or missing `roles/secretmanager.secretAccessor` IAM would boot
    green and then fail per-request the first time it needs the Sheets SA JSON.
    Failing the readiness probe instead keeps the broken revision from ever
    taking traffic.

    The response never includes secret *values* — only the secret name,
    destination key, and error code/message, which are safe to surface.
    """
    try:
        settings = load_settings()
        diagnostics = diagnostics_from_settings(settings)
    except Exception as exc:  # noqa: BLE001 — a probe must answer, not 500
        _LOGGER.error("readyz_settings_load_failed", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "error_code": "settings_load_failed",
                "message": str(exc),
            },
        )

    secret_errors = [
        {
            "key": err.key,
            "secret_name": err.secret_name,
            "error_code": err.error_code,
            "message": err.message,
        }
        for err in diagnostics.secret_resolution_errors
    ]
    ready = not secret_errors

    if not ready:
        _LOGGER.error(
            "readyz_not_ready",
            extra={
                "error_count": len(secret_errors),
                "error_codes": [e["error_code"] for e in secret_errors],
            },
        )

    body: dict[str, object] = {
        "status": "ready" if ready else "not_ready",
        "service_role": settings.qa_service_role,
        "config": {
            "bq_meta_project": settings.bq_meta_project,
            "polaris_api_url": settings.polaris_api_url,
            "qa_run_store_backend": settings.qa_run_store_backend,
            "cloud_tasks_auth_required": settings.qa_cloud_tasks_auth_required,
        },
        "secrets": {
            "secret_manager_enabled": diagnostics.secret_manager_enabled,
            "fetched_keys": diagnostics.fetched_secret_keys,
            "errors": secret_errors,
        },
    }
    return JSONResponse(status_code=200 if ready else 503, content=body)


@app.post("/tasks/qa/run", response_model=SocialTaskResponse)
def qa_run_task(payload: SocialTaskRequest, request: Request) -> SocialTaskResponse:
    settings = load_settings()
    _verify_task_auth(request, settings)

    service = wiring.build_orchestration_service(settings)
    notifier = wiring.build_slack_client(settings)

    orchestration_request = OrchestrationRequest(
        request_id=payload.request_id,
        account_id=payload.account_id,
        campaign_id=payload.campaign_id,
        campaign_name=payload.campaign_name,
        sheet_url=payload.sheet_url,
        thread_ts=payload.thread_ts,
        channel_id=payload.channel_id,
    )

    result = _run_with_timeout(
        service, orchestration_request, settings.qa_worker_max_runtime_seconds
    )
    if result is None:
        _LOGGER.error(
            "worker_timeout",
            extra={"request_id": payload.request_id, "stage": "execution"},
        )
        # 500 so Cloud Tasks retries; orchestration dedup makes the retry safe.
        return JSONResponse(
            status_code=500,
            content={
                "status": "retry",
                "message": "Run exceeded worker max runtime; retry task.",
                "request_id": payload.request_id,
            },
        )

    _post_result_to_slack(service, notifier, payload, result)

    return SocialTaskResponse(
        status=result.status,
        message=result.message,
        run_id=result.run_id,
        request_id=payload.request_id,
        error_code=result.error_code,
    )


def _verify_task_auth(request: Request, settings) -> None:
    """Verify the Cloud Tasks OIDC token. No-op when auth isn't required (local)."""
    auth_settings = TaskAuthSettings(
        auth_required=settings.qa_cloud_tasks_auth_required,
        expected_audience=settings.qa_cloud_tasks_oidc_audience,
        expected_service_account_email=settings.qa_cloud_tasks_service_account_email,
    )
    try:
        verify_cloud_task_request(request.headers, auth_settings)
    except TaskAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "task_auth_failed", "message": str(exc)},
        ) from exc


def _run_with_timeout(
    service, request: OrchestrationRequest, timeout_seconds: int
) -> OrchestrationResult | None:
    """Run orchestration in a worker thread with a hard timeout.

    Returns None on timeout so the caller can ask Cloud Tasks to retry.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(service.run, request)
    try:
        return future.result(timeout=max(int(timeout_seconds), 1))
    except FuturesTimeout:
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _post_result_to_slack(
    service, notifier: SlackClient | None, payload: SocialTaskRequest,
    result: OrchestrationResult,
) -> None:
    """Post the run's terminal message to the Slack thread, idempotently.

    Skips when there's no Slack client (local), no channel/thread, or the
    notification was already sent for this request_id (Cloud Task retry). On a
    transient Slack failure, raises so the task retries; a permanent failure is
    logged (the run itself already completed).
    """
    if notifier is None:
        _LOGGER.info(
            "slack_post_skipped_no_token", extra={"request_id": payload.request_id}
        )
        return
    if not payload.channel_id or not payload.thread_ts:
        return

    run_store = getattr(service, "run_store", None)
    if run_store is not None and run_store.has_worker_notification(payload.request_id):
        _LOGGER.info(
            "slack_post_skipped_duplicate",
            extra={"request_id": payload.request_id},
        )
        return

    try:
        notifier.post_thread_message(
            channel_id=payload.channel_id,
            thread_ts=payload.thread_ts,
            text=result.message,
        )
        if run_store is not None:
            run_store.mark_worker_notification(payload.request_id)
        _LOGGER.info(
            "slack_post_ok",
            extra={"request_id": payload.request_id, "status": result.status},
        )
    except SlackPostError as exc:
        if SlackClient.is_transient(exc):
            _LOGGER.warning(
                "slack_post_transient",
                extra={"request_id": payload.request_id, "error": str(exc)},
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "retry",
                    "error_code": exc.code,
                    "message": "Transient Slack post failure; retry task.",
                },
            ) from exc
        _LOGGER.error(
            "slack_post_terminal",
            extra={"request_id": payload.request_id, "error": str(exc)},
        )
