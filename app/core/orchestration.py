"""Social QA orchestration: the flow that turns a task request into sheet verdicts.

Walks the pipeline:
  dedup -> create run -> validate -> resolve client_id -> check sheet access ->
  fetch BigQuery evidence -> read check rows -> run checks -> write results ->
  summarize -> mark complete.

Every failure path returns a terminal OrchestrationResult with a clear message
and error_code, and updates the run record. The worker endpoint posts that
message to Slack — so a run never ends silently (handoff §5.4).

Slack posting and Cloud-Task notification dedup live at the endpoint layer, not
here, which keeps this class free of Slack mocking and matches the Search split.

Adapters are injected (12-factor IV): the service depends on the Protocols in
app.core.contracts, not concrete implementations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.core.contracts import (
    AccountResolver,
    GeminiClient,
    MetaDataClient,
    RunRecord,
    RunStore,
    SheetClient,
)
from app.core.pipeline import build_summary, execute_checks, execute_text_checks
from app.models import CheckResult

_logger = logging.getLogger("paid_social_qa_buddy.orchestration")

_TERMINAL_STATES = {"completed", "failed", "rejected"}

CheckRunner = Callable[..., CheckResult]


@dataclass
class OrchestrationRequest:
    """The fields the worker needs from the Cloud Tasks envelope."""
    request_id: str
    account_id: str
    campaign_id: str
    campaign_name: str = ""
    sheet_url: str = ""
    thread_ts: str = ""
    channel_id: str = ""


@dataclass
class OrchestrationResult:
    """Terminal outcome of a run. `message` is Slack-ready."""
    status: str  # completed | failed | rejected
    message: str
    run_id: str = ""
    error_code: str = ""
    summary_counts: dict[str, int] = field(default_factory=dict)
    fix_items: list[str] = field(default_factory=list)
    sheet_url: str = ""
    resolved_client_id: str = ""
    duplicate_of_run_id: str = ""


def _empty_counts() -> dict[str, int]:
    return {"pass": 0, "fix": 0, "review": 0, "na": 0, "error": 0}


class SocialQAOrchestrationService:
    def __init__(
        self,
        *,
        run_store: RunStore,
        resolver: AccountResolver,
        meta_client: MetaDataClient,
        sheet_client: SheetClient,
        check_runner: CheckRunner,
        gemini_client: GeminiClient | None = None,
        qa_initial: str = "QA-BOT",
        fix_items_limit: int = 5,
        peacock_client_ids: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.run_store = run_store
        self.resolver = resolver
        self.meta_client = meta_client
        self.sheet_client = sheet_client
        self.check_runner = check_runner
        # Client_ids whose data is Peacock's (separate GCP project + own
        # vocabulary). When the resolved client_id is in here, evidence carries
        # peacock_mode=True so the value-match checks compare in Peacock's own
        # terms (Acquisition / Biddable / …) instead of mapping to Meta enums.
        self.peacock_client_ids = frozenset(peacock_client_ids or ())
        # Optional: when None, text checks are skipped silently. Lets the local
        # dev path run without a Gemini key (existing tests don't pass one).
        self.gemini_client = gemini_client
        self.qa_initial = qa_initial
        self.fix_items_limit = fix_items_limit

    def run(self, request: OrchestrationRequest) -> OrchestrationResult:
        request_id = (request.request_id or "").strip()

        # Idempotency: a completed/failed/rejected run replays its stored result;
        # an in-progress run is rejected so we don't double-process.
        existing = (
            self.run_store.find_by_request_id(request_id) if request_id else None
        )
        if existing is not None:
            if existing.status in _TERMINAL_STATES:
                result = self._result_from_record(existing)
                result.duplicate_of_run_id = existing.run_id
                return result
            return OrchestrationResult(
                status="rejected",
                message="A QA run for this request is already in progress.",
                error_code="duplicate_request_in_progress",
                run_id=existing.run_id,
                sheet_url=request.sheet_url,
                summary_counts=_empty_counts(),
                duplicate_of_run_id=existing.run_id,
            )

        record = RunRecord(
            run_id=uuid4().hex,
            request_id=request_id,
            status="accepted",
            message="Run accepted.",
            sheet_url=request.sheet_url,
            account_id=request.account_id,
            campaign_id=request.campaign_id,
            campaign_name=request.campaign_name,
            thread_ts=request.thread_ts,
            channel_id=request.channel_id,
        )
        self.run_store.create_run(record)

        try:
            return self._execute(record, request)
        except Exception:
            _logger.exception(
                "orchestration_internal_error", extra={"run_id": record.run_id}
            )
            return self._fail(
                record, "QA failed due to an internal error.", "internal_error"
            )

    def _execute(
        self, record: RunRecord, request: OrchestrationRequest
    ) -> OrchestrationResult:
        # 1. Validate required fields.
        missing = [
            name
            for name, value in (
                ("account_id", request.account_id),
                ("campaign_id", request.campaign_id),
                ("sheet_url", request.sheet_url),
            )
            if not (value or "").strip()
        ]
        if missing:
            return self._reject(
                record,
                f"Missing required field(s): {', '.join(missing)}.",
                "invalid_request",
            )

        # 2. Resolve account_id -> client_id (the BQ dataset selector).
        record.status = "validating"
        self.run_store.update_run(record)
        try:
            client_id = self.resolver.resolve_client_id(request.account_id)
        except Exception as exc:  # noqa: BLE001 — convert to a clean terminal result
            return self._fail(
                record,
                f"Couldn't resolve the account to a client: {exc}",
                "account_resolution_failed",
            )
        if not client_id:
            return self._reject(
                record,
                (
                    f"Account {request.account_id} couldn't be mapped to a client "
                    "in BigQuery (no performance data found). Double-check the account ID."
                ),
                "account_not_found",
            )

        # 3. Sheet access — the #1 user error (sheet not shared with the SA).
        access = self.sheet_client.check_access(request.sheet_url)
        if not access.ok:
            return self._reject(
                record, access.reason, access.error_code or "sheet_inaccessible"
            )

        # 4. Fetch Meta evidence from BigQuery.
        record.status = "running"
        self.run_store.update_run(record)
        try:
            evidence = self._fetch_evidence(client_id, request.campaign_id)
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                record,
                f"Failed to fetch Meta data from BigQuery: {exc}",
                "bigquery_fetch_failed",
            )

        # 5. Read check rows from the sheet.
        try:
            rows = self.sheet_client.read_check_rows(request.sheet_url)
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                record, f"Failed to read the QA sheet: {exc}", "sheet_read_failed"
            )

        # 6. Run deterministic checks.
        results = execute_checks(rows, self.check_runner, evidence=evidence)

        # 6b. Text checks via Gemini — one batched call across the whole job
        # (cost + latency). No-op when no gemini_client is wired, so the local
        # path without an API key still passes through cleanly. Failures inside
        # the adapter degrade to Review per the Peacock-Olympics rule; only
        # truly unexpected exceptions bubble here and become "internal_error".
        if self.gemini_client is not None:
            text_results = execute_text_checks(
                rows, evidence.get("ads", []), self.gemini_client
            )
            results.extend(text_results)

        # 7. Write verdicts back to the sheet (batched).
        try:
            self.sheet_client.write_results(
                request.sheet_url, results, self.qa_initial
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                record,
                f"Failed to write results to the QA sheet: {exc}",
                "sheet_write_failed",
            )

        # 8. Summarize and mark complete.
        summary = build_summary(results)
        summary_counts = {
            "pass": summary.pass_count,
            "fix": summary.fix_count,
            "review": summary.review_count,
            "na": summary.na_count,
            "error": summary.error_count,
        }
        fix_items = self._build_fix_items(results)
        message = self._build_summary_message(summary_counts, request, fix_items)

        record.status = "completed"
        record.message = message
        record.pass_count = summary.pass_count
        record.fix_count = summary.fix_count
        record.review_count = summary.review_count
        record.na_count = summary.na_count
        record.error_count = summary.error_count
        record.fix_items = fix_items
        self.run_store.update_run(record)

        _logger.info(
            "orchestration_completed",
            extra={
                "run_id": record.run_id,
                "request_id": record.request_id,
                "resolved_client_id": client_id,
                **summary_counts,
            },
        )

        return OrchestrationResult(
            status="completed",
            message=message,
            run_id=record.run_id,
            summary_counts=summary_counts,
            fix_items=fix_items,
            sheet_url=request.sheet_url,
            resolved_client_id=client_id,
        )

    def _fetch_evidence(self, client_id: str, campaign_id: str) -> dict[str, Any]:
        """Bundle the campaign/adset/ad data into the dict check functions read."""
        return {
            "client_id": client_id,
            "campaign_id": campaign_id,
            "peacock_mode": client_id in self.peacock_client_ids,
            "campaign": self.meta_client.get_campaign(client_id, campaign_id),
            "ad_sets": self.meta_client.get_ad_sets(client_id, campaign_id),
            "ads": self.meta_client.get_ads(client_id, campaign_id),
        }

    def _reject(
        self, record: RunRecord, message: str, error_code: str
    ) -> OrchestrationResult:
        record.status = "rejected"
        record.message = message
        record.error_codes = [error_code]
        self.run_store.update_run(record)
        _logger.warning(
            "orchestration_rejected",
            extra={"run_id": record.run_id, "error_code": error_code},
        )
        return OrchestrationResult(
            status="rejected",
            message=message,
            run_id=record.run_id,
            error_code=error_code,
            sheet_url=record.sheet_url,
            summary_counts=_empty_counts(),
        )

    def _fail(
        self, record: RunRecord, message: str, error_code: str
    ) -> OrchestrationResult:
        record.status = "failed"
        record.message = message
        record.error_codes = [error_code]
        self.run_store.update_run(record)
        _logger.error(
            "orchestration_failed",
            extra={"run_id": record.run_id, "error_code": error_code},
        )
        return OrchestrationResult(
            status="failed",
            message=message,
            run_id=record.run_id,
            error_code=error_code,
            sheet_url=record.sheet_url,
            summary_counts=_empty_counts(),
        )

    def _result_from_record(self, record: RunRecord) -> OrchestrationResult:
        return OrchestrationResult(
            status=record.status,
            message=record.message,
            run_id=record.run_id,
            error_code=record.error_codes[0] if record.error_codes else "",
            summary_counts={
                "pass": record.pass_count,
                "fix": record.fix_count,
                "review": record.review_count,
                "na": record.na_count,
                "error": record.error_count,
            },
            fix_items=record.fix_items,
            sheet_url=record.sheet_url,
        )

    def _build_fix_items(self, results: list[CheckResult]) -> list[str]:
        items: list[str] = []
        for result in results:
            if result.verdict != "Fix":
                continue
            action = (result.action or "Needs fixes").strip()
            if len(action) > 120:
                action = f"{action[:117]}..."
            items.append(f"{result.check_id}: {action}")
            if len(items) >= self.fix_items_limit:
                break
        return items

    def _build_summary_message(
        self,
        counts: dict[str, int],
        request: "OrchestrationRequest",
        fix_items: list[str] | None = None,
    ) -> str:
        """Slack-ready summary. Mirrors the Search bot's format so both platforms
        read the same in-thread: a header with the campaign + ids, a Summary
        counts line, surfaced Fix specifics (our addition, per the brief), the
        sheet link, and the request_id for traceability.
        """
        label = (request.campaign_name or "Campaign").strip() or "Campaign"
        header = f"QA completed for {label}"
        ids = []
        if request.account_id:
            ids.append(f"account_id={request.account_id}")
        if request.campaign_id:
            ids.append(f"campaign_id={request.campaign_id}")
        if ids:
            header += f" ({', '.join(ids)})"

        lines = [
            header,
            f"Summary: Pass {counts['pass']} | Fix {counts['fix']} | "
            f"Review {counts['review']} | N/A {counts['na']} | Error {counts['error']}",
        ]

        fix_items = fix_items or []
        if fix_items:
            shown = len(fix_items)
            total = counts.get("fix", shown)
            lines.append("")
            lines.append(f"Fixes ({shown} of {total}):" if total > shown else "Fixes:")
            lines.extend(f"• {item}" for item in fix_items)

        if request.sheet_url:
            lines.append("")
            lines.append(f"Sheet: {request.sheet_url}")
        if request.request_id:
            lines.append(f"request_id: {request.request_id}")

        return "\n".join(lines)
