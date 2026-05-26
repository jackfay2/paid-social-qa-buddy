"""Meta deterministic check functions.

Each check takes (row, *, evidence) and returns a CheckResult. Pattern mirrors
the Search repo's search_checks.py: small verdict-constructor helpers, then one
function per check. Registered in app.checks.registry.

Design principles (from the handoff):
  - Default to Review on uncertainty; never emit a false Pass (Peacock-Olympics
    rule). A wrong Fix erodes trust; a wrong Pass costs a weekend rebuild.
  - When a field isn't in BigQuery yet, return Review ("not available"), so the
    check auto-upgrades to deterministic when Riley/Nikki land the column.
  - Meta stores enum values (OUTCOME_TRAFFIC); builders type friendly values
    (Traffic). Normalize + map before comparing — this is the false-negative
    trap that made the old spreadsheet-formula approach useless (handoff §2).

NOTE: the value mappings below (objectives, buying types) are a reasonable first
pass. They SHOULD be validated against real QA sheets / with Kerri before we
fully trust Fix verdicts in production. Until then they lean toward Review when
they can't confidently interpret a value.
"""

from __future__ import annotations

from typing import Any

from app.models import CheckResult, CheckRow


# --- verdict constructors --------------------------------------------------


def _pass(row: CheckRow, action: str = "") -> CheckResult:
    return CheckResult(row.row_index, row.check_id, "Pass", action)


def _fix(row: CheckRow, action: str) -> CheckResult:
    return CheckResult(row.row_index, row.check_id, "Fix", action)


def _review(row: CheckRow, action: str) -> CheckResult:
    return CheckResult(row.row_index, row.check_id, "Review", action)


# --- helpers ---------------------------------------------------------------


def _norm(value: Any) -> str:
    """Lowercase, collapse whitespace, treat underscores as spaces."""
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _campaign(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    campaign = evidence.get("campaign")
    return campaign if isinstance(campaign, dict) else {}


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


# --- campaign_objective ----------------------------------------------------

# Meta ODAX objective enums, mapped from common builder-friendly inputs.
_OBJECTIVE_SYNONYMS = {
    "awareness": "OUTCOME_AWARENESS",
    "traffic": "OUTCOME_TRAFFIC",
    "engagement": "OUTCOME_ENGAGEMENT",
    "leads": "OUTCOME_LEADS",
    "sales": "OUTCOME_SALES",
    "conversions": "OUTCOME_SALES",
    "app promotion": "OUTCOME_APP_PROMOTION",
    "app promo": "OUTCOME_APP_PROMOTION",
}


def _canonical_objective(value: Any) -> str:
    """Map a builder input or BQ value to a canonical OUTCOME_* enum, or "" if
    it can't be confidently interpreted."""
    norm = _norm(value)
    if not norm:
        return ""
    # Already an enum (possibly with spaces from normalization): canonicalize.
    upper = norm.upper().replace(" ", "_")
    if upper.startswith("OUTCOME_"):
        return upper
    return _OBJECTIVE_SYNONYMS.get(norm, "")


def check_campaign_objective(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    actual = _campaign(evidence).get("objective")
    if _is_blank(actual):
        return _review(
            row, "Campaign objective not available in BigQuery; verify manually."
        )

    expected_canon = _canonical_objective(row.builder_input)
    actual_canon = _canonical_objective(actual)

    if not expected_canon:
        return _review(
            row,
            f'Could not interpret the expected objective "{row.builder_input}". '
            f'Actual is "{actual}". Verify manually.',
        )
    if not actual_canon:
        return _review(
            row, f'Actual objective "{actual}" not recognized. Verify manually.'
        )
    if expected_canon == actual_canon:
        return _pass(row)
    return _fix(row, f'Expected "{row.builder_input}", got "{actual}"')


# --- campaign_buying_type --------------------------------------------------

_KNOWN_BUYING_TYPES = {"AUCTION", "RESERVED", "FIXED_CPM"}


def _canonical_buying_type(value: Any) -> str:
    return _norm(value).upper().replace(" ", "_")


def check_campaign_buying_type(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    actual = _campaign(evidence).get("buying_type")
    if _is_blank(actual):
        return _review(
            row, "Buying type not available in BigQuery; verify manually."
        )

    expected = _canonical_buying_type(row.builder_input)
    actual_canon = _canonical_buying_type(actual)

    if not expected:
        return _review(row, "Expected buying type is blank; verify manually.")
    if expected not in _KNOWN_BUYING_TYPES:
        return _review(
            row,
            f'Could not interpret the expected buying type "{row.builder_input}". '
            f'Actual is "{actual}". Verify manually.',
        )
    if expected == actual_canon:
        return _pass(row)
    return _fix(row, f'Expected "{row.builder_input}", got "{actual}"')
