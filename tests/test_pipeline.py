"""Unit tests for the check-execution pipeline."""

from __future__ import annotations

import app.core.pipeline as pipeline
from app.core.pipeline import build_summary, execute_checks, is_builder_na
from app.models import CheckResult, CheckRow


def _row(check_id: str, builder_input: str = "x", row_index: int = 2) -> CheckRow:
    return CheckRow(row_index=row_index, check_id=check_id, builder_input=builder_input)


def _passing_runner(row: CheckRow, *, evidence=None) -> CheckResult:
    return CheckResult(row_index=row.row_index, check_id=row.check_id, verdict="Pass", action="")


# --- is_builder_na ---------------------------------------------------------


def test_is_builder_na_variants() -> None:
    assert is_builder_na("") is True
    assert is_builder_na("   ") is True
    assert is_builder_na("N/A") is True
    assert is_builder_na("na") is True
    assert is_builder_na("None") is True
    assert is_builder_na("Lowest cost") is False


# --- build_summary ---------------------------------------------------------


def test_build_summary_counts_all_verdicts() -> None:
    results = [
        CheckResult(1, "a", "Pass"),
        CheckResult(2, "b", "Fix"),
        CheckResult(3, "c", "Review"),
        CheckResult(4, "d", "N/A"),
        CheckResult(5, "e", "Error"),
        CheckResult(6, "f", "Pass"),
    ]
    summary = build_summary(results)
    assert summary.pass_count == 2
    assert summary.fix_count == 1
    assert summary.review_count == 1
    assert summary.na_count == 1
    assert summary.error_count == 1


def test_build_summary_unknown_verdict_counts_as_error() -> None:
    summary = build_summary([CheckResult(1, "a", "Garbage")])
    assert summary.error_count == 1


# --- execute_checks --------------------------------------------------------


def test_execute_checks_skips_rows_without_check_id() -> None:
    rows = [
        CheckRow(row_index=2, check_id="", builder_input="x"),
        _row("bid_strategy"),
    ]
    results = execute_checks(rows, _passing_runner)
    assert len(results) == 1
    assert results[0].check_id == "bid_strategy"


def test_execute_checks_na_for_blank_builder_input() -> None:
    rows = [_row("bid_strategy", builder_input="")]
    results = execute_checks(rows, _passing_runner)
    assert results[0].verdict == "N/A"


def test_execute_checks_runs_runner_for_real_input() -> None:
    rows = [_row("bid_strategy", builder_input="Lowest cost")]
    results = execute_checks(rows, _passing_runner)
    assert results[0].verdict == "Pass"


def test_execute_checks_attaches_builder_fields() -> None:
    row = CheckRow(
        row_index=2,
        check_id="bid_strategy",
        builder_input="Lowest cost",
        builder_notes="a note",
    )
    results = execute_checks([row], _passing_runner)
    assert results[0].builder_input == "Lowest cost"
    assert results[0].builder_notes == "a note"


def test_execute_checks_manual_review_check(monkeypatch) -> None:
    monkeypatch.setitem(
        pipeline.ALWAYS_REVIEW_CHECK_ACTIONS,
        "creative_dims",
        "Verify creative manually.",
    )
    rows = [_row("creative_dims", builder_input="anything")]
    results = execute_checks(rows, _passing_runner)
    assert results[0].verdict == "Review"
    assert results[0].action == "Verify creative manually."


def test_download_changes_is_manual_review_even_when_blank() -> None:
    """The brief's mandated manual check returns Review with instructions even
    if the builder leaves the input blank (manual gate beats the N/A gate)."""
    rows = [_row("download_changes", builder_input="")]
    results = execute_checks(rows, _passing_runner)
    assert results[0].verdict == "Review"
    assert "download" in results[0].action.lower()


def test_ad_creative_dimensions_is_manual_review() -> None:
    rows = [_row("ad_creative_dimensions", builder_input="")]
    results = execute_checks(rows, _passing_runner)
    assert results[0].verdict == "Review"
    assert "1x1" in results[0].action and "9x16" in results[0].action


def test_ad_creative_dimensions_peacock_routes_to_registry() -> None:
    """Phase B: in Peacock mode the trafficking table carries Frame_Size, so the
    check runs deterministically (routes to the runner) instead of the manual
    note. Non-Peacock keeps the manual Review."""
    rows = [_row("ad_creative_dimensions", builder_input="9:16")]
    standard = execute_checks(rows, _passing_runner)
    assert standard[0].verdict == "Review"
    assert "manual" in standard[0].action.lower()
    # Peacock run: routed to the (stub) runner, which returns Pass here.
    peacock = execute_checks(rows, _passing_runner, evidence={"peacock_mode": True})
    assert peacock[0].verdict == "Pass"


def test_name_convention_checks_are_manual_review() -> None:
    """Kerri's call (2026-06-02): naming conventions vary by client, so the
    ad-set and ad name checks are MANUAL — always Review with instructions,
    never an auto-guess, even when the builder filled in 'Yes'."""
    for check_id in ("adset_name_conventions", "ad_name_conventions"):
        rows = [_row(check_id, builder_input="Yes")]
        results = execute_checks(rows, _passing_runner)
        assert results[0].verdict == "Review", check_id
        assert "naming convention" in results[0].action.lower(), check_id


def test_manual_naming_review_surfaces_actual_names() -> None:
    """Enrichment: the manual naming Review includes the actual name(s) so the
    reviewer doesn't have to open Ads Manager to see them."""
    ev = {"campaign": {}, "ad_sets": [{"name": "Peacock_FBIG_ACQ_2501"}], "ads": []}
    results = execute_checks([_row("adset_name_conventions", "Yes")], _passing_runner, evidence=ev)
    assert results[0].verdict == "Review"
    assert "Peacock_FBIG_ACQ_2501" in results[0].action


def test_manual_dimensions_review_surfaces_ad_count() -> None:
    ev = {"campaign": {}, "ad_sets": [], "ads": [{"id": "1"}, {"id": "2"}]}
    results = execute_checks([_row("ad_creative_dimensions", "")], _passing_runner, evidence=ev)
    assert results[0].verdict == "Review"
    assert "2 ad(s)" in results[0].action


def test_manual_review_context_safe_without_evidence() -> None:
    # No evidence -> base instruction unchanged, no crash.
    results = execute_checks([_row("ad_name_conventions", "Yes")], _passing_runner)
    assert results[0].verdict == "Review"
    assert "naming convention" in results[0].action.lower()


def test_execute_checks_force_run_overrides_na(monkeypatch) -> None:
    """A force-run check runs even with blank input."""
    rows = [_row("always_run_check", builder_input="")]
    results = execute_checks(
        rows, _passing_runner, force_run_check_ids={"always_run_check"}
    )
    assert results[0].verdict == "Pass"


def test_execute_checks_runner_without_evidence_param() -> None:
    """Runners that don't accept evidence= still work (TypeError fallback)."""

    def runner_no_evidence(row: CheckRow) -> CheckResult:
        return CheckResult(row.row_index, row.check_id, "Fix", "needs fixing")

    rows = [_row("bid_strategy", builder_input="Lowest cost")]
    results = execute_checks(rows, runner_no_evidence)
    assert results[0].verdict == "Fix"
