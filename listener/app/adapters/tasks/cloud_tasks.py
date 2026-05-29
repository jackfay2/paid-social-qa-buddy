from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any


@dataclass(frozen=True)
class CloudTasksRequest:
    request_id: str
    channel_id: str
    thread_ts: str
    sheet_url: str
    customer_id: str
    campaign_id: str
    campaign_name: str
    requester_user_id: str
    requester_text: str
    route_id: str = ""
    resolved_login_customer_id: str = ""
    resolved_platform: str = ""
    resolved_campaign_type: str = ""
    resolved_template_family: str = ""
    resolved_tab_name: str = ""
    # SOCIAL ADDITION: which QA app this request routes to. "search" (default,
    # backward-compatible) keeps every existing request on the Search path;
    # "social" routes to the Meta worker. Set by the parser/wiring; read by
    # RoutingQAQueue to pick the platform queue.
    qa_app: str = "search"
    entity_filter: dict | None = None


@dataclass(frozen=True)
class CloudTasksEnqueueResult:
    task_name: str
    request_id: str
    outcome: str = "enqueued"


class CloudTasksQAQueue:
    def __init__(
        self,
        *,
        project_id: str,
        location: str,
        queue_name: str,
        worker_url: str,
        service_account_email: str,
        oidc_audience: str = "",
        dispatch_deadline_seconds: int = 1800,
        client: Any | None = None,
    ) -> None:
        self.project_id = project_id.strip()
        self.location = location.strip()
        self.queue_name = queue_name.strip()
        self.worker_url = worker_url.strip()
        self.service_account_email = service_account_email.strip()
        self.oidc_audience = oidc_audience.strip()
        self.dispatch_deadline_seconds = max(int(dispatch_deadline_seconds), 1)
        self._client = client
        self.logger = logging.getLogger("qa_buddy.queue")

    def enqueue(self, payload: CloudTasksRequest) -> CloudTasksEnqueueResult:
        started = time.monotonic()
        request_id = (payload.request_id or "").strip() or self.build_request_id(
            payload
        )
        task_id = _task_id_from_request_id(request_id)

        body = {
            "request_id": request_id,
            "channel_id": payload.channel_id,
            "thread_ts": payload.thread_ts,
            "sheet_url": payload.sheet_url,
            "customer_id": payload.customer_id,
            "campaign_id": payload.campaign_id,
            "campaign_name": payload.campaign_name,
            "requester": {
                "user_id": payload.requester_user_id,
                "source": "slack",
            },
            "requester_text": payload.requester_text,
            "route_id": payload.route_id,
            "resolved_login_customer_id": payload.resolved_login_customer_id,
            "resolved_platform": payload.resolved_platform,
            "resolved_campaign_type": payload.resolved_campaign_type,
            "resolved_template_family": payload.resolved_template_family,
            "resolved_tab_name": payload.resolved_tab_name,
            "entity_filter": payload.entity_filter or {},
        }

        encoded_body = json.dumps(body).encode("utf-8")
        if len(encoded_body) > 80 * 1024:
            self.logger.error(
                "Payload exceeds 80KB size limit",
                extra={
                    "request_id": request_id,
                    "payload_bytes": len(encoded_body),
                    "customer_id": payload.customer_id,
                },
            )
            raise ValueError(
                "❌ Filter too large after retry context; please re-submit with fewer ids"
            )

        client = self._client or self._build_client()
        parent = client.queue_path(self.project_id, self.location, self.queue_name)
        task_name = f"{parent}/tasks/{task_id}"
        task = {
            "name": task_name,
            "http_request": {
                "http_method": "POST",
                "url": self.worker_url,
                "headers": {"Content-Type": "application/json"},
                "body": encoded_body,
                "oidc_token": {
                    "service_account_email": self.service_account_email,
                    "audience": self.oidc_audience or self.worker_url,
                },
            },
            "dispatch_deadline": {"seconds": self.dispatch_deadline_seconds},
        }

        try:
            client.create_task(request={"parent": parent, "task": task})
        except Exception as exc:
            if _is_already_exists_error(exc):
                self.logger.info(
                    "Cloud Task already enqueued",
                    extra={
                    "request_id": request_id,
                    "channel_id": payload.channel_id,
                    "thread_ts": payload.thread_ts,
                    "customer_id": payload.customer_id,
                    "entity_filter_audit": payload.entity_filter or {},
                    "stage": "queue",
                    "outcome": "already_enqueued",
                    "task_name": task_name,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return CloudTasksEnqueueResult(
                    task_name=task_name,
                    request_id=request_id,
                    outcome="already_enqueued",
                )
            raise
        self.logger.info(
            "Cloud Task enqueued",
            extra={
                "request_id": request_id,
                "channel_id": payload.channel_id,
                "thread_ts": payload.thread_ts,
                "customer_id": payload.customer_id,
                "entity_filter_audit": payload.entity_filter or {},
                "stage": "queue",
                "outcome": "enqueued",
                "task_name": task_name,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return CloudTasksEnqueueResult(
            task_name=task_name,
            request_id=request_id,
            outcome="enqueued",
        )

    @staticmethod
    def build_request_id(payload: CloudTasksRequest) -> str:
        filter_fingerprint = _entity_filter_fingerprint(payload.entity_filter)
        components = [
            payload.channel_id.strip(),
            payload.thread_ts.strip(),
            payload.sheet_url.strip(),
            payload.customer_id.strip(),
            payload.campaign_id.strip(),
            filter_fingerprint,
        ]
        material = "|".join(components)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"req_{digest[:24]}"

    @staticmethod
    def _build_client():
        from google.cloud import tasks_v2

        return tasks_v2.CloudTasksClient()


def _task_id_from_request_id(request_id: str) -> str:
    normalized = "".join(
        char if char.isalnum() or char == "-" else "-" for char in request_id.lower()
    )
    normalized = normalized.strip("-")
    if not normalized:
        normalized = "req"
    return normalized[:500]


def _is_already_exists_error(exc: Exception) -> bool:
    try:
        from google.api_core.exceptions import AlreadyExists

        if isinstance(exc, AlreadyExists):
            return True
    except Exception:
        pass

    text = str(exc or "").upper()
    return "ALREADY_EXISTS" in text or "ALREADY EXISTS" in text


def _entity_filter_fingerprint(entity_filter: dict | None) -> str:
    if not entity_filter:
        return ""

    normalized: dict[str, list[str]] = {}
    for key in ("campaign_ids", "campaign_names", "ad_group_ids", "ad_ids"):
        raw_values = entity_filter.get(key, [])
        if isinstance(raw_values, (list, tuple)):
            values = [str(value).strip() for value in raw_values if str(value).strip()]
        elif raw_values in (None, ""):
            values = []
        else:
            value = str(raw_values).strip()
            values = [value] if value else []
        normalized[key] = sorted(set(values))

    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))
