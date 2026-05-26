"""Deterministic check execution and verdict summarization. Pure functions, no adapters.

Mirrors the Search repo's pipeline: iterate CheckRows, skip blanks, honor
manual-review-by-design checks, run the rest through the registry runner, and
tally a summary. The orchestration service calls execute_checks + build_summary;
keeping them here (adapter-free) makes them trivially testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models import CheckResult, CheckRow, RunSummary

CheckRunner = Callable[..., CheckResult]

# Checks that must run even when the builder leaves the input blank. None yet
# for Social; the Search side uses this for its "downloaded_changes" check.
ALWAYS_RUN_CHECK_IDS: frozenset[str] = frozenset()

# Manual-by-design checks: always Review with a fixed instruction, never auto-attempted.
# Keyed by check_id. Populated as Kerri's manual rows get check_ids assigned.
ALWAYS_REVIEW_CHECK_ACTIONS: dict[str, str] = {
    # Example (pending check_id assignment):
    # "creative_dimensions_1x1_9x16": "Verify 1x1 and 9x16 creative manually in Ads Manager.",
}


def is_builder_na(value: str) -> bool:
    """True when the builder left the expected-value cell blank or marked N/A."""
    return (value or "").strip().lower() in {"", "n/a", "na", "none"}


def build_summary(results: list[CheckResult]) -> RunSummary:
    """Tally verdict counts across results."""
    summary = RunSummary()
    for result in results:
        verdict = (result.verdict or "").strip().lower()
        if verdict == "pass":
            summary.pass_count += 1
        elif verdict == "fix":
            summary.fix_count += 1
        elif verdict == "review":
            summary.review_count += 1
        elif verdict in {"n/a", "na"}:
            summary.na_count += 1
        else:
            summary.error_count += 1
    return summary


def execute_checks(
    rows: list[CheckRow],
    check_runner: CheckRunner,
    *,
    evidence: dict[str, Any] | None = None,
    force_run_check_ids: set[str] | None = None,
) -> list[CheckResult]:
    """Run each checkable row through the registry runner.

    - Rows without a check_id are skipped (section headers, instructions).
    - Manual-review-by-design check_ids return Review with their fixed note.
    - Rows whose builder input is blank/N/A return N/A (unless force-run).
    - Everything else is dispatched to check_runner(row, evidence=...).

    builder_input/builder_notes are copied onto each result so the writer and
    Slack summary have them without re-reading the sheet.
    """
    results: list[CheckResult] = []
    force = (force_run_check_ids or set()) | set(ALWAYS_RUN_CHECK_IDS)

    for row in rows:
        if not row.check_id:
            continue

        if row.check_id in ALWAYS_REVIEW_CHECK_ACTIONS:
            results.append(
                CheckResult(
                    row_index=row.row_index,
                    check_id=row.check_id,
                    verdict="Review",
                    action=ALWAYS_REVIEW_CHECK_ACTIONS[row.check_id],
                    builder_input=row.builder_input,
                    builder_notes=row.builder_notes,
                )
            )
            continue

        if is_builder_na(row.builder_input) and row.check_id not in force:
            results.append(
                CheckResult(
                    row_index=row.row_index,
                    check_id=row.check_id,
                    verdict="N/A",
                    action="Builder input was blank or N/A; check skipped.",
                    builder_input=row.builder_input,
                    builder_notes=row.builder_notes,
                )
            )
            continue

        # The registry runner accepts (row, evidence=...); fall back to (row)
        # for runners/tests that don't take evidence.
        try:
            result = check_runner(row, evidence=evidence)
        except TypeError:
            result = check_runner(row)

        result.builder_input = row.builder_input
        result.builder_notes = row.builder_notes
        results.append(result)

    return results
