"""Unit tests for SocialQAOrchestrationService.

Uses the real InMemoryRunStore plus mocked resolver / meta_client / sheet_client.
Covers the happy path and each failure branch — every branch must produce a
terminal result with a clear error_code (handoff §5.4: never silent).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.storage.run_store import InMemoryRunStore
from app.core.contracts import RunRecord, SheetAccessResult
from app.core.orchestration import (
    OrchestrationRequest,
    SocialQAOrchestrationService,
)
from app.models import CheckResult, CheckRow


def _request(**overrides) -> OrchestrationRequest:
    base = dict(
        request_id="req-1",
        account_id="123456789",
        campaign_id="987654321",
        campaign_name="Test Campaign",
        sheet_url="https://docs.google.com/spreadsheets/d/abc/edit",
        thread_ts="1.2",
        channel_id="C123",
    )
    base.update(overrides)
    return OrchestrationRequest(**base)


def _pass_runner(row: CheckRow, *, evidence=None) -> CheckResult:
    return CheckResult(row.row_index, row.check_id, "Pass", "")


def _make_service(
    *,
    run_store=None,
    resolver=None,
    meta_client=None,
    sheet_client=None,
    check_runner=None,
    rows=None,
):
    run_store = run_store if run_store is not None else InMemoryRunStore()

    if resolver is None:
        resolver = MagicMock()
        resolver.resolve_client_id.return_value = "C00030334"

    if meta_client is None:
        meta_client = MagicMock()
        meta_client.get_campaign.return_value = {"objective": "OUTCOME_TRAFFIC"}
        meta_client.get_ad_sets.return_value = []
        meta_client.get_ads.return_value = []

    if sheet_client is None:
        sheet_client = MagicMock()
        sheet_client.check_access.return_value = SheetAccessResult(ok=True)
        sheet_client.read_check_rows.return_value = (
            rows
            if rows is not None
            else [CheckRow(row_index=2, check_id="bid_strategy", builder_input="Lowest cost")]
        )

    service = SocialQAOrchestrationService(
        run_store=run_store,
        resolver=resolver,
        meta_client=meta_client,
        sheet_client=sheet_client,
        check_runner=check_runner or _pass_runner,
    )
    return service, run_store, resolver, meta_client, sheet_client


# --- happy path ------------------------------------------------------------


def test_happy_path_completes() -> None:
    service, *_ = _make_service()
    result = service.run(_request())

    assert result.status == "completed"
    assert result.summary_counts["pass"] == 1
    assert result.resolved_client_id == "C00030334"
    assert "QA complete" in result.message


def test_summary_message_includes_fix_specifics_and_sheet_link() -> None:
    """Brief: the Slack summary surfaces fix items with specifics + a sheet link."""

    def fixing_runner(row, *, evidence=None):
        return CheckResult(row.row_index, row.check_id, "Fix", "Expected X, got Y")

    rows = [CheckRow(row_index=5, check_id="campaign_objective", builder_input="Sales")]
    service, *_ = _make_service(check_runner=fixing_runner, rows=rows)
    result = service.run(_request(sheet_url="https://docs.google.com/spreadsheets/d/abc/edit"))

    assert "QA complete" in result.message
    assert "Fixes" in result.message
    assert "campaign_objective: Expected X, got Y" in result.message
    assert "Sheet: https://docs.google.com/spreadsheets/d/abc/edit" in result.message


def test_summary_message_no_fixes_section_when_all_pass() -> None:
    service, *_ = _make_service()  # default runner passes
    result = service.run(_request())
    assert "QA complete" in result.message
    assert "Fixes" not in result.message


def test_happy_path_writes_results_once() -> None:
    service, _store, _resolver, _meta, sheet = _make_service()
    service.run(_request())
    sheet.write_results.assert_called_once()


def test_happy_path_persists_completed_record() -> None:
    service, store, *_ = _make_service()
    result = service.run(_request())
    record = store.get_run(result.run_id)
    assert record is not None
    assert record.status == "completed"


def test_evidence_passed_to_checks() -> None:
    captured = {}

    def capturing_runner(row, *, evidence=None):
        captured["evidence"] = evidence
        return CheckResult(row.row_index, row.check_id, "Pass", "")

    service, *_ = _make_service(check_runner=capturing_runner)
    service.run(_request())

    assert captured["evidence"]["client_id"] == "C00030334"
    assert captured["evidence"]["campaign"] == {"objective": "OUTCOME_TRAFFIC"}
    assert "ad_sets" in captured["evidence"]
    assert "ads" in captured["evidence"]


# --- text checks (Gemini wiring) -------------------------------------------


def test_gemini_text_check_result_in_run_output(monkeypatch) -> None:
    """When gemini_client is wired and a text-check row is present, the result
    flows through orchestration → sheet write → summary counts."""
    from app.checks import text_checks as text_checks_module
    from app.core.orchestration import SocialQAOrchestrationService

    monkeypatch.setattr(
        text_checks_module,
        "TEXT_CHECK_DEFINITIONS",
        {
            "creative_spelling": text_checks_module.TextCheckDefinition(
                check_id="creative_spelling",
                instruction="Spellcheck?",
                ad_field="creative.body",
            )
        },
    )

    rows = [CheckRow(row_index=3, check_id="creative_spelling", builder_input="no typos")]
    gemini_calls = []

    class _Gemini:
        def run_text_checks(self, batch):
            gemini_calls.append(batch)
            return {
                "check_results": {
                    batch[0]["check_id"]: {
                        "verdict": "Fix",
                        "action": "Typo found",
                        "confidence": 0.95,
                    }
                }
            }

    service, _store, _resolver, meta, sheet = _make_service(rows=rows)
    meta.get_ads.return_value = [{"id": "ad1", "name": "Ad A", "creative": {"body": "Recieve"}}]
    # Re-create the service with gemini wired — _make_service doesn't accept it.
    wired = SocialQAOrchestrationService(
        run_store=service.run_store,
        resolver=service.resolver,
        meta_client=service.meta_client,
        sheet_client=service.sheet_client,
        check_runner=service.check_runner,
        gemini_client=_Gemini(),
    )
    result = wired.run(_request())

    assert result.status == "completed"
    assert result.summary_counts["fix"] == 1
    assert any("Ad A" in item for item in result.fix_items)
    assert len(gemini_calls) == 1


def test_no_gemini_client_means_text_checks_skipped() -> None:
    """With gemini_client=None (existing default), text-check rows return no
    results — they're skipped at execute_checks and execute_text_checks both."""
    from app.checks import text_checks as text_checks_module

    # Use monkeypatch via the standard pattern but keep it scoped.
    original = dict(text_checks_module.TEXT_CHECK_DEFINITIONS)
    text_checks_module.TEXT_CHECK_DEFINITIONS["creative_spelling"] = (
        text_checks_module.TextCheckDefinition(
            check_id="creative_spelling",
            instruction="Spellcheck?",
            ad_field="creative.body",
        )
    )
    try:
        rows = [CheckRow(row_index=3, check_id="creative_spelling", builder_input="no typos")]
        service, *_ = _make_service(rows=rows)  # gemini_client default = None
        result = service.run(_request())
        # No results at all → summary is all zeros.
        assert result.summary_counts == {"pass": 0, "fix": 0, "review": 0, "na": 0, "error": 0}
        assert result.status == "completed"
    finally:
        text_checks_module.TEXT_CHECK_DEFINITIONS.clear()
        text_checks_module.TEXT_CHECK_DEFINITIONS.update(original)


# --- dedup -----------------------------------------------------------------


def test_dedup_terminal_run_replays_cached_result() -> None:
    store = InMemoryRunStore()
    store.create_run(
        RunRecord(
            run_id="old-run",
            request_id="req-1",
            status="completed",
            message="QA complete for X | Pass 5 | Fix 0 | Review 0 | N/A 0 | Error 0",
            pass_count=5,
        )
    )
    service, *_ = _make_service(run_store=store)

    result = service.run(_request(request_id="req-1"))

    assert result.status == "completed"
    assert result.run_id == "old-run"
    assert result.duplicate_of_run_id == "old-run"
    assert result.summary_counts["pass"] == 5


def test_dedup_in_progress_run_rejected() -> None:
    store = InMemoryRunStore()
    store.create_run(
        RunRecord(run_id="old-run", request_id="req-1", status="running")
    )
    service, *_ = _make_service(run_store=store)

    result = service.run(_request(request_id="req-1"))

    assert result.status == "rejected"
    assert result.error_code == "duplicate_request_in_progress"


# --- validation ------------------------------------------------------------


def test_missing_account_id_rejected() -> None:
    service, *_ = _make_service()
    result = service.run(_request(account_id=""))
    assert result.status == "rejected"
    assert result.error_code == "invalid_request"


def test_missing_sheet_url_rejected() -> None:
    service, *_ = _make_service()
    result = service.run(_request(sheet_url=""))
    assert result.status == "rejected"
    assert result.error_code == "invalid_request"


# --- resolver --------------------------------------------------------------


def test_account_not_found_rejected() -> None:
    resolver = MagicMock()
    resolver.resolve_client_id.return_value = None
    service, *_ = _make_service(resolver=resolver)

    result = service.run(_request())

    assert result.status == "rejected"
    assert result.error_code == "account_not_found"


def test_resolver_error_fails() -> None:
    resolver = MagicMock()
    resolver.resolve_client_id.side_effect = RuntimeError("boom")
    service, *_ = _make_service(resolver=resolver)

    result = service.run(_request())

    assert result.status == "failed"
    assert result.error_code == "account_resolution_failed"


# --- sheet access ----------------------------------------------------------


def test_sheet_access_denied_rejected_with_message() -> None:
    sheet = MagicMock()
    sheet.check_access.return_value = SheetAccessResult(
        ok=False,
        reason="The QA sheet isn't shared with the bot's service account.",
        error_code="sheet_permission_denied",
    )
    service, *_ = _make_service(sheet_client=sheet)

    result = service.run(_request())

    assert result.status == "rejected"
    assert result.error_code == "sheet_permission_denied"
    assert "service account" in result.message.lower()


# --- downstream failures ---------------------------------------------------


def test_bigquery_fetch_failure_fails() -> None:
    meta = MagicMock()
    meta.get_campaign.side_effect = RuntimeError("bq down")
    service, *_ = _make_service(meta_client=meta)

    result = service.run(_request())

    assert result.status == "failed"
    assert result.error_code == "bigquery_fetch_failed"


def test_sheet_read_failure_fails() -> None:
    sheet = MagicMock()
    sheet.check_access.return_value = SheetAccessResult(ok=True)
    sheet.read_check_rows.side_effect = RuntimeError("read boom")
    service, *_ = _make_service(sheet_client=sheet)

    result = service.run(_request())

    assert result.status == "failed"
    assert result.error_code == "sheet_read_failed"


def test_sheet_write_failure_fails() -> None:
    sheet = MagicMock()
    sheet.check_access.return_value = SheetAccessResult(ok=True)
    sheet.read_check_rows.return_value = [
        CheckRow(row_index=2, check_id="bid_strategy", builder_input="Lowest cost")
    ]
    sheet.write_results.side_effect = RuntimeError("write boom")
    service, *_ = _make_service(sheet_client=sheet)

    result = service.run(_request())

    assert result.status == "failed"
    assert result.error_code == "sheet_write_failed"


def test_unexpected_check_error_fails_as_internal() -> None:
    def boom_runner(row, *, evidence=None):
        raise ValueError("unexpected check explosion")

    service, *_ = _make_service(check_runner=boom_runner)

    result = service.run(_request())

    assert result.status == "failed"
    assert result.error_code == "internal_error"


# --- fix items -------------------------------------------------------------


def test_fix_items_collected_from_fix_verdicts() -> None:
    def fix_runner(row, *, evidence=None):
        return CheckResult(row.row_index, row.check_id, "Fix", "Expected X, got Y")

    rows = [
        CheckRow(row_index=2, check_id="bid_strategy", builder_input="Lowest cost"),
        CheckRow(row_index=3, check_id="objective", builder_input="Traffic"),
    ]
    service, *_ = _make_service(rows=rows, check_runner=fix_runner)

    result = service.run(_request())

    assert result.status == "completed"
    assert result.summary_counts["fix"] == 2
    assert len(result.fix_items) == 2
    assert result.fix_items[0].startswith("bid_strategy:")
