from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time

from app.adapters.tasks import CloudTasksQAQueue, CloudTasksRequest
from app.listener.slack_models import SlackParsedRequest, SlackSubmitResult
from app.listener.slack_messages import (
    format_accepted_message,
    format_duplicate_cancelled_message,
    format_duplicate_reminder_message,
    format_duplicate_warning_message,
    format_execution_error_message,
    format_filter_cancelled_message,
    format_filter_expired_message,
    format_unknown_template_family_message,
)
from app.routing import (
    DEFAULT_CAMPAIGN_TYPE,
    DEFAULT_PLATFORM,
    DEFAULT_TEMPLATE_FAMILY,
    MccRouteConfig,
    resolve_mcc_route,
)


@dataclass
class SlackCloudTasksEnqueueService:
    queue: CloudTasksQAQueue
    run_store: object
    routing_enabled: bool = False
    routing_enforcement: str = "warn"
    mcc_routes: list[MccRouteConfig] | None = None
    retry_window_minutes: int = 15

    def __post_init__(self) -> None:
        self.logger = logging.getLogger("qa_buddy.listener.service")

    def submit_request(self, request: SlackParsedRequest) -> SlackSubmitResult:
        started = time.monotonic()

        template_family, template_family_source, template_rejected = (
            self._validate_template_family(request)
        )
        if template_rejected is not None:
            return template_rejected

        payload = self._request_to_payload(
            request,
            template_family=template_family,
        )
        request_id = CloudTasksQAQueue.build_request_id(payload)
        payload, rejected = self._apply_routing(
            payload,
            customer_id=request.customer_id,
            request_id=request_id,
            started=started,
            context={
                "channel_id": request.channel_id,
                "thread_ts": request.thread_ts,
                "customer_id": request.customer_id,
                "entity_filter_audit": payload.entity_filter or {},
                "stage": "routing",
                "template_family": template_family,
                "template_family_source": template_family_source,
            },
        )
        if rejected is not None:
            return rejected
        payload = CloudTasksRequest(
            request_id=request_id,
            channel_id=payload.channel_id,
            thread_ts=payload.thread_ts,
            sheet_url=payload.sheet_url,
            customer_id=payload.customer_id,
            campaign_id=payload.campaign_id,
            campaign_name=payload.campaign_name,
            requester_user_id=payload.requester_user_id,
            requester_text=payload.requester_text,
            route_id=payload.route_id,
            resolved_login_customer_id=payload.resolved_login_customer_id,
            resolved_platform=payload.resolved_platform,
            resolved_campaign_type=payload.resolved_campaign_type,
            resolved_template_family=payload.resolved_template_family,
            resolved_tab_name=payload.resolved_tab_name,
            entity_filter=payload.entity_filter or {},
        )

        try:
            duplicate_run = self._find_recent_completed(
                customer_id=request.customer_id,
                campaign_id=request.campaign_id,
            )
        except Exception:
            duplicate_run = None
            self.logger.exception(
                "Duplicate-check lookup failed; continuing without duplicate guard",
                extra={
                    "request_id": request_id,
                    "channel_id": request.channel_id,
                    "thread_ts": request.thread_ts,
                    "customer_id": request.customer_id,
                    "entity_filter_audit": payload.entity_filter or {},
                    "stage": "duplicate_confirm",
                    "outcome": "duplicate_lookup_failed_open",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )

        if duplicate_run is not None and int(getattr(duplicate_run, "error_count", 0)) == 0:
            created = self._create_pending_confirmation(payload)
            if not created:
                self.logger.info(
                    "Duplicate confirmation already pending; suppressing duplicate prompt",
                    extra={
                        "request_id": request_id,
                        "channel_id": request.channel_id,
                        "thread_ts": request.thread_ts,
                        "customer_id": request.customer_id,
                        "entity_filter_audit": payload.entity_filter or {},
                        "stage": "duplicate_confirm",
                        "outcome": "confirmation_already_pending",
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return SlackSubmitResult(outcome="ignored")

            self.logger.info(
                "Duplicate run detected; requesting confirmation",
                extra={
                    "request_id": request_id,
                    "channel_id": request.channel_id,
                    "thread_ts": request.thread_ts,
                    "customer_id": request.customer_id,
                    "entity_filter_audit": payload.entity_filter or {},
                    "stage": "duplicate_confirm",
                    "outcome": "confirmation_requested",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return SlackSubmitResult(
                outcome="duplicate_confirmation_requested",
                message=format_duplicate_warning_message(),
                request_id=request_id,
            )

        queue_result = self.queue.enqueue(payload)
        self.logger.info(
            "Enqueued Cloud Task from Slack listener",
            extra={
                "request_id": request_id,
                "channel_id": request.channel_id,
                "thread_ts": request.thread_ts,
                "customer_id": request.customer_id,
                "entity_filter_audit": payload.entity_filter or {},
                "task_name": queue_result.task_name,
                "stage": "queue",
                "outcome": queue_result.outcome,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return SlackSubmitResult(
            outcome=queue_result.outcome,
            request_id=queue_result.request_id,
        )


    def handle_thread_reply(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        text: str,
    ) -> SlackSubmitResult:
        started = time.monotonic()
        normalized = (text or "").strip().lower()
        pending = self._get_pending_confirmation(
            channel_id=channel_id, thread_ts=thread_ts
        )
        if pending is None:
            if normalized == "retry":
                retry_result = self._retry_latest_failed_run(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    user_id=user_id,
                    started=started,
                )
                if retry_result is not None:
                    return retry_result
            self.logger.info(
                "Thread reply ignored; no pending confirmation",
                extra={
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "stage": "duplicate_confirm",
                    "outcome": "ignored",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return SlackSubmitResult(outcome="ignored")

        # --- Expiry check (filter-confirmation records with expires_at) ---
        pending_expires_at = getattr(pending, "expires_at", None)
        if pending_expires_at is not None:
            now_utc = datetime.now(timezone.utc)
            # Coerce string fallback
            if isinstance(pending_expires_at, str):
                try:
                    pending_expires_at = datetime.fromisoformat(pending_expires_at)
                except (ValueError, TypeError):
                    pending_expires_at = None
            if pending_expires_at is not None and pending_expires_at < now_utc:
                self._resolve_pending_confirmation(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    resolution="expired",
                )
                self.logger.info(
                    "Filter confirmation expired",
                    extra={
                        "request_id": str(pending.request_id),
                        "channel_id": channel_id,
                        "thread_ts": thread_ts,
                        "stage": "filter_confirm",
                        "outcome": "expired",
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return SlackSubmitResult(
                    outcome="expired",
                    message=format_filter_expired_message(),
                )

        if normalized == "proceed":
            payload = CloudTasksRequest(
                request_id=str(pending.request_id),
                channel_id=str(pending.channel_id),
                thread_ts=str(pending.thread_ts),
                sheet_url=str(pending.sheet_url),
                customer_id=str(pending.customer_id),
                campaign_id=str(pending.campaign_id),
                campaign_name=str(pending.campaign_name),
                requester_user_id=str(pending.requester_user_id),
                requester_text=str(pending.requester_text),
                route_id=str(getattr(pending, "route_id", "")),
                resolved_login_customer_id=str(
                    getattr(pending, "resolved_login_customer_id", "")
                ),
                resolved_platform=str(
                    getattr(pending, "resolved_platform", DEFAULT_PLATFORM)
                ),
                resolved_campaign_type=str(
                    getattr(pending, "resolved_campaign_type", DEFAULT_CAMPAIGN_TYPE)
                ),
                resolved_template_family=str(
                    getattr(pending, "resolved_template_family", DEFAULT_TEMPLATE_FAMILY)
                ),
                resolved_tab_name=str(getattr(pending, "resolved_tab_name", "")),
                entity_filter=getattr(pending, "entity_filter", None) or {},
            )
            queue_result = self.queue.enqueue(payload)
            self._resolve_pending_confirmation(
                channel_id=channel_id,
                thread_ts=thread_ts,
                resolution="proceeded",
            )
            self.logger.info(
                "Duplicate confirmation proceeded",
                extra={
                    "request_id": payload.request_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": payload.customer_id,
                    "entity_filter_audit": payload.entity_filter or {},
                    "task_name": queue_result.task_name,
                    "stage": "duplicate_confirm",
                    "outcome": "proceeded",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return SlackSubmitResult(
                outcome="duplicate_proceeded",
                message=format_accepted_message(),
                request_id=payload.request_id,
            )

        if normalized == "cancel":
            self._resolve_pending_confirmation(
                channel_id=channel_id,
                thread_ts=thread_ts,
                resolution="cancelled",
            )
            is_filter_confirm = bool(getattr(pending, "entity_filter", None))
            stage = "filter_confirm" if is_filter_confirm else "duplicate_confirm"
            outcome = "filter_cancelled" if is_filter_confirm else "cancelled"
            self.logger.info(
                "Confirmation cancelled",
                extra={
                    "request_id": str(pending.request_id),
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": str(pending.customer_id),
                    "entity_filter_audit": getattr(pending, "entity_filter", None) or {},
                    "stage": stage,
                    "outcome": outcome,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            if is_filter_confirm:
                return SlackSubmitResult(
                    outcome="filter_cancelled",
                    message=format_filter_cancelled_message(),
                )
            return SlackSubmitResult(
                outcome="duplicate_cancelled",
                message=format_duplicate_cancelled_message(),
            )

        self.logger.info(
            "Duplicate confirmation still pending",
            extra={
                "request_id": str(pending.request_id),
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "customer_id": str(pending.customer_id),
                "entity_filter_audit": getattr(pending, "entity_filter", None) or {},
                "stage": "duplicate_confirm",
                "outcome": "reminder",
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return SlackSubmitResult(
            outcome="duplicate_pending_reminder",
            message=format_duplicate_reminder_message(),
        )

    def _retry_latest_failed_run(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        started: float,
    ) -> SlackSubmitResult | None:
        run = self._find_recent_run_by_thread_ts(thread_ts=thread_ts)
        if run is None or run.status not in {"failed", "rejected"}:
            return None

        if self._is_retry_expired(run):
            self.logger.info(
                "Retry request ignored; latest failed run is expired",
                extra={
                    "source_request_id": str(getattr(run, "request_id", "")),
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": str(getattr(run, "customer_id", "")),
                    "entity_filter_audit": getattr(run, "entity_filter", None) or {},
                    "stage": "retry",
                    "outcome": "expired",
                    "retry_window_minutes": self.retry_window_minutes,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return SlackSubmitResult(
                outcome="expired",
                message=format_filter_expired_message(),
            )

        retry_request_id = self._build_retry_request_id(
            str(getattr(run, "request_id", ""))
        )
        payload = CloudTasksRequest(
            request_id=retry_request_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            sheet_url=str(getattr(run, "sheet_url", "")),
            customer_id=str(getattr(run, "customer_id", "")),
            campaign_id=str(getattr(run, "campaign_id", "")),
            campaign_name=str(getattr(run, "campaign_name", "")),
            requester_user_id=user_id,
            requester_text="retry",
            route_id=str(getattr(run, "route_id", "")),
            resolved_login_customer_id=str(
                getattr(run, "resolved_login_customer_id", "")
            ),
            resolved_platform=str(
                getattr(run, "resolved_platform", DEFAULT_PLATFORM)
            ),
            resolved_campaign_type=str(
                getattr(run, "resolved_campaign_type", DEFAULT_CAMPAIGN_TYPE)
            ),
            resolved_template_family=str(
                getattr(run, "resolved_template_family", DEFAULT_TEMPLATE_FAMILY)
            ),
            resolved_tab_name=str(getattr(run, "resolved_tab_name", "")),
            entity_filter=getattr(run, "entity_filter", None) or {},
        )

        payload, rejected = self._apply_routing(
            payload,
            customer_id=payload.customer_id,
            request_id=payload.request_id,
            started=started,
            context={
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "customer_id": payload.customer_id,
                "entity_filter_audit": payload.entity_filter or {},
                "stage": "retry_routing",
            },
        )
        if rejected is not None:
            return rejected

        queue_result = self.queue.enqueue(payload)
        self.logger.info(
            "Retry request enqueued from thread reply",
            extra={
                "request_id": payload.request_id,
                "source_request_id": str(getattr(run, "request_id", "")),
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "customer_id": payload.customer_id,
                "entity_filter_audit": payload.entity_filter or {},
                "task_name": queue_result.task_name,
                "stage": "retry",
                "outcome": "enqueued",
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return SlackSubmitResult(outcome="retry_enqueued", message=format_accepted_message())


    @staticmethod
    def _request_to_payload(
        request: SlackParsedRequest,
        *,
        template_family: str = DEFAULT_TEMPLATE_FAMILY,
    ) -> CloudTasksRequest:
        return CloudTasksRequest(
            request_id="",
            channel_id=request.channel_id,
            thread_ts=request.thread_ts,
            sheet_url=request.sheet_url,
            customer_id=request.customer_id,
            campaign_id=request.campaign_id,
            campaign_name=request.campaign_name,
            requester_user_id=request.user_id,
            requester_text=request.raw_text,
            resolved_platform=DEFAULT_PLATFORM,
            resolved_campaign_type=DEFAULT_CAMPAIGN_TYPE,
            resolved_template_family=template_family,
            entity_filter=request.entity_filter.to_audit_dict(),
        )

    def _known_template_families(self) -> set[str]:
        families: set[str] = {DEFAULT_TEMPLATE_FAMILY}
        for route in self.mcc_routes or []:
            family = getattr(route, "template_family", None)
            if family:
                families.add(str(family).strip().lower())
        return families

    def _validate_template_family(
        self, request: SlackParsedRequest
    ) -> tuple[str, str, SlackSubmitResult | None]:
        submitted = (getattr(request, "template_family", None) or "").strip().lower()
        if not submitted:
            return DEFAULT_TEMPLATE_FAMILY, "default", None
        if not self.routing_enabled:
            return submitted, "user_supplied", None
        known = self._known_template_families()
        if submitted in known:
            return submitted, "user_supplied", None
        self.logger.warning(
            "Unknown template family submitted via Slack",
            extra={
                "channel_id": request.channel_id,
                "thread_ts": request.thread_ts,
                "customer_id": request.customer_id,
                "entity_filter_audit": getattr(request, "entity_filter", None) and request.entity_filter.to_audit_dict() or {},
                "stage": "template_validation",
                "outcome": "unknown_template_family",
                "submitted_template_family": submitted,
                "known_template_families": sorted(known),
            },
        )
        return (
            DEFAULT_TEMPLATE_FAMILY,
            "user_supplied",
            SlackSubmitResult(
                outcome="template_rejected",
                message=format_unknown_template_family_message(
                    submitted_value=submitted,
                    valid_options=sorted(known),
                ),
            ),
        )

    def _apply_routing(
        self,
        payload: CloudTasksRequest,
        *,
        customer_id: str,
        request_id: str,
        started: float,
        context: dict[str, object],
    ) -> tuple[CloudTasksRequest, SlackSubmitResult | None]:
        if not self.routing_enabled:
            return payload, None

        resolution = resolve_mcc_route(
            routes=list(self.mcc_routes or []),
            customer_id=customer_id,
            platform=payload.resolved_platform,
            campaign_type=payload.resolved_campaign_type,
            template_family=payload.resolved_template_family,
        )
        resolved_payload = CloudTasksRequest(
            request_id=payload.request_id,
            channel_id=payload.channel_id,
            thread_ts=payload.thread_ts,
            sheet_url=payload.sheet_url,
            customer_id=payload.customer_id,
            campaign_id=payload.campaign_id,
            campaign_name=payload.campaign_name,
            requester_user_id=payload.requester_user_id,
            requester_text=payload.requester_text,
            route_id=resolution.route_id,
            resolved_login_customer_id=resolution.login_customer_id,
            resolved_platform=resolution.platform,
            resolved_campaign_type=resolution.campaign_type,
            resolved_template_family=resolution.template_family,
            resolved_tab_name=resolution.resolved_tab_name,
            entity_filter=payload.entity_filter or {},
        )
        if resolution.outcome == "matched":
            self.logger.info(
                "MCC route resolved",
                extra={
                    **context,
                    "request_id": request_id,
                    "routing_outcome": "matched",
                    "route_id": resolution.route_id,
                    "resolved_login_customer_id": resolution.login_customer_id,
                    "resolved_template_family": resolution.template_family,
                    "resolved_tab_name": resolution.resolved_tab_name,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return resolved_payload, None

        self.logger.warning(
            "MCC route resolution did not return a unique match",
            extra={
                **context,
                "request_id": request_id,
                "routing_enforcement": self.routing_enforcement,
                "routing_outcome": resolution.outcome,
                "matched_route_ids": list(resolution.matched_route_ids),
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        if self.routing_enforcement == "enforce":
            return resolved_payload, SlackSubmitResult(
                outcome="routing_rejected",
                message=format_execution_error_message(
                    request_id=request_id,
                    error_code=resolution.outcome,
                    status="rejected",
                ),
                request_id=request_id,
            )
        return resolved_payload, None

    def _find_recent_completed(self, *, customer_id: str, campaign_id: str):
        finder = getattr(
            self.run_store, "find_recent_completed_by_customer_campaign", None
        )
        if not callable(finder):
            return None
        return finder(customer_id=customer_id, campaign_id=campaign_id)

    def _find_recent_run_by_thread_ts(self, *, thread_ts: str):
        finder = getattr(self.run_store, "find_recent_by_thread_ts", None)
        if not callable(finder):
            return None
        return finder(thread_ts=thread_ts)

    @staticmethod
    def _build_retry_request_id(source_request_id: str) -> str:
        normalized = str(source_request_id or "").strip() or "req_retry"
        return f"{normalized}-retry-{int(time.time() * 1000)}"

    def _is_retry_expired(self, run: object) -> bool:
        if self.retry_window_minutes <= 0:
            return False
        run_timestamp = self._coerce_datetime(
            getattr(run, "updated_at", None)
        ) or self._coerce_datetime(getattr(run, "created_at", None))
        if run_timestamp is None:
            return False
        cutoff = datetime.now(timezone.utc).timestamp() - (
            self.retry_window_minutes * 60
        )
        return run_timestamp.timestamp() < cutoff

    @staticmethod
    def _coerce_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        return None

    def _create_pending_confirmation(self, payload: CloudTasksRequest) -> bool:
        creator = getattr(self.run_store, "create_pending_confirmation", None)
        if not callable(creator):
            return True
        created = creator(
            request_id=payload.request_id,
            channel_id=payload.channel_id,
            thread_ts=payload.thread_ts,
            sheet_url=payload.sheet_url,
            customer_id=payload.customer_id,
            campaign_id=payload.campaign_id,
            campaign_name=payload.campaign_name,
            requester_user_id=payload.requester_user_id,
            requester_text=payload.requester_text,
            route_id=payload.route_id,
            resolved_login_customer_id=payload.resolved_login_customer_id,
            resolved_platform=payload.resolved_platform,
            resolved_campaign_type=payload.resolved_campaign_type,
            resolved_template_family=payload.resolved_template_family,
            resolved_tab_name=payload.resolved_tab_name,
            entity_filter=payload.entity_filter or {},
        )
        return created is not None

    def _get_pending_confirmation(self, *, channel_id: str, thread_ts: str):
        getter = getattr(self.run_store, "get_pending_confirmation", None)
        if not callable(getter):
            return None
        return getter(channel_id=channel_id, thread_ts=thread_ts)

    def _resolve_pending_confirmation(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        resolution: str,
    ) -> None:
        resolver = getattr(self.run_store, "resolve_pending_confirmation", None)
        if not callable(resolver):
            return
        resolver(
            channel_id=channel_id,
            thread_ts=thread_ts,
            resolution=resolution,
        )

    def create_acceptance_notification(self, *, request_id: str) -> bool:
        creator = getattr(self.run_store, "create_listener_acceptance_notification", None)
        if not callable(creator):
            return True
        return bool(creator(request_id=request_id))

    def create_validation_notification(self, *, validation_key: str) -> bool:
        creator = getattr(self.run_store, "create_listener_validation_notification", None)
        if not callable(creator):
            return True
        try:
            return bool(creator(validation_key=validation_key))
        except Exception:
            self.logger.exception("Validation notification guard failed; allowing post")
            return True

    def create_thread_reply_event_notification(self, *, event_id: str) -> bool:
        creator = getattr(
            self.run_store,
            "create_listener_thread_reply_event_notification",
            None,
        )
        if not callable(creator):
            return True
        try:
            return bool(creator(event_id=event_id))
        except Exception:
            self.logger.exception(
                "Thread reply event notification guard failed; allowing post"
            )
            return True

    def create_thread_reply_notification(self, *, dedupe_key: str) -> bool:
        creator = getattr(
            self.run_store,
            "create_listener_thread_reply_notification",
            None,
        )
        if not callable(creator):
            return True
        try:
            return bool(creator(dedupe_key=dedupe_key))
        except Exception:
            self.logger.exception(
                "Thread reply notification guard failed; allowing post"
            )
            return True
