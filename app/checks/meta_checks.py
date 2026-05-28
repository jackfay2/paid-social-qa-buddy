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

Value-map calibration:
- Objective map is calibrated to Brandon's rule (2026-05-28): match against the
  new ODAX objectives (Sales / Traffic / Leads / Awareness / Engagement / App
  Promotion), and treat legacy Meta enums (CONVERSIONS, LEAD_GENERATION,
  PAGE_LIKES, etc.) as equivalent to their modern replacements per Meta's
  official objective migration.
- Buying-type set (AUCTION / RESERVED / FIXED_CPM) is stable per Meta's docs.
- Future value-mapped checks: validate maps with Brandon/Kerri before trusting
  Fix verdicts.
"""

from __future__ import annotations

from datetime import date, datetime
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

# Meta objective values mapped to the ODAX OUTCOME_* canonical form.
#
# Brandon (2026-05-28) confirmed: new campaigns being QA'd use the new ODAX
# objectives only. Older campaigns may still carry legacy enum values; those
# should be treated as equivalent to their modern replacement per Meta's
# official migration. So both friendly inputs AND legacy enums normalize to the
# same OUTCOME_* key, and the comparison Passes cleanly across either taxonomy.
_OBJECTIVE_SYNONYMS = {
    # New ODAX objectives (what builders type and what new campaigns store).
    "awareness": "OUTCOME_AWARENESS",
    "traffic": "OUTCOME_TRAFFIC",
    "engagement": "OUTCOME_ENGAGEMENT",
    "leads": "OUTCOME_LEADS",
    "sales": "OUTCOME_SALES",
    "app promotion": "OUTCOME_APP_PROMOTION",
    "app promo": "OUTCOME_APP_PROMOTION",
    # Legacy Meta enums (pre-ODAX), mapped to their modern replacements per
    # Meta's official objective migration. Older campaigns may carry these.
    "conversions": "OUTCOME_SALES",
    "product catalog sales": "OUTCOME_SALES",
    "lead generation": "OUTCOME_LEADS",
    "brand awareness": "OUTCOME_AWARENESS",
    "reach": "OUTCOME_AWARENESS",
    "store visits": "OUTCOME_AWARENESS",
    "link clicks": "OUTCOME_TRAFFIC",
    "post engagement": "OUTCOME_ENGAGEMENT",
    "page likes": "OUTCOME_ENGAGEMENT",
    "event responses": "OUTCOME_ENGAGEMENT",
    "video views": "OUTCOME_ENGAGEMENT",
    "messages": "OUTCOME_ENGAGEMENT",
    "app installs": "OUTCOME_APP_PROMOTION",
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


# --- campaign_status -------------------------------------------------------

# Known Meta status enums (effective_status / status fields).
_KNOWN_META_STATUSES = {
    "ACTIVE",
    "PAUSED",
    "DELETED",
    "ARCHIVED",
    "PENDING_REVIEW",
    "DISAPPROVED",
    "PREAPPROVED",
    "PENDING_BILLING_INFO",
    "CAMPAIGN_PAUSED",
    "ADSET_PAUSED",
}

_STATUS_SYNONYMS = {
    "live": "ACTIVE",
    "active": "ACTIVE",
    "on": "ACTIVE",
    "running": "ACTIVE",
    "paused": "PAUSED",
    "off": "PAUSED",
    "archived": "ARCHIVED",
    "deleted": "DELETED",
}


def _canonical_status(value: Any) -> str:
    norm = _norm(value)
    if not norm:
        return ""
    upper = norm.upper().replace(" ", "_")
    if upper in _KNOWN_META_STATUSES:
        return upper
    return _STATUS_SYNONYMS.get(norm, "")


def check_campaign_status(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    actual = _campaign(evidence).get("effective_status")
    if _is_blank(actual):
        return _review(
            row, "Campaign status not available in BigQuery; verify manually."
        )

    expected = _canonical_status(row.builder_input)
    actual_canon = _canonical_status(actual)

    if not expected:
        return _review(
            row,
            f'Could not interpret the expected status "{row.builder_input}". '
            f'Actual is "{actual}". Verify manually.',
        )
    if not actual_canon:
        return _review(
            row, f'Actual status "{actual}" not recognized. Verify manually.'
        )
    if expected == actual_canon:
        return _pass(row)
    return _fix(row, f'Expected "{row.builder_input}", got "{actual}"')


# --- campaign_start_date ---------------------------------------------------

# Date formats we accept from builder inputs. Kerri's template suggests
# MM/DD/YYYY 00:00:00 PM, but builders often abbreviate; we accept common forms.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p",
)


def _parse_date(value: Any) -> date | None:
    """Parse a date from a builder string or BQ TIMESTAMP. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Final fallback: Python's flexible ISO parser handles many variants.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def check_campaign_start_date(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    actual = _campaign(evidence).get("start_time")
    if _is_blank(actual):
        return _review(
            row, "Campaign start time not available in BigQuery; verify manually."
        )

    expected_date = _parse_date(row.builder_input)
    actual_date = _parse_date(actual)

    if expected_date is None:
        return _review(
            row,
            f'Could not parse the expected start date "{row.builder_input}". '
            "Use YYYY-MM-DD or MM/DD/YYYY.",
        )
    if actual_date is None:
        return _review(
            row,
            f'Actual start time "{actual}" could not be parsed. Verify manually.',
        )
    if expected_date == actual_date:
        return _pass(row)
    return _fix(
        row,
        f"Expected start date {expected_date.isoformat()}, "
        f"got {actual_date.isoformat()}",
    )


# --- campaign_bid_strategy -------------------------------------------------

_KNOWN_BID_STRATEGIES = {
    "LOWEST_COST_WITHOUT_CAP",
    "LOWEST_COST_WITH_BID_CAP",
    "COST_CAP",
    "LOWEST_COST_WITH_MIN_ROAS",
    "TARGET_COST",
}

# Meta UI labels and common shorthands mapped to the enum values. A reasonable
# first pass that leans toward Review on unknown inputs; validate with Brandon
# against real QA sheets before trusting Fix verdicts at scale.
_BID_STRATEGY_SYNONYMS = {
    "lowest cost": "LOWEST_COST_WITHOUT_CAP",
    "lowest cost without cap": "LOWEST_COST_WITHOUT_CAP",
    "highest volume": "LOWEST_COST_WITHOUT_CAP",
    "auto bid": "LOWEST_COST_WITHOUT_CAP",
    "auto": "LOWEST_COST_WITHOUT_CAP",
    "automatic": "LOWEST_COST_WITHOUT_CAP",
    "bid cap": "LOWEST_COST_WITH_BID_CAP",
    "lowest cost with bid cap": "LOWEST_COST_WITH_BID_CAP",
    "cost cap": "COST_CAP",
    "cost per result goal": "COST_CAP",
    "cost per result": "COST_CAP",
    "roas goal": "LOWEST_COST_WITH_MIN_ROAS",
    "min roas": "LOWEST_COST_WITH_MIN_ROAS",
    "minimum roas": "LOWEST_COST_WITH_MIN_ROAS",
    "lowest cost with min roas": "LOWEST_COST_WITH_MIN_ROAS",
    "target cost": "TARGET_COST",
}


def _canonical_bid_strategy(value: Any) -> str:
    norm = _norm(value)
    if not norm:
        return ""
    upper = norm.upper().replace(" ", "_")
    if upper in _KNOWN_BID_STRATEGIES:
        return upper
    return _BID_STRATEGY_SYNONYMS.get(norm, "")


def check_campaign_bid_strategy(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    actual = _campaign(evidence).get("bid_strategy")
    if _is_blank(actual):
        return _review(
            row, "Bid strategy not available in BigQuery; verify manually."
        )

    expected = _canonical_bid_strategy(row.builder_input)
    actual_canon = _canonical_bid_strategy(actual)

    if not expected:
        return _review(
            row,
            f'Could not interpret the expected bid strategy "{row.builder_input}". '
            f'Actual is "{actual}". Verify manually.',
        )
    if not actual_canon:
        return _review(
            row,
            f'Actual bid strategy "{actual}" not recognized. Verify manually.',
        )
    if expected == actual_canon:
        return _pass(row)
    return _fix(row, f'Expected "{row.builder_input}", got "{actual}"')
