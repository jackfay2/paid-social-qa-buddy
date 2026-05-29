"""Deterministic check execution and verdict summarization. Pure functions, no adapters.

Mirrors the Search repo's pipeline: iterate CheckRows, skip blanks, honor
manual-review-by-design checks, run the rest through the registry runner, and
tally a summary. The orchestration service calls execute_checks + build_summary;
keeping them here (adapter-free) makes them trivially testable.

Text checks (Gemini-evaluated) live in `execute_text_checks` below. The
orchestration service calls both, then concatenates results before writing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Import the module (not its names) so tests can monkeypatch
# TEXT_CHECK_DEFINITIONS on the source module and have changes flow through.
# Re-binding by name with `from ... import` would freeze a reference to the
# original dict.
from app.checks import text_checks as _text_checks
from app.models import CheckResult, CheckRow, RunSummary

CheckRunner = Callable[..., CheckResult]

# Checks that must run even when the builder leaves the input blank. None yet
# for Social; the Search side uses this for its "downloaded_changes" check.
ALWAYS_RUN_CHECK_IDS: frozenset[str] = frozenset()

# Manual-by-design checks: always Review with a fixed instruction, never
# auto-attempted (handoff hard rule #9). Checked before the blank/N-A gate, so
# they surface even when the builder leaves the input empty. Keyed by check_id.
ALWAYS_REVIEW_CHECK_ACTIONS: dict[str, str] = {
    # The brief mandates at least one manual check (download changes).
    "download_changes": (
        "Manual check: confirm recent changes were downloaded in Meta Ads "
        "Manager / Editor before building (or note 'Built in platform')."
    ),
    # Template marks this row MANUAL — creative dimensions can't be verified
    # from BigQuery fields reliably.
    "ad_creative_dimensions": (
        "Manual check: verify the 1x1 and 9x16 creative are present and correct "
        "in Ads Manager."
    ),
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

        if _text_checks.is_text_check(row.check_id):
            # Text checks are handled by execute_text_checks (one batched
            # Gemini call across the job). Skip here so they don't fall
            # through to the deterministic registry and produce
            # "Error: Unrecognized check_id".
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


# --- Text checks (Gemini) --------------------------------------------------


def execute_text_checks(
    rows: list[CheckRow],
    ads: list[dict[str, Any]] | None,
    gemini_client: Any,
) -> list[CheckResult]:
    """Run all text-check rows in one batched Gemini call; aggregate per-ad verdicts.

    Per-row semantics (matches the deterministic ad-set checks): if any ad in
    the campaign Fixes for this check, the row is Fix and points at that ad. If
    any ad Reviews and none Fix, the row is Review. Otherwise Pass. When no ad
    has populated text for the row's `ad_field`, the row is Review with a
    "no ad text available" note — never a false Pass.

    Returns an empty list when there are no text-check rows or no gemini_client
    is wired (graceful degradation on the local/test path).
    """
    if not rows or gemini_client is None:
        return []

    text_rows = [
        r for r in rows if r.check_id and _text_checks.is_text_check(r.check_id)
    ]
    if not text_rows:
        return []

    ads = ads or []

    # Build the Gemini batch: one item per (row, ad-with-text). The
    # `check_id` field on each batch item is a compound key the Gemini
    # adapter uses to key its response — decoded back to (row, ad) on merge.
    batch_items: list[dict[str, Any]] = []
    row_to_ad_keys: dict[int, list[tuple[str, str]]] = {}

    for row in text_rows:
        spec = _text_checks.TEXT_CHECK_DEFINITIONS[row.check_id]
        for ad in ads:
            if not isinstance(ad, dict):
                continue
            # Try the primary path then per-client fallbacks (object_story_spec
            # vs flat creative.body/title — confirmed to vary on live data).
            text = _text_checks.resolve_ad_text(ad, spec)
            if not text:
                continue
            ad_identifier = str(ad.get("id") or ad.get("ad_id") or len(batch_items))
            batch_key = f"r{row.row_index}_a{ad_identifier}"
            batch_items.append(
                {
                    "check_id": batch_key,
                    "instruction": spec.instruction,
                    "text": text,
                }
            )
            row_to_ad_keys.setdefault(row.row_index, []).append(
                (batch_key, _text_checks.ad_label(ad))
            )

    response = gemini_client.run_text_checks(batch_items) or {}
    raw_results = response.get("check_results") or {}
    if not isinstance(raw_results, dict):
        raw_results = {}

    return [
        _aggregate_text_row(row, row_to_ad_keys.get(row.row_index, []), raw_results)
        for row in text_rows
    ]


def _aggregate_text_row(
    row: CheckRow,
    ad_keys: list[tuple[str, str]],
    gemini_results: dict[str, Any],
) -> CheckResult:
    """Combine per-ad Gemini verdicts for one row into a single CheckResult."""

    def _result(verdict: str, action: str) -> CheckResult:
        return CheckResult(
            row_index=row.row_index,
            check_id=row.check_id,
            verdict=verdict,
            action=action,
            builder_input=row.builder_input,
            builder_notes=row.builder_notes,
        )

    if not ad_keys:
        return _result(
            "Review",
            "No ad text available for this check; verify manually.",
        )

    fix_ad: tuple[str, str] | None = None
    review_ad: tuple[str, str] | None = None

    for batch_key, label in ad_keys:
        per_ad = gemini_results.get(batch_key)
        if not isinstance(per_ad, dict):
            # Defensive: if Gemini didn't return anything for this ad,
            # treat as Review (never silently Pass).
            if review_ad is None:
                review_ad = (label, "No Gemini result; verify manually.")
            continue

        verdict = str(per_ad.get("verdict", "Review")).strip().title()
        action = str(per_ad.get("action", "")).strip()

        if verdict == "Fix":
            fix_ad = (label, action)
            break  # Fix beats everything; stop scanning.
        if verdict != "Pass" and review_ad is None:
            review_ad = (label, action or "Gemini wasn't confident; verify manually.")

    if fix_ad is not None:
        label, action = fix_ad
        message = f"{label}: {action}" if action else label
        return _result("Fix", message)
    if review_ad is not None:
        label, action = review_ad
        return _result("Review", f"{label}: {action}" if action else label)
    return _result("Pass", "")
