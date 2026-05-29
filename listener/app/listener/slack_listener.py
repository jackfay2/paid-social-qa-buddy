from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Protocol

from app.listener.slack_models import (
    SlackParsedRequest,
    SlackSubmitResult,
)
from app.listener.slack_messages import (
    format_accepted_message,
    format_validation_errors_message,
)
from app.listener.slack_parser import parse_and_validate_slack_request

_DEFAULT_BOT_MENTION = "@QA Bot"


class SlackListenerService(Protocol):
    def submit_request(self, request: SlackParsedRequest) -> SlackSubmitResult: ...

    def handle_thread_reply(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        text: str,
    ) -> SlackSubmitResult: ...


@dataclass
class SlackEventDeduper:
    ttl_seconds: int = 900
    max_entries: int = 10000

    def __post_init__(self) -> None:
        self._seen: dict[str, float] = {}

    def seen_recently(self, event_id: str) -> bool:
        normalized = str(event_id or "").strip()
        if not normalized:
            return False
        now = time.monotonic()
        self._purge(now)
        seen_at = self._seen.get(normalized)
        if seen_at is not None:
            return True
        self._seen[normalized] = now
        if len(self._seen) > self.max_entries:
            self._purge(now, aggressive=True)
        return False

    def _purge(self, now: float, aggressive: bool = False) -> None:
        ttl = max(int(self.ttl_seconds), 1)
        expired = [key for key, seen_at in self._seen.items() if (now - seen_at) > ttl]
        for key in expired:
            self._seen.pop(key, None)
        if aggressive and len(self._seen) > self.max_entries:
            ordered = sorted(self._seen.items(), key=lambda item: item[1])
            to_trim = len(self._seen) - self.max_entries
            for key, _ in ordered[: max(to_trim, 0)]:
                self._seen.pop(key, None)


@dataclass
class SlackMessageListener:
    service: SlackListenerService
    bot_mention: str = _DEFAULT_BOT_MENTION
    bot_user_id: str = ""
    event_deduper: SlackEventDeduper | None = None

    def __post_init__(self) -> None:
        if self.event_deduper is None:
            self.event_deduper = SlackEventDeduper()
        self.logger = logging.getLogger("qa_buddy.listener")

    def register(self, bolt_app: Any) -> None:
        @bolt_app.event("message")
        def _handle_message(
            event: dict[str, Any], body: dict[str, Any], say, logger=None
        ) -> None:
            self.handle_event(
                event=event,
                event_id=str((body or {}).get("event_id") or ""),
                say=say,
                logger=logger,
            )

    def handle_event(
        self,
        *,
        event: dict[str, Any],
        event_id: str = "",
        say,
        logger=None,
    ) -> bool:
        started = time.monotonic()
        normalized = _normalize_event_payload(event)
        channel_id = normalized["channel_id"]
        thread_ts = normalized["thread_ts"]
        user_id = normalized["user_id"]

        if self.event_deduper and self.event_deduper.seen_recently(event_id):
            self.logger.info(
                "Ignored duplicate Slack delivery",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "outcome": "duplicate_event_ignored",
                },
            )
            return False

        if not _should_consider_event(event):
            return False

        semantic_fingerprint = _build_event_fingerprint(normalized)
        if self.event_deduper and self.event_deduper.seen_recently(
            f"semantic:{semantic_fingerprint}"
        ):
            self.logger.info(
                "Ignored duplicate Slack semantic event",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "outcome": "duplicate_semantic_event_ignored",
                },
            )
            return False

        text = normalized["text"]
        if _is_thread_reply(normalized):
            thread_reply_event_guard = getattr(
                self.service,
                "create_thread_reply_event_notification",
                None,
            )
            if event_id and callable(thread_reply_event_guard):
                try:
                    should_process_event = thread_reply_event_guard(event_id=event_id)
                except Exception:
                    should_process_event = True
                    self.logger.exception(
                        "Thread reply event guard failed; allowing post",
                        extra={
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "outcome": "thread_reply_event_guard_failed_open",
                        },
                    )
                if not should_process_event:
                    self.logger.info(
                        "Ignored duplicate Slack thread reply by event id",
                        extra={
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "outcome": "duplicate_thread_reply_event_ignored",
                        },
                    )
                    return False

            thread_reply_key = _build_thread_reply_fingerprint(normalized)
            thread_reply_guard = getattr(
                self.service,
                "create_thread_reply_notification",
                None,
            )
            if callable(thread_reply_guard):
                try:
                    should_process_semantic = thread_reply_guard(
                        dedupe_key=thread_reply_key
                    )
                except Exception:
                    should_process_semantic = True
                    self.logger.exception(
                        "Thread reply semantic guard failed; allowing post",
                        extra={
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "outcome": "thread_reply_semantic_guard_failed_open",
                        },
                    )
                if not should_process_semantic:
                    self.logger.info(
                        "Ignored duplicate Slack thread reply by semantic key",
                        extra={
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "outcome": "duplicate_thread_reply_semantic_ignored",
                        },
                    )
                    return False

            service_result = self.service.handle_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                text=text,
            )
            if service_result.outcome != "ignored":
                say(
                    text=service_result.message,
                    thread_ts=thread_ts,
                )
                self.logger.info(
                    "Processed thread reply",
                    extra={
                        "event_id": event_id,
                        "channel_id": channel_id,
                        "thread_ts": thread_ts,
                        "outcome": service_result.outcome,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return True
            return False

        if not self._has_required_mention(text):
            return False

        thread_ts = normalized["ts"]
        channel_id = normalized["channel_id"]
        user_id = normalized["user_id"]

        validation = parse_and_validate_slack_request(
            text=text,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
        )

        if not validation.accepted:
            validation_key = _build_validation_fingerprint(normalized)
            validation_guard = getattr(
                self.service, "create_validation_notification", None
            )
            if callable(validation_guard):
                try:
                    should_post = validation_guard(validation_key=validation_key)
                except Exception:
                    should_post = True
                    self.logger.exception(
                        "Validation notification guard failed; allowing post",
                        extra={
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "outcome": "validation_guard_failed_open",
                        },
                    )
                if not should_post:
                    self.logger.info(
                        "Skipped duplicate Slack validation post",
                        extra={
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "outcome": "validation_post_suppressed",
                            "duration_ms": int((time.monotonic() - started) * 1000),
                        },
                    )
                    return False
            say(
                text=format_validation_errors_message(validation.errors),
                thread_ts=thread_ts,
            )
            self.logger.info(
                "Rejected Slack request on validation",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "outcome": "validation_failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return True

        try:
            submit_result = self.service.submit_request(validation.request)
        except Exception:
            target_logger = logger or logging.getLogger("qa_buddy.listener")
            target_logger.exception("Failed to enqueue Slack QA request")
            say(
                text=(
                    "Request was validated, but queueing failed. Please retry by "
                    "submitting the request again in this thread."
                ),
                thread_ts=thread_ts,
            )
            self.logger.error(
                "Slack request enqueue failed",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": validation.request.customer_id,
                    "campaign_id": validation.request.campaign_id,
                    "request_id": "",
                    "outcome": "enqueue_failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return True

        if submit_result.outcome == "duplicate_confirmation_requested":
            say(text=submit_result.message, thread_ts=thread_ts)
            self.logger.info(
                "Slack request requires duplicate confirmation",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": validation.request.customer_id,
                    "campaign_id": validation.request.campaign_id,
                    "request_id": "",
                    "outcome": submit_result.outcome,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return True

        if submit_result.outcome == "ignored":
            self.logger.info(
                "Slack request ignored after submit",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": validation.request.customer_id,
                    "campaign_id": validation.request.campaign_id,
                    "request_id": "",
                    "outcome": submit_result.outcome,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return False

        if submit_result.outcome == "already_enqueued":
            self.logger.info(
                "Slack request already enqueued",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": validation.request.customer_id,
                    "campaign_id": validation.request.campaign_id,
                    "request_id": "",
                    "outcome": submit_result.outcome,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return False

        if submit_result.outcome in ("routing_rejected", "template_rejected"):
            say(
                text=submit_result.message,
                thread_ts=thread_ts,
            )
            self.logger.info(
                "Slack request rejected by routing",
                extra={
                    "event_id": event_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "customer_id": validation.request.customer_id,
                    "campaign_id": validation.request.campaign_id,
                    "request_id": str(submit_result.request_id or "").strip(),
                    "outcome": submit_result.outcome,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return True

        submit_request_id = str(submit_result.request_id or "").strip()
        if submit_request_id:
            acceptance_guard = getattr(
                self.service, "create_acceptance_notification", None
            )
            if callable(acceptance_guard):
                should_post = acceptance_guard(request_id=submit_request_id)
                if not should_post:
                    self.logger.info(
                        "Skipped duplicate Slack acceptance post",
                        extra={
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "customer_id": validation.request.customer_id,
                            "campaign_id": validation.request.campaign_id,
                            "request_id": submit_request_id,
                            "outcome": "accepted_post_suppressed",
                            "duration_ms": int((time.monotonic() - started) * 1000),
                        },
                    )
                    return False

        say(text=format_accepted_message(), thread_ts=thread_ts)
        self.logger.info(
            "Slack request accepted and enqueued",
            extra={
                "event_id": event_id,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "customer_id": validation.request.customer_id,
                "campaign_id": validation.request.campaign_id,
                "request_id": submit_request_id,
                "outcome": submit_result.outcome,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return True

    def _has_required_mention(self, text: str) -> bool:
        lowered = (text or "").lower()
        if self.bot_mention and self.bot_mention.lower() in lowered:
            return True
        if self.bot_user_id and f"<@{self.bot_user_id}>" in (text or ""):
            return True
        return False


def _should_consider_event(event: dict[str, Any]) -> bool:
    normalized = _normalize_event_payload(event)
    if not normalized:
        return False
    if normalized["subtype"] not in {"", "thread_broadcast", "reply_broadcast", "message_replied"}:
        return False
    if normalized["bot_id"]:
        return False
    if not normalized["text"]:
        return False
    if _contains_broadcast_mention(normalized["text"]):
        return False
    if not normalized["channel_id"]:
        return False
    if not normalized["ts"]:
        return False
    return True


def _is_thread_reply(event_fields: dict[str, str]) -> bool:
    thread_ts = str(event_fields.get("thread_ts") or "").strip()
    ts = str(event_fields.get("ts") or "").strip()
    return bool(thread_ts and thread_ts != ts)


def _normalize_event_payload(event: dict[str, Any]) -> dict[str, str]:
    if not isinstance(event, dict):
        return {}
    nested = event.get("message") if isinstance(event.get("message"), dict) else {}
    text = str(event.get("text") or nested.get("text") or "").strip()
    channel_id = str(event.get("channel") or "").strip()
    ts = str(event.get("ts") or nested.get("ts") or "").strip()
    thread_ts = str(event.get("thread_ts") or nested.get("thread_ts") or ts).strip()
    user_id = str(event.get("user") or nested.get("user") or "").strip()
    subtype = str(event.get("subtype") or nested.get("subtype") or "").strip()
    bot_id = str(event.get("bot_id") or nested.get("bot_id") or "").strip()
    return {
        "text": text,
        "channel_id": channel_id,
        "ts": ts,
        "thread_ts": thread_ts,
        "user_id": user_id,
        "subtype": subtype,
        "bot_id": bot_id,
    }


def _build_event_fingerprint(event_fields: dict[str, str]) -> str:
    text = " ".join(str(event_fields.get("text") or "").split()).lower()
    channel_id = str(event_fields.get("channel_id") or "").strip()
    user_id = str(event_fields.get("user_id") or "").strip()
    thread_ts = str(event_fields.get("thread_ts") or "").strip()
    return "|".join([channel_id, user_id, thread_ts, text])


def _build_validation_fingerprint(event_fields: dict[str, str]) -> str:
    return f"validation:{_build_event_fingerprint(event_fields)}"


def _build_thread_reply_fingerprint(event_fields: dict[str, str]) -> str:
    return f"thread_reply:{_build_event_fingerprint(event_fields)}"


def _contains_broadcast_mention(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        mention in lowered
        for mention in (
            "@here",
            "<!here>",
            "@channel",
            "<!channel>",
            "@everyone",
            "<!everyone>",
        )
    )
