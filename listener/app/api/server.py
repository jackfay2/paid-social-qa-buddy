"""Listener entrypoint — the merged search + social Slack listener.

A focused FastAPI app (NOT a full vendoring of Maya's api/server.py — just the
listener role) that wires her vendored listener components plus our social
routing:

    Slack @-mention  ->  /slack/events  ->  SlackMessageListener.handle_event
        ->  SlackCloudTasksEnqueueService.submit_request
        ->  RoutingQAQueue  ->  search queue (Maya's worker)  or
                                social queue (our Meta worker, by channel)

Config is read from env (no pydantic dependency). Secrets (`SLACK_SIGNING_SECRET`,
`SLACK_BOT_TOKEN`) come from Cloud Run `--set-secrets` (the existing
`test-slack-*` secrets). Deploy as `qa-buddy-listener-social-test`; point the
test Slack app's Events URL here (same-bot approach).

NOTE: `run_store` is a no-op stub here — the enqueue service accesses every
run_store method via `getattr(..., None)`, so the dedup / proceed-cancel
"pending confirmation" features simply don't activate. The core enqueue path
(the @-mention → worker flow) works fully. Wire a real Firestore run store
later to light up dedup.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import parse_qs

import requests
from fastapi import FastAPI, HTTPException, Request

from app.adapters.tasks import CloudTasksQAQueue
from app.api.slack_auth import SlackAuthError, SlackAuthSettings, verify_slack_request
from app.listener.cloud_tasks_service import SlackCloudTasksEnqueueService
from app.listener.platform_router import RoutingQAQueue
from app.listener.slack_listener import SlackEventDeduper, SlackMessageListener

logging.basicConfig(level=os.environ.get("QA_LOG_LEVEL", "INFO"))
_LOGGER = logging.getLogger("qa_buddy.listener")

app = FastAPI(title="Paid Social QA Buddy — Listener (search + social)")

_DEDUPER = SlackEventDeduper()


# --- config (env) ----------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _social_channel_ids() -> set[str]:
    raw = _env("SOCIAL_CHANNEL_IDS")
    return {c.strip() for c in raw.split(",") if c.strip()}


# --- run store stub (dedup/pending features no-op without these methods) ----


class _NoopRunStore:
    """Satisfies the enqueue service's optional run_store contract by having
    none of the optional methods — every `getattr(run_store, m, None)` returns
    None, so dedup/pending-confirmation paths are skipped. Core enqueue works."""


# --- Slack poster (the `say` callable) -------------------------------------


def _post_to_slack(*, channel_id: str, thread_ts: str, text: str) -> None:
    token = _env("SLACK_BOT_TOKEN")
    if not token:
        _LOGGER.warning("SLACK_BOT_TOKEN missing; cannot post ack")
        return
    body = {"channel": channel_id, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=utf-8"},
            json=body, timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            _LOGGER.warning("slack post not ok: %s", data.get("error"))
    except Exception as exc:  # noqa: BLE001 — never let a Slack post crash intake
        _LOGGER.warning("slack post failed: %s", exc)


# --- wiring ----------------------------------------------------------------


def _build_search_queue() -> CloudTasksQAQueue:
    """Maya's Search path — kept faithful so the merged listener routes Search
    exactly as hers. Not exercised by our social-channel test."""
    return CloudTasksQAQueue(
        project_id=_env("SEARCH_QUEUE_PROJECT", "prj-prd-ai-ppc-qa-pkph"),
        location=_env("SEARCH_QUEUE_LOCATION", "us-west1"),
        queue_name=_env("SEARCH_QUEUE_NAME", "qa-buddy-runs"),
        worker_url=_env("SEARCH_WORKER_URL"),
        service_account_email=_env("CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL"),
        oidc_audience=_env("SEARCH_WORKER_URL"),
    )


def _build_social_queue() -> CloudTasksQAQueue:
    return CloudTasksQAQueue(
        project_id=_env("SOCIAL_QUEUE_PROJECT", "prj-prd-ai-ppc-qa-pkph"),
        location=_env("SOCIAL_QUEUE_LOCATION", "us-west1"),
        queue_name=_env("SOCIAL_QUEUE_NAME", "qa-buddy-runs-social-test"),
        worker_url=_env("SOCIAL_WORKER_URL"),
        service_account_email=_env("CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL"),
        oidc_audience=_env("SOCIAL_WORKER_AUDIENCE") or _env("SOCIAL_WORKER_URL"),
    )


def _build_listener() -> SlackMessageListener:
    router = RoutingQAQueue(
        search_queue=_build_search_queue(),
        social_queue=_build_social_queue(),
        social_channel_ids=_social_channel_ids(),
    )
    service = SlackCloudTasksEnqueueService(
        queue=router,
        run_store=_NoopRunStore(),
        routing_enabled=False,   # social skips MCC routing; search routing off in test
        mcc_routes=None,
        retry_window_minutes=int(_env("QA_RETRY_WINDOW_MINUTES", "15") or "15"),
    )
    return SlackMessageListener(
        service=service,
        bot_mention=_env("SLACK_BOT_MENTION", "@qa-buddy"),
        bot_user_id=_env("SLACK_BOT_USER_ID"),
        event_deduper=_DEDUPER,
    )


# --- endpoints --------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, object]:
    return {
        "status": "ready",
        "role": "listener",
        "social_channel_ids": sorted(_social_channel_ids()),
        "social_queue": _env("SOCIAL_QUEUE_NAME", "qa-buddy-runs-social-test"),
        "social_worker_url": _env("SOCIAL_WORKER_URL"),
    }


@app.get("/slack/events")
def slack_events_get() -> dict[str, bool]:
    return {"ok": True}


@app.post("/slack/events")
async def slack_events(request: Request) -> dict[str, object]:
    body = await request.body()
    auth = SlackAuthSettings(
        signing_secret=_env("SLACK_SIGNING_SECRET"),
        auth_required=_env("SLACK_EVENTS_AUTH_REQUIRED", "true").lower() != "false",
    )
    try:
        verify_slack_request(headers=request.headers, body=body, settings=auth)
    except SlackAuthError as exc:
        raise HTTPException(status_code=401, detail={"error_code": "slack_auth_failed", "message": str(exc)}) from exc

    payload = _parse_body(body)
    event_type = str(payload.get("type") or "")
    if event_type == "url_verification":
        return {"challenge": payload.get("challenge", "")}
    if event_type != "event_callback":
        return {"ok": True, "processed": False}

    event = payload.get("event")
    if not isinstance(event, dict) or str(event.get("type") or "") not in {"message", "app_mention"}:
        return {"ok": True, "processed": False}

    channel_id = str(event.get("channel") or "")

    def _say(**kwargs) -> None:
        _post_to_slack(
            channel_id=channel_id,
            thread_ts=str(kwargs.get("thread_ts") or ""),
            text=str(kwargs.get("text") or ""),
        )

    processed = _build_listener().handle_event(
        event=event, event_id=str(payload.get("event_id") or ""), say=_say,
    )
    return {"ok": True, "processed": processed}


def _parse_body(body: bytes) -> dict:
    if not body:
        return {}
    text = body.decode("utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        form = parse_qs(text, keep_blank_values=True)
        if "payload" in form:
            parsed = json.loads((form.get("payload") or [""])[0])
        else:
            parsed = {k: (v[0] if v else "") for k, v in form.items()}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail={"error_code": "invalid_json"})
    return parsed
