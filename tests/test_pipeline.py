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
