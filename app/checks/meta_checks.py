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

import math
import re
from datetime import date, datetime
from typing import Any

from app.checks._targeting import read_targeting
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


def _ad_sets(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the ad_sets list from evidence, defensively coerced.

    A campaign may have 0..N ad sets. The handful of ad-set-level checks are
    interpreted as "all ad sets must match the builder's expectation" — if any
    ad set diverges, the check Fixes with that ad set's value.
    """
    if not isinstance(evidence, dict):
        return []
    ad_sets = evidence.get("ad_sets")
    if not isinstance(ad_sets, list):
        return []
    return [a for a in ad_sets if isinstance(a, dict)]


def _adset_label(adset: dict[str, Any]) -> str:
    """Best-effort identifier for error messages."""
    name = adset.get("name") or adset.get("adset_name")
    if name:
        return str(name)
    adset_id = adset.get("id") or adset.get("adset_id") or adset.get("ad_set_id")
    return f"ad set {adset_id}" if adset_id else "an ad set"


def _ads(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the ads list from evidence, defensively coerced.

    Ad-level checks interpret the builder input as "every ad must match this
    expectation" — same multi-entity rule as ad-set checks. The exception is
    aggregate checks (e.g. ad_count) which read the count directly.
    """
    if not isinstance(evidence, dict):
        return []
    ads = evidence.get("ads")
    if not isinstance(ads, list):
        return []
    return [a for a in ads if isinstance(a, dict)]


def _ad_label(ad: dict[str, Any]) -> str:
    """Best-effort identifier for ad-level error messages."""
    name = ad.get("name") or ad.get("ad_name")
    if name:
        return str(name)
    ad_id = ad.get("id") or ad.get("ad_id")
    return f"ad {ad_id}" if ad_id else "an ad"


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _known_context(evidence: dict[str, Any] | None) -> str:
    """A short suffix of campaign facts we DO know, to enrich a Review/manual
    message when the specific field isn't in the data — so a human reviewer gets a
    head start instead of a dead end. Empty string when nothing useful is known."""
    bits: list[str] = []
    objective = _campaign(evidence).get("objective")
    if not _is_blank(objective):
        bits.append(f"objective {objective}")
    for adset in _ad_sets(evidence):
        goal = adset.get("optimization_goal")
        if not _is_blank(goal):
            bits.append(f"optimization goal {goal}")
            break
    return f" Known: {', '.join(bits)}." if bits else ""


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


def _peacock_mode(evidence: dict[str, Any] | None) -> bool:
    """True when this run is a Peacock account (its data + vocabulary differ)."""
    return bool(isinstance(evidence, dict) and evidence.get("peacock_mode"))


def _peacock_value_match(row: CheckRow, actual: Any, label: str) -> CheckResult:
    """Compare builder vs BQ in Peacock's OWN vocabulary (no Meta-enum mapping).

    Peacock stores its own terms — Objective='Acquisition', Buy_Type='Biddable' —
    and builders QA in those same terms (Kerri approved Peacock-specific
    vocabulary, 2026-06-03). So a normalized direct match is the right comparison.
    Blank actual -> Review (never a false Pass).
    """
    if _is_blank(actual):
        return _review(row, f"{label} not available in Peacock data; verify manually.")
    if _norm(row.builder_input) == _norm(actual):
        return _pass(row)
    return _fix(row, f'Expected "{row.builder_input}", got "{actual}"')


def check_campaign_objective(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    actual = _campaign(evidence).get("objective")
    if _peacock_mode(evidence):
        return _peacock_value_match(row, actual, "Campaign objective")
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

# Builder-facing labels (from the QA template's MASTER DATA VALIDATION dropdown)
# mapped to the Meta enum stored in BigQuery. The template offers "Reservation",
# but Meta's buying_type enum value is RESERVED — without this the check would
# return a false Review on a valid, dropdown-selected input.
_BUYING_TYPE_SYNONYMS = {
    "auction": "AUCTION",
    "reservation": "RESERVED",
    "reserved": "RESERVED",
    "fixed cpm": "FIXED_CPM",
}


def _canonical_buying_type(value: Any) -> str:
    norm = _norm(value)
    if not norm:
        return ""
    upper = norm.upper().replace(" ", "_")
    if upper in _KNOWN_BUYING_TYPES:
        return upper
    return _BUYING_TYPE_SYNONYMS.get(norm, "")


def check_campaign_buying_type(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    actual = _campaign(evidence).get("buying_type")
    if _peacock_mode(evidence):
        return _peacock_value_match(row, actual, "Buying type")
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


# --- campaign_budget -------------------------------------------------------


def _parse_money(value: Any) -> float | None:
    """Parse a builder-entered budget into dollars. Strips $, commas, and a
    trailing 'usd'. Returns None on anything it can't read cleanly (caller →
    Review, never a wrong guess)."""
    if value is None:
        return None
    s = str(value).strip().lower().replace("$", "").replace(",", "").replace("usd", "").strip()
    if not s:
        return None
    try:
        parsed = float(s)
    except ValueError:
        return None
    # float() accepts "nan"/"inf"/"infinity" without raising; reject non-finite
    # so they become Review, not a bogus comparison → wrong Fix.
    return parsed if math.isfinite(parsed) else None


def check_campaign_budget(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    """Compare the builder's expected campaign budget to BigQuery.

    BigQuery stores budgets in MINOR units (cents): daily_budget=200000 → $2,000.
    A campaign normally has either a daily OR a lifetime budget (the other is 0);
    when both are 0 the budget is set per ad set (CBO off) → Review.

    Defensive by design (input format pending Kerri): we ASSUME the builder types
    dollars and disclose that in the action. If their number instead matches the
    raw cents value, that's a units ambiguity → Review (not a wrong Fix). Anything
    unparseable → Review. So this is safe regardless of the final format decision.
    """
    campaign = _campaign(evidence)
    daily = _parse_int(campaign.get("daily_budget"))
    lifetime = _parse_int(campaign.get("lifetime_budget"))

    if daily is None and lifetime is None:
        return _review(row, "Campaign budget not available in BigQuery; verify manually.")

    daily_set = bool(daily and daily > 0)
    lifetime_set = bool(lifetime and lifetime > 0)
    if not daily_set and not lifetime_set:
        return _review(
            row,
            "No campaign-level budget is set (it may be set per ad set / CBO off); "
            "verify manually.",
        )
    if daily_set and lifetime_set:
        return _review(
            row, "Both a daily and a lifetime campaign budget are set; verify manually."
        )

    budget_minor = daily if daily_set else lifetime
    kind = "daily" if daily_set else "lifetime"
    budget_dollars = budget_minor / 100.0

    expected = _parse_money(row.builder_input)
    if expected is None:
        return _review(
            row,
            f'Could not interpret the expected budget "{row.builder_input}". '
            f"Actual {kind} budget is ${budget_dollars:,.2f}. Verify manually.",
        )

    if abs(expected - budget_dollars) <= 0.01:
        return _pass(row, f"{kind.capitalize()} budget ${budget_dollars:,.2f} matches.")
    # Units guard: their number matches the raw cents value → likely a
    # dollars-vs-cents convention difference, not a real mismatch → Review.
    if abs(expected - budget_minor) <= 0.01:
        return _review(
            row,
            f'Expected "{row.builder_input}" equals the raw cents value; the {kind} '
            f"budget is ${budget_dollars:,.2f}. Confirm budgets are entered in dollars.",
        )
    return _fix(
        row,
        f"Expected ${expected:,.2f}, but the {kind} budget is ${budget_dollars:,.2f} "
        "(compared as dollars — BigQuery stores minor units).",
    )


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

# Meta UI labels and common shorthands mapped to the enum values. The four
# entries marked (DROPDOWN) are the exact builder-facing labels from the QA
# template's MASTER DATA VALIDATION tab — the values a builder can actually
# select. The rest are common shorthands. Leans toward Review on unknown input.
_BID_STRATEGY_SYNONYMS = {
    "highest volume or value": "LOWEST_COST_WITHOUT_CAP",  # (DROPDOWN)
    "cost per result goal": "COST_CAP",                    # (DROPDOWN)
    "roas goal": "LOWEST_COST_WITH_MIN_ROAS",              # (DROPDOWN)
    "bid cap": "LOWEST_COST_WITH_BID_CAP",                 # (DROPDOWN)
    "lowest cost": "LOWEST_COST_WITHOUT_CAP",
    "lowest cost without cap": "LOWEST_COST_WITHOUT_CAP",
    "highest volume": "LOWEST_COST_WITHOUT_CAP",
    "highest value": "LOWEST_COST_WITHOUT_CAP",
    "auto bid": "LOWEST_COST_WITHOUT_CAP",
    "auto": "LOWEST_COST_WITHOUT_CAP",
    "automatic": "LOWEST_COST_WITHOUT_CAP",
    "lowest cost with bid cap": "LOWEST_COST_WITH_BID_CAP",
    "cost cap": "COST_CAP",
    "cost per result": "COST_CAP",
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


# === Ad-set level checks ===================================================
#
# Semantics: a campaign has 0..N ad sets. Each check below interprets the
# builder input as "every ad set must match this expectation." That mirrors how
# the QA sheet is filled — one row per setting, expected to hold across all ad
# sets in the campaign. If any ad set diverges, we Fix and point at that one.
#
# Per-client BQ schemas vary on ad-set fields too. Missing field → Review, not
# Fix. The Fix verdict requires us to be sure we saw the actual value.


# --- adset_status ----------------------------------------------------------


def check_adset_status(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _canonical_status(row.builder_input)
    if not expected:
        return _review(
            row,
            f'Could not interpret the expected ad set status "{row.builder_input}".',
        )

    mismatched: list[tuple[str, str]] = []
    unparseable: list[tuple[str, str]] = []
    missing: list[str] = []

    for adset in ad_sets:
        actual = adset.get("effective_status")
        if _is_blank(actual):
            missing.append(_adset_label(adset))
            continue
        actual_canon = _canonical_status(actual)
        if not actual_canon:
            unparseable.append((_adset_label(adset), str(actual)))
            continue
        if actual_canon != expected:
            mismatched.append((_adset_label(adset), str(actual)))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f'Expected "{row.builder_input}", but {first_label} is "{first_actual}"{more}',
        )
    if unparseable:
        label, raw = unparseable[0]
        return _review(
            row, f'Ad set status "{raw}" on {label} not recognized. Verify manually.'
        )
    if missing:
        # All ad sets were missing the field — pure Review, no signal.
        if len(missing) == len(ad_sets):
            return _review(
                row,
                "Ad set status not available in BigQuery for any ad set; verify manually.",
            )
        # Some had it and matched, some didn't — still a Pass on what we saw,
        # but flag the partial coverage so the builder knows.
        return _pass(row, f"({len(missing)} of {len(ad_sets)} ad sets missing status)")
    return _pass(row)


# --- adset_start_date / adset_end_date -------------------------------------


def _check_adset_date_field(
    row: CheckRow,
    *,
    evidence: dict[str, Any] | None,
    field: str,
    label: str,
) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected_date = _parse_date(row.builder_input)
    if expected_date is None:
        return _review(
            row,
            f'Could not parse the expected {label} "{row.builder_input}". '
            "Use YYYY-MM-DD or MM/DD/YYYY.",
        )

    mismatched: list[tuple[str, str]] = []
    unparseable: list[tuple[str, str]] = []
    missing: list[str] = []

    for adset in ad_sets:
        actual = adset.get(field)
        if _is_blank(actual):
            missing.append(_adset_label(adset))
            continue
        actual_date = _parse_date(actual)
        if actual_date is None:
            unparseable.append((_adset_label(adset), str(actual)))
            continue
        if actual_date != expected_date:
            mismatched.append((_adset_label(adset), actual_date.isoformat()))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        # Peacock: the ad-set date is an aggregate of per-creative flight windows
        # (min start / max end) and the flight→template-date mapping is unconfirmed
        # (Kerri). A mismatch is surfaced for review, never a false Fix — Pass only
        # on an exact match. (Promote to Fix once the date semantics are locked.)
        if _peacock_mode(evidence):
            return _review(
                row,
                f"Trafficked flight {label} is {first_actual} (expected "
                f"{expected_date.isoformat()}){more}; confirm against the flight window.",
            )
        return _fix(
            row,
            f"Expected {label} {expected_date.isoformat()}, "
            f"but {first_label} is {first_actual}{more}",
        )
    if unparseable:
        label_str, raw = unparseable[0]
        return _review(
            row,
            f'Ad set {label} "{raw}" on {label_str} could not be parsed. Verify manually.',
        )
    if missing:
        if len(missing) == len(ad_sets):
            return _review(
                row,
                f"Ad set {label} not available in BigQuery for any ad set; verify manually.",
            )
        return _pass(row, f"({len(missing)} of {len(ad_sets)} ad sets missing {label})")
    return _pass(row)


def check_adset_start_date(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _check_adset_date_field(
        row, evidence=evidence, field="start_time", label="start date"
    )


def check_adset_end_date(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _check_adset_date_field(
        row, evidence=evidence, field="end_time", label="end date"
    )


# --- adset_age_min / adset_age_max -----------------------------------------
#
# Meta age fields are integers (`age_min`, `age_max` on the targeting RECORD).
# Builders type integers too — just normalize and compare. Targeting can be
# nested or flat per-client; `read_targeting` handles both.


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int — explicitly reject.
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            f = float(text)
        except ValueError:
            return None
        if f.is_integer():
            return int(f)
        return None


def _check_adset_age_field(
    row: CheckRow,
    *,
    evidence: dict[str, Any] | None,
    field: str,
    label: str,
) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _parse_int(row.builder_input)
    if expected is None:
        return _review(
            row,
            f'Could not parse the expected {label} "{row.builder_input}" as an integer.',
        )

    mismatched: list[tuple[str, int]] = []
    missing: list[str] = []

    for adset in ad_sets:
        targeting = read_targeting(adset)
        actual = _parse_int(targeting.get(field))
        if actual is None:
            missing.append(_adset_label(adset))
            continue
        if actual != expected:
            mismatched.append((_adset_label(adset), actual))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f"Expected {label} {expected}, but {first_label} is {first_actual}{more}",
        )
    if missing:
        if len(missing) == len(ad_sets):
            return _review(
                row,
                f"Ad set {label} not available in BigQuery targeting for any ad set; "
                "verify manually.",
            )
        return _pass(row, f"({len(missing)} of {len(ad_sets)} ad sets missing {label})")
    return _pass(row)


def check_adset_age_min(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _check_adset_age_field(
        row, evidence=evidence, field="age_min", label="age_min"
    )


def check_adset_age_max(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _check_adset_age_field(
        row, evidence=evidence, field="age_max", label="age_max"
    )


# --- adset_genders ---------------------------------------------------------
#
# Meta encodes genders as int codes inside the targeting RECORD:
#   [1]    = Men only
#   [2]    = Women only
#   [1, 2] = All (men + women)
#   absent or empty = All (Meta's default, no targeting restriction)
#
# Builders type the friendly label. Compare canonical sets.


_GENDER_SYNONYMS: dict[str, frozenset[int]] = {
    "all": frozenset({1, 2}),
    "all genders": frozenset({1, 2}),
    "any": frozenset({1, 2}),
    "everyone": frozenset({1, 2}),
    "men and women": frozenset({1, 2}),
    "women and men": frozenset({1, 2}),
    "both": frozenset({1, 2}),
    "men": frozenset({1}),
    "male": frozenset({1}),
    "males": frozenset({1}),
    "m": frozenset({1}),
    "women": frozenset({2}),
    "female": frozenset({2}),
    "females": frozenset({2}),
    "f": frozenset({2}),
}


def _canonical_genders(value: Any) -> frozenset[int] | None:
    """Return a frozenset of {1, 2} | {1} | {2}, or None if uninterpretable.

    Accepts: builder strings ("Men", "All"), int lists ([1, 2]), or string lists.
    Empty/missing maps to the Meta default of {1, 2} (all).
    """
    if value is None:
        return frozenset({1, 2})
    if isinstance(value, (list, tuple)):
        if not value:
            return frozenset({1, 2})
        codes: set[int] = set()
        for item in value:
            i = _parse_int(item)
            if i not in (1, 2):
                return None
            codes.add(i)
        return frozenset(codes) if codes else None
    text = _norm(value)
    if not text:
        return frozenset({1, 2})
    return _GENDER_SYNONYMS.get(text)


def _format_genders(codes: frozenset[int]) -> str:
    if codes == frozenset({1, 2}):
        return "All"
    if codes == frozenset({1}):
        return "Men"
    if codes == frozenset({2}):
        return "Women"
    return str(sorted(codes))


def check_adset_genders(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _canonical_genders(row.builder_input)
    if expected is None:
        return _review(
            row,
            f'Could not interpret the expected genders "{row.builder_input}". '
            "Use Men, Women, or All.",
        )

    mismatched: list[tuple[str, str]] = []
    unparseable: list[tuple[str, str]] = []

    for adset in ad_sets:
        targeting = read_targeting(adset)
        raw = targeting.get("genders")
        actual = _canonical_genders(raw)
        if actual is None:
            unparseable.append((_adset_label(adset), str(raw)))
            continue
        if actual != expected:
            mismatched.append((_adset_label(adset), _format_genders(actual)))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f"Expected {_format_genders(expected)}, "
            f"but {first_label} targets {first_actual}{more}",
        )
    if unparseable:
        label, raw = unparseable[0]
        return _review(
            row, f'Ad set genders "{raw}" on {label} not recognized. Verify manually.'
        )
    return _pass(row)


# --- adset_countries -------------------------------------------------------
#
# Meta stores location targeting as ISO-3166-1 alpha-2 codes in
# `targeting.countries` (list of strings). Builders may type codes ("US, CA")
# or full names ("United States, Canada"). We normalize both sides to a set of
# upper-case 2-letter codes for comparison.


_COUNTRY_NAME_TO_CODE = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "u s": "US",
    "u s a": "US",
    "canada": "CA",
    "mexico": "MX",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "australia": "AU",
    "new zealand": "NZ",
    "ireland": "IE",
    "france": "FR",
    "germany": "DE",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "belgium": "BE",
    "japan": "JP",
    "china": "CN",
    "india": "IN",
    "brazil": "BR",
    "argentina": "AR",
    "south africa": "ZA",
}


def _canonical_country(token: Any) -> str:
    """Normalize a single country token to its ISO-3166-1 alpha-2 code, or '' if
    we can't confidently map it."""
    if token is None:
        return ""
    text = str(token).strip()
    if not text:
        return ""
    # Friendly-name / known-alias map FIRST. Some informal aliases map to a
    # DIFFERENT alpha-2 code than their letters (e.g. "uk" -> "GB"); the 2-letter
    # passthrough below would shadow those, wrong-Fixing a UK campaign that BQ
    # correctly stores as "GB". Consulting the map first fixes that while leaving
    # genuine codes (e.g. "ca" -> "CA") to pass through.
    mapped = _COUNTRY_NAME_TO_CODE.get(_norm(text))
    if mapped:
        return mapped
    # Otherwise an already-2-letter code passes through uppercased.
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return ""


def _parse_country_set(value: Any) -> set[str] | None:
    """Parse builder input or BQ value into a set of alpha-2 codes. Returns None
    if any token in a list is unrecognized, so we can Review rather than
    silently passing on a partial match."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple)):
        tokens = [str(t) for t in value if not _is_blank(t)]
    else:
        tokens = [t.strip() for t in str(value).split(",") if t.strip()]
    if not tokens:
        return set()
    codes: set[str] = set()
    for token in tokens:
        code = _canonical_country(token)
        if not code:
            return None
        codes.add(code)
    return codes


def check_adset_countries(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _parse_country_set(row.builder_input)
    if expected is None or not expected:
        return _review(
            row,
            f'Could not interpret the expected countries "{row.builder_input}". '
            "Use ISO codes (US, CA) or full names.",
        )

    mismatched: list[tuple[str, set[str]]] = []
    unparseable: list[tuple[str, str]] = []
    missing: list[str] = []

    for adset in ad_sets:
        targeting = read_targeting(adset)
        raw = targeting.get("countries")
        if _is_blank(raw) and not isinstance(raw, (list, tuple)):
            missing.append(_adset_label(adset))
            continue
        actual = _parse_country_set(raw)
        if actual is None:
            unparseable.append((_adset_label(adset), str(raw)))
            continue
        if not actual:
            # Empty targeting (e.g. countries == []) — we can't confirm the
            # location; treat as missing → Review, never a wrong Fix.
            missing.append(_adset_label(adset))
            continue
        if actual != expected:
            mismatched.append((_adset_label(adset), actual))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f"Expected countries {sorted(expected)}, "
            f"but {first_label} targets {sorted(first_actual)}{more}",
        )
    if unparseable:
        label, raw = unparseable[0]
        return _review(
            row, f'Ad set countries "{raw}" on {label} not recognized. Verify manually.'
        )
    if missing:
        if len(missing) == len(ad_sets):
            return _review(
                row,
                "Ad set countries not available in BigQuery targeting for any ad set; "
                "verify manually.",
            )
        return _pass(row, f"({len(missing)} of {len(ad_sets)} ad sets missing countries)")
    return _pass(row)


# --- adset_placements (Peacock: AirTable_Placement) ------------------------
#
# Peacock carries the delivery placement per creative (Stories / Reels / In-Feed
# / Creator), aggregated onto each ad set as `placements` by the adapter. Standard
# clients don't have this in BigQuery -> the check Reviews (no data). Conservative
# v1: Pass on an exact set match, else Review (placement vocabulary isn't locked
# with Kerri yet, so we never Fix on a naming variance). Promote to Fix later.


def _norm_placement(value: Any) -> str:
    """Normalize a placement token for comparison: lowercase, drop spaces/hyphens
    ('In-Feed' / 'in feed' -> 'infeed')."""
    return "".join(str(value or "").strip().lower().replace("-", "").split())


def _parse_placement_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple)):
        tokens = [t for t in value if not _is_blank(t)]
    else:
        tokens = [t for t in str(value).split(",") if t.strip()]
    return {_norm_placement(t) for t in tokens if _norm_placement(t)}


def check_adset_placements(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _parse_placement_set(row.builder_input)
    if not expected:
        return _review(
            row,
            f'Could not interpret the expected placements "{row.builder_input}". '
            "Use a comma-separated list (e.g. Stories, Reels, In-Feed).",
        )

    actual: set[str] = set()
    raw_actual: list[str] = []
    have_data = False
    for adset in ad_sets:
        placements = adset.get("placements")
        if isinstance(placements, list) and placements:
            have_data = True
            for placement in placements:
                if not _is_blank(placement):
                    actual.add(_norm_placement(placement))
                    raw_actual.append(str(placement))

    if not have_data:
        return _review(
            row,
            "Placements aren't available in the data for this campaign; verify manually.",
        )
    sizes = ", ".join(sorted(set(raw_actual)))
    if expected == actual:
        return _pass(row, f"Placements match: {sizes}.")
    return _review(
        row,
        f"Trafficked placements are {sizes} (vs expected \"{row.builder_input}\"); "
        "confirm they match.",
    )


# === Ad-level checks ========================================================
#
# Same multi-entity rule as ad-set checks: "every ad must match this builder
# expectation." If any ad diverges, Fix and point at that one. The exception is
# aggregate checks (ad_count) which read the count directly.
#
# Creative is often sparse on paused/old ads (per the 2026-05-27 live finding),
# so checks that read into creative.* fields skip ads with blank text rather
# than misreporting them. The Peacock-Olympics rule still holds: never auto-Pass
# on missing data.


# --- ad_status -------------------------------------------------------------


def check_ad_status(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ads = _ads(evidence)
    if not ads:
        return _review(row, "No ads found in BigQuery for this campaign.")

    expected = _canonical_status(row.builder_input)
    if not expected:
        return _review(
            row,
            f'Could not interpret the expected ad status "{row.builder_input}".',
        )

    mismatched: list[tuple[str, str]] = []
    unparseable: list[tuple[str, str]] = []
    missing: list[str] = []

    for ad in ads:
        actual = ad.get("effective_status")
        if _is_blank(actual):
            missing.append(_ad_label(ad))
            continue
        actual_canon = _canonical_status(actual)
        if not actual_canon:
            unparseable.append((_ad_label(ad), str(actual)))
            continue
        if actual_canon != expected:
            mismatched.append((_ad_label(ad), str(actual)))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f'Expected "{row.builder_input}", but {first_label} is "{first_actual}"{more}',
        )
    if unparseable:
        label, raw = unparseable[0]
        return _review(
            row, f'Ad status "{raw}" on {label} not recognized. Verify manually.'
        )
    if missing:
        if len(missing) == len(ads):
            return _review(
                row,
                "Ad status not available in BigQuery for any ad; verify manually.",
            )
        return _pass(row, f"({len(missing)} of {len(ads)} ads missing status)")
    return _pass(row)


# --- ad_count --------------------------------------------------------------
#
# Aggregate over the ads list — does the campaign have the expected number of
# ads. Builder input is an integer. Unlike per-ad checks, an empty ads list is
# a real Pass/Fix data point (e.g. "0 ads expected, 0 found = Pass"), not Review.


def check_ad_count(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    expected = _parse_int(row.builder_input)
    if expected is None or expected < 0:
        return _review(
            row,
            f'Could not interpret the expected ad count "{row.builder_input}" as a non-negative integer.',
        )

    actual = len(_ads(evidence))
    if actual == expected:
        return _pass(row)
    return _fix(
        row,
        f"Expected {expected} ad(s), but found {actual} in BigQuery.",
    )


# --- ad_destination_url ----------------------------------------------------
#
# Builders specify the destination URL the ads should drive to. Read defensively
# from several common field paths because the BQ schema varies per client.
# Strict comparison with light normalization (lowercase scheme/host, strip
# trailing slash). UTMs / query params ARE significant — a mismatching UTM is
# something the builder will want flagged. False Fix is recoverable; false Pass
# is the Peacock-Olympics class.

# Common locations the destination URL can show up in BQ. Order matters: first
# non-blank wins per ad.
_AD_URL_FIELDS = (
    "link_url",
    "destination_url",
    "creative.link_url",
    "creative.object_story_spec.link_data.link",
    "object_story_spec.link_data.link",
)


def _read_path(record: Any, dotted: str) -> str:
    """Read a (possibly nested) string value off `record`; "" if missing."""
    if not isinstance(record, dict):
        return ""
    current: Any = record
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
        if current is None:
            return ""
    return str(current).strip()


def _read_ad_url(ad: dict[str, Any]) -> str:
    """Return the first non-blank destination URL found on an ad."""
    for path in _AD_URL_FIELDS:
        value = _read_path(ad, path)
        if value:
            return value
    return ""


def _normalize_url(value: Any) -> str:
    """Light, deterministic normalization for URL comparison.

    Lowercases scheme + host; strips trailing slash on the path; preserves query
    string and fragment exactly. Returns "" when the input doesn't look like a
    URL — internal whitespace, no dot in the host, missing scheme/host. Callers
    treat "" as "uninterpretable → Review", never a false Fix.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Any internal whitespace means it wasn't a URL (e.g. "next tuesday",
    # "::: not a url :::"). Be strict — false Fix is worse than false Review.
    if any(c.isspace() for c in text):
        return ""
    # Default to https when scheme is omitted (builders often paste bare hosts).
    if "://" not in text:
        text = "https://" + text

    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    # Real ad destination hosts always have at least one dot (example.com,
    # subdomain.example.co.uk, etc.). Excludes "localhost" and garbage netlocs
    # that urlsplit accepts permissively.
    if "." not in parts.netloc:
        return ""

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


def _is_bare_domain(normalized_url: str) -> bool:
    """True when a normalized URL is just a domain — no path, query, or fragment.
    Signals the builder is asserting the destination DOMAIN, not an exact link
    (e.g. "peacocktv.com"), so the check compares hosts instead of full URLs.
    Peacock's per-creative tracking URLs are all unique but share one domain, so
    full-URL comparison never matches while a domain check is meaningful + passable."""
    if not normalized_url:
        return False
    from urllib.parse import urlsplit

    parts = urlsplit(normalized_url)
    return not parts.query and not parts.fragment and parts.path in ("", "/")


def _url_host(normalized_url: str) -> str:
    """Comparable host of a normalized URL, with a leading 'www.' dropped so
    'www.peacocktv.com' and 'peacocktv.com' match. '' if unparseable."""
    if not normalized_url:
        return ""
    from urllib.parse import urlsplit

    host = urlsplit(normalized_url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def check_ad_destination_url(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ads = _ads(evidence)
    if not ads:
        return _review(row, "No ads found in BigQuery for this campaign.")

    expected_norm = _normalize_url(row.builder_input)
    if not expected_norm:
        return _review(
            row,
            f'Could not parse the expected destination URL "{row.builder_input}".',
        )

    # If the builder entered a bare domain ("peacocktv.com"), compare by host;
    # if they entered a full URL, compare exactly. Inferred from the input shape.
    domain_mode = _is_bare_domain(expected_norm)
    expected_host = _url_host(expected_norm)

    mismatched: list[tuple[str, str]] = []
    missing: list[str] = []

    for ad in ads:
        raw = _read_ad_url(ad)
        if not raw:
            missing.append(_ad_label(ad))
            continue
        actual_norm = _normalize_url(raw)
        if not actual_norm:
            # Couldn't normalize an actual URL — treat as missing so we Review
            # rather than emit a false Fix on a parser quirk.
            missing.append(_ad_label(ad))
            continue
        if domain_mode:
            if _url_host(actual_norm) != expected_host:
                mismatched.append((_ad_label(ad), raw))
        elif actual_norm != expected_norm:
            mismatched.append((_ad_label(ad), raw))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        target = f"domain {expected_host}" if domain_mode else f'"{row.builder_input}"'
        return _fix(
            row,
            f'Expected {target}, but {first_label} points to "{first_actual}"{more}',
        )
    if missing:
        if len(missing) == len(ads):
            return _review(
                row,
                "Destination URL not available in BigQuery for any ad; verify manually.",
            )
        return _pass(row, f"({len(missing)} of {len(ads)} ads missing destination URL)")
    if domain_mode:
        return _pass(row, f"All ads point to {expected_host}.")
    return _pass(row)


# --- ad_call_to_action -----------------------------------------------------
#
# Builders pick a CTA from the MASTER DATA VALIDATION dropdown (18 values);
# Meta stores creative.call_to_action_type as an enum. Most friendly labels are
# just the enum upper-snake-cased ("Learn More" -> LEARN_MORE), but a few differ
# ("Send Message" -> MESSAGE_PAGE, "Send WhatsApp Message" -> WHATSAPP_MESSAGE).
# The map below is keyed by the exact dropdown labels (normalized) so we match
# what builders actually select; unknown input -> Review, never a false Fix.

# Meta call_to_action_type enums we recognize as actual values.
_KNOWN_CTA_TYPES = {
    "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "CONTACT_US", "DOWNLOAD", "BOOK_NOW",
    "GET_QUOTE", "GET_OFFER", "CALL_NOW", "MESSAGE_PAGE", "WHATSAPP_MESSAGE",
    "ORDER_NOW", "SUBSCRIBE", "APPLY_NOW", "WATCH_MORE", "USE_APP",
    "BUY_TICKETS", "GET_DIRECTIONS",
}

# Dropdown label (normalized: lowercase, underscores->spaces) -> Meta enum.
_CTA_SYNONYMS = {
    "learn more": "LEARN_MORE",
    "shop now": "SHOP_NOW",
    "sign up": "SIGN_UP",
    "contact us": "CONTACT_US",
    "download": "DOWNLOAD",
    "book now": "BOOK_NOW",
    "get quote": "GET_QUOTE",
    "get offer": "GET_OFFER",
    "call now": "CALL_NOW",
    "send message": "MESSAGE_PAGE",
    "send whatsapp message": "WHATSAPP_MESSAGE",
    "order now": "ORDER_NOW",
    "subscribe": "SUBSCRIBE",
    "apply now": "APPLY_NOW",
    "watch more": "WATCH_MORE",
    "use app": "USE_APP",
    "buy tickets": "BUY_TICKETS",
    "get directions": "GET_DIRECTIONS",
}

# CTA can sit at a few places on the ad/creative record depending on client schema.
_AD_CTA_FIELDS = (
    "call_to_action_type",
    "creative.call_to_action_type",
    "creative.object_story_spec.link_data.call_to_action.type",
    "object_story_spec.link_data.call_to_action.type",
)


def _canonical_cta(value: Any) -> str:
    """Map a builder label or stored value to a Meta CTA enum, or '' if unknown.

    Meta enums (MESSAGE_PAGE, etc.) treated as equivalent to their dropdown
    label so an actual value matches the builder's selection. Note: "send
    message" maps to MESSAGE_PAGE, but a stored SEND_MESSAGE is also accepted.
    """
    norm = _norm(value)
    if not norm:
        return ""
    # Synonym map first so dropdown labels with non-obvious enums resolve
    # correctly ("send message" -> MESSAGE_PAGE) before the upper-snake-case
    # fallback. A stored enum like "SEND_MESSAGE" also normalizes to
    # "send message" and routes through the synonym → same canonical value.
    if norm in _CTA_SYNONYMS:
        return _CTA_SYNONYMS[norm]
    upper = norm.upper().replace(" ", "_")
    if upper in _KNOWN_CTA_TYPES:
        return upper
    return ""


def _read_ad_cta(ad: dict[str, Any]) -> str:
    for path in _AD_CTA_FIELDS:
        value = _read_path(ad, path)
        if value:
            return value
    return ""


def check_ad_call_to_action(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ads = _ads(evidence)
    if not ads:
        return _review(row, "No ads found in BigQuery for this campaign.")

    expected = _canonical_cta(row.builder_input)
    if not expected:
        return _review(
            row,
            f'Could not interpret the expected call to action "{row.builder_input}". '
            "Use a value from the CTA dropdown (e.g. Learn More, Shop Now).",
        )

    mismatched: list[tuple[str, str]] = []
    unparseable: list[tuple[str, str]] = []
    missing: list[str] = []

    for ad in ads:
        raw = _read_ad_cta(ad)
        if not raw:
            missing.append(_ad_label(ad))
            continue
        actual = _canonical_cta(raw)
        if not actual:
            unparseable.append((_ad_label(ad), str(raw)))
            continue
        if actual != expected:
            mismatched.append((_ad_label(ad), str(raw)))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f'Expected "{row.builder_input}", but {first_label} uses "{first_actual}"{more}',
        )
    if unparseable:
        label, raw = unparseable[0]
        return _review(
            row, f'Ad CTA "{raw}" on {label} not recognized. Verify manually.'
        )
    if missing:
        if len(missing) == len(ads):
            return _review(
                row,
                "Call to action not available in BigQuery for any ad; verify manually.",
            )
        return _pass(row, f"({len(missing)} of {len(ads)} ads missing a CTA)")
    return _pass(row)


# --- ad_creative_dimensions (Peacock: deterministic via Frame_Size) --------
#
# Standard clients: creative dimensions aren't reliably in BigQuery, so this is
# a manual Review (the pipeline routes non-Peacock runs to
# ALWAYS_REVIEW_CHECK_ACTIONS before reaching this function). In PEACOCK mode the
# trafficking table (Phase B) carries a clean Frame_Size per creative
# ("1080x1920", "1080x1080", …), so we can verify the expected sizes are present.
#
# Comparison is by ASPECT RATIO, so a builder may type pixels ("1080x1920") OR a
# ratio ("9x16" / "9:16") and still match the trafficked pixel size. Conservative
# per the cardinal rule (never a false Fix/Pass):
#   - any unparseable token on EITHER side  -> Review (with the actual sizes shown)
#   - an expected size confidently absent    -> Fix, BUT only when every ad's size
#                                               was seen (an unsynced ad could be
#                                               the missing size) — else Review
#   - exact set match                        -> Pass
# The trafficked sizes are always echoed in the action so a human sees them even
# on Review — already a win over the old blanket "verify in Ads Manager" note.

# Accepts WxH or W:H with common separators (x, ×, :, "by", *).
_FRAME_SEP_RE = re.compile(r"\s*(?:x|×|:|by|\*)\s*", re.IGNORECASE)

# Where a creative's frame size can sit on the ad record (Peacock attaches the
# trafficked spec under `trafficking`).
_AD_FRAME_FIELDS = (
    "trafficking.frame_size",
    "frame_size",
    "creative.frame_size",
)


def _canonical_aspect_ratio(value: Any) -> str:
    """Reduce a 'WxH' or 'W:H' dimension to a canonical 'W:H' aspect ratio
    ("1080x1920" -> "9:16", "1x1" -> "1:1"). Returns "" when it can't be read as
    two positive integers (caller → Review, never a guessed Fix)."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    parts = _FRAME_SEP_RE.split(text)
    if len(parts) != 2:
        return ""
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        return ""
    if width <= 0 or height <= 0:
        return ""
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _read_ad_frame_size(ad: dict[str, Any]) -> str:
    for path in _AD_FRAME_FIELDS:
        value = _read_path(ad, path)
        if value:
            return value
    return ""


def check_ad_creative_dimensions(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    # Only Peacock carries frame data in BigQuery; for other accounts this is a
    # manual check. (The pipeline normally short-circuits non-Peacock runs to the
    # manual-Review note; this guard makes the function safe if called directly.)
    if not _peacock_mode(evidence):
        return _review(
            row,
            "Creative dimensions are a manual check for this account; verify in Ads Manager.",
        )

    ads = _ads(evidence)
    if not ads:
        return _review(row, "No ads found in BigQuery for this campaign.")

    # Parse builder expectation into canonical aspect ratios; any unparseable
    # token → Review (don't risk a Fix on a format we didn't understand).
    expected: set[str] = set()
    for token in [t.strip() for t in str(row.builder_input).split(",") if t.strip()]:
        ratio = _canonical_aspect_ratio(token)
        if not ratio:
            return _review(
                row,
                f'Could not interpret the expected dimension "{token}". '
                "Use pixels (1080x1920) or a ratio (9:16).",
            )
        expected.add(ratio)
    if not expected:
        return _review(row, "Expected creative dimensions are blank; verify manually.")

    actual_ratios: set[str] = set()
    actual_raw: list[str] = []
    unparseable: list[str] = []
    missing = 0
    for ad in ads:
        raw = _read_ad_frame_size(ad)
        if not raw:
            missing += 1
            continue
        actual_raw.append(raw)
        ratio = _canonical_aspect_ratio(raw)
        if not ratio:
            unparseable.append(raw)
            continue
        actual_ratios.add(ratio)

    if not actual_raw:
        return _review(
            row,
            "Creative dimensions (Frame_Size) not available in the trafficking data "
            "for any ad; verify manually.",
        )
    sizes_str = ", ".join(sorted(set(actual_raw)))
    if unparseable:
        return _review(
            row,
            f'Could not parse the trafficked frame size "{unparseable[0]}". '
            f"Trafficked sizes: {sizes_str}. Verify manually.",
        )

    missing_expected = expected - actual_ratios
    extra = actual_ratios - expected

    if missing_expected:
        # Confident Fix only if we saw EVERY ad's size; an unsynced ad could be
        # the very size we think is missing → Review instead (never a false Fix).
        if missing:
            return _review(
                row,
                f"Expected {sorted(expected)}; trafficked sizes seen: {sizes_str}, but "
                f"{missing} ad(s) had no frame size — can't confirm {sorted(missing_expected)} "
                "is absent. Verify manually.",
            )
        return _fix(
            row,
            f"Expected {sorted(expected)}, but no creative has {sorted(missing_expected)}. "
            f"Trafficked sizes: {sizes_str}.",
        )
    if extra:
        return _review(
            row,
            f"Expected {sorted(expected)}; trafficked sizes also include {sorted(extra)} "
            f"({sizes_str}). Confirm the extra size is intended.",
        )
    return _pass(row, f"Trafficked sizes match {sorted(expected)}: {sizes_str}.")


# --- ad_flight_window (Peacock QC surface) ---------------------------------
#
# Peacock's trafficking table pre-computes a human-readable flight-window QC flag
# (`Live_After_End_Date_Warning`: "🚦 All Clear: Live within Flight Window 🚦" vs
# "‼️ Caution: Approaching End Date ‼️"). This surfaces it directly: all-clear ->
# Pass, any caution/warning -> Review (never auto-Fix — it's an advisory the
# builder should eyeball). No builder input needed, so it's ALWAYS_RUN. Peacock-
# only; standard clients have no such flag -> Review.

_FLIGHT_CLEAR_MARKERS = ("all clear", "within flight", "live within")


def check_ad_flight_window(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    if not _peacock_mode(evidence):
        return _review(
            row, "Flight-window QC is a Peacock-only check; verify manually."
        )
    ads = _ads(evidence)
    flags: list[str] = []
    for ad in ads:
        traf = ad.get("trafficking")
        flag = traf.get("flight_window_flag") if isinstance(traf, dict) else None
        if not _is_blank(flag):
            flags.append(str(flag))

    if not flags:
        return _review(
            row,
            "No trafficking flight-window flag available for this campaign; verify manually.",
        )

    not_clear = sorted(
        {f for f in flags if not any(m in f.lower() for m in _FLIGHT_CLEAR_MARKERS)}
    )
    if not_clear:
        more = f" (+{len(not_clear) - 1} more)" if len(not_clear) > 1 else ""
        return _review(
            row,
            f"Trafficking flagged: {not_clear[0]}{more}. Confirm the flight window.",
        )
    return _pass(row, "All creatives are flagged live within their flight window.")


# === Ad-set level: conversion event (the Peacock-Olympics check) =============
#
# promoted_object.custom_event_type — the optimization/conversion event the ad
# set is configured for. This is THE check the bot exists for: the Feb 2026
# Peacock-Olympics incident launched with "purchase event" instead of
# "purchase" and survived 2-3 rounds of human QA because reviewers glossed over
# the near-match. The bot must do the opposite of that:
#
#   CARDINAL RULE: never Pass unless we are confident expected == actual.
#   Any ambiguity (a non-standard / unmappable value on either side) → Review,
#   never a silent Pass. A confident mismatch between two recognized standard
#   events → Fix.
#
# Standard Meta custom_event_type enums. Most friendly labels normalize straight
# to these (upper-snake), so the synonym map stays small + high-confidence on
# purpose — anything we can't confidently map escalates to Review.
_KNOWN_EVENT_TYPES = {
    "PURCHASE", "LEAD", "COMPLETE_REGISTRATION", "ADD_TO_CART",
    "ADD_TO_WISHLIST", "ADD_PAYMENT_INFO", "INITIATED_CHECKOUT", "SEARCH",
    "VIEW_CONTENT", "CONTACT", "SUBSCRIBE", "START_TRIAL", "SUBMIT_APPLICATION",
    "DONATE", "SCHEDULE", "FIND_LOCATION", "CUSTOMIZE_PRODUCT", "OTHER",
}

# Only genuine, unambiguous aliases. Deliberately conservative: a wrong synonym
# would cause a false Pass, which is exactly the failure this check guards.
_EVENT_SYNONYMS = {
    "purchases": "PURCHASE",
    "leads": "LEAD",
    "registration": "COMPLETE_REGISTRATION",
    "registrations": "COMPLETE_REGISTRATION",
    "complete registrations": "COMPLETE_REGISTRATION",
    "checkout": "INITIATED_CHECKOUT",
    "initiate checkout": "INITIATED_CHECKOUT",
    "begin checkout": "INITIATED_CHECKOUT",
    "payment info": "ADD_PAYMENT_INFO",
    "wishlist": "ADD_TO_WISHLIST",
}

_ADSET_EVENT_FIELDS = (
    "promoted_object.custom_event_type",
    "custom_event_type",
)


def _canonical_event(value: Any) -> str:
    """Map a builder label or stored value to a standard custom_event_type enum,
    or "" if it is not a recognized standard event (i.e. a custom event or
    garbage). "" is the signal to the caller to escalate to Review rather than
    risk a false Pass."""
    norm = _norm(value)
    if not norm:
        return ""
    upper = norm.upper().replace(" ", "_")
    if upper in _KNOWN_EVENT_TYPES:
        return upper
    return _EVENT_SYNONYMS.get(norm, "")


def _read_adset_event(adset: dict[str, Any]) -> str:
    for path in _ADSET_EVENT_FIELDS:
        value = _read_path(adset, path)
        if value:
            return value
    return ""


def check_adset_conversion_event(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected_enum = _canonical_event(row.builder_input)
    expected_norm = _norm(row.builder_input)
    if not expected_norm:
        return _review(row, "Expected conversion event is blank; verify manually.")

    mismatched: list[tuple[str, str]] = []   # confident Fix (both standard, differ)
    review_ads: list[tuple[str, str]] = []    # ambiguity → escalate, never Pass
    missing: list[str] = []

    for adset in ad_sets:
        label = _adset_label(adset)
        raw = _read_adset_event(adset)
        if _is_blank(raw):
            missing.append(label)
            continue
        actual_enum = _canonical_event(raw)
        actual_norm = _norm(raw)

        if expected_enum and actual_enum:
            # Both recognized standard events — confident comparison.
            if expected_enum != actual_enum:
                mismatched.append((label, str(raw)))
        elif expected_enum and not actual_enum:
            # Builder expected a standard event but the ad set's value isn't one
            # (e.g. "purchase event"). This is the Peacock case — escalate.
            review_ads.append(
                (label, f'actual event "{raw}" is not a recognized standard event')
            )
        elif not expected_enum and actual_enum:
            review_ads.append(
                (label, f'expected "{row.builder_input}" is not a recognized standard event')
            )
        else:
            # Neither maps to a standard enum: both custom. Only Pass on an exact
            # (normalized) string match; otherwise escalate — never guess.
            if expected_norm != actual_norm:
                review_ads.append(
                    (label, f'custom event "{raw}" vs expected "{row.builder_input}"')
                )

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f'Expected conversion event "{row.builder_input}", but {first_label} '
            f'is set to "{first_actual}"{more}',
        )
    if review_ads:
        label, reason = review_ads[0]
        more = f" (+{len(review_ads) - 1} more)" if len(review_ads) > 1 else ""
        return _review(row, f"{label}: {reason}{more}. Verify manually.")
    if missing:
        if len(missing) == len(ad_sets):
            return _review(
                row,
                "Conversion event not available in BigQuery for any ad set; verify manually."
                + _known_context(evidence),
            )
        return _pass(row, f"({len(missing)} of {len(ad_sets)} ad sets missing a conversion event)")
    return _pass(row)


# === Ad-set level: attribution setting + optimization goal ==================
#
# Both confirmed against live BigQuery (C61854560, 2026-05-29):
#   attribution_spec  = [{'event_type': 'CLICK_THROUGH'|'VIEW_THROUGH',
#                         'window_days': N}, ...]  (empty [] when not a
#                        conversion ad set)
#   optimization_goal = string enum ('CLICKS', 'OFFSITE_CONVERSIONS', ...)


# --- adset_attribution_setting ---------------------------------------------
#
# Builder dropdown values (MASTER DATA VALIDATION tab): "1-day click",
# "7-day click", "1-day click, 1-day view", "7-day click, 1-day view".
# We normalize both sides to a frozenset of (channel, days) and compare.

_ATTR_EVENT_MAP = {"CLICK_THROUGH": "click", "VIEW_THROUGH": "view"}
_ATTR_TOKEN_RE = re.compile(r"(\d+)\s*-?\s*day\s+(click|view)")


def _format_attribution(spec: frozenset[tuple[str, int]]) -> str:
    order = {"click": 0, "view": 1}
    parts = sorted(spec, key=lambda cv: (order.get(cv[0], 9), cv[1]))
    return ", ".join(f"{days}-day {channel}" for channel, days in parts)


def _parse_attribution_input(value: Any) -> frozenset[tuple[str, int]] | None:
    """Parse a friendly attribution string ("7-day click, 1-day view") into a
    set of (channel, days). None if nothing parseable."""
    text = _norm(value)
    if not text:
        return None
    found = _ATTR_TOKEN_RE.findall(text)
    if not found:
        return None
    return frozenset((channel, int(days)) for days, channel in found)


# Sentinel so "column absent" is distinct from "empty attribution_spec list".
_MISSING = object()


def _parse_attribution_actual(value: Any) -> frozenset[tuple[str, int]] | None:
    """Parse the stored attribution_spec into a set of (channel, days).
    Returns None if the value can't be confidently interpreted (→ Review).
    An empty list parses to an empty frozenset (no attribution window set)."""
    if isinstance(value, (list, tuple)):
        result: set[tuple[str, int]] = set()
        for item in value:
            if not isinstance(item, dict):
                return None
            channel = _ATTR_EVENT_MAP.get(str(item.get("event_type", "")).upper())
            window = item.get("window_days")
            if channel is None or window is None:
                return None
            try:
                result.add((channel, int(window)))
            except (TypeError, ValueError):
                return None
        return frozenset(result)
    if isinstance(value, str):
        return _parse_attribution_input(value)
    return None


def check_adset_attribution_setting(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _parse_attribution_input(row.builder_input)
    if not expected:
        return _review(
            row,
            f'Could not interpret the expected attribution "{row.builder_input}". '
            'Use e.g. "7-day click" or "7-day click, 1-day view".',
        )

    mismatched: list[tuple[str, str]] = []
    review_ads: list[tuple[str, str]] = []
    missing: list[str] = []

    for adset in ad_sets:
        label = _adset_label(adset)
        raw = adset.get("attribution_spec", _MISSING)
        if raw is _MISSING or raw is None:
            missing.append(label)
            continue
        actual = _parse_attribution_actual(raw)
        if actual is None:
            review_ads.append((label, f'attribution value "{raw}" could not be parsed'))
            continue
        if not actual:
            # Empty spec — ad set has no attribution window (likely not a
            # conversion ad set). Escalate rather than Fix.
            review_ads.append(
                (label, "no attribution window set (may not be a conversion ad set)")
            )
            continue
        if actual != expected:
            mismatched.append((label, _format_attribution(actual)))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f'Expected attribution "{row.builder_input}", but {first_label} is '
            f'"{first_actual}"{more}',
        )
    if review_ads:
        label, reason = review_ads[0]
        more = f" (+{len(review_ads) - 1} more)" if len(review_ads) > 1 else ""
        return _review(row, f"{label}: {reason}{more}. Verify manually.")
    if missing:
        if len(missing) == len(ad_sets):
            return _review(
                row,
                "Attribution setting not available in BigQuery for any ad set; verify manually."
                + _known_context(evidence),
            )
        return _pass(row, f"({len(missing)} of {len(ad_sets)} ad sets missing attribution)")
    return _pass(row)


# --- adset_optimization_goal -----------------------------------------------
#
# optimization_goal is a clean string enum in BQ. No dropdown vocabulary in the
# validation tab yet, so the synonym map is conservative (exact enum + common
# Meta UI labels); anything unmapped → Review, never a guessed Pass/Fix.
# Peacock-adjacent (the incident was an optimization-event mistake).

_KNOWN_OPT_GOALS = {
    "OFFSITE_CONVERSIONS", "ONSITE_CONVERSIONS", "LINK_CLICKS", "CLICKS",
    "LANDING_PAGE_VIEWS", "IMPRESSIONS", "REACH", "THRUPLAY", "VALUE",
    "APP_INSTALLS", "LEAD_GENERATION", "QUALITY_LEAD", "QUALITY_CALL",
    "CONVERSATIONS", "POST_ENGAGEMENT", "PAGE_LIKES", "EVENT_RESPONSES",
    "TWO_SECOND_CONTINUOUS_VIDEO_VIEWS", "AD_RECALL_LIFT", "VISIT_INSTAGRAM_PROFILE",
}

_OPT_GOAL_SYNONYMS = {
    "conversions": "OFFSITE_CONVERSIONS",
    "offsite conversions": "OFFSITE_CONVERSIONS",
    "link clicks": "LINK_CLICKS",
    "landing page views": "LANDING_PAGE_VIEWS",
    "impressions": "IMPRESSIONS",
    "reach": "REACH",
    "thruplay": "THRUPLAY",
    "value": "VALUE",
    "leads": "LEAD_GENERATION",
    "lead generation": "LEAD_GENERATION",
}


def _canonical_opt_goal(value: Any) -> str:
    norm = _norm(value)
    if not norm:
        return ""
    upper = norm.upper().replace(" ", "_")
    if upper in _KNOWN_OPT_GOALS:
        return upper
    return _OPT_GOAL_SYNONYMS.get(norm, "")


def check_adset_optimization_goal(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _canonical_opt_goal(row.builder_input)
    if not expected:
        return _review(
            row,
            f'Could not interpret the expected optimization goal "{row.builder_input}". '
            "Verify manually.",
        )

    mismatched: list[tuple[str, str]] = []
    review_ads: list[tuple[str, str]] = []
    missing: list[str] = []

    for adset in ad_sets:
        label = _adset_label(adset)
        raw = adset.get("optimization_goal")
        if _is_blank(raw):
            missing.append(label)
            continue
        actual = _canonical_opt_goal(raw)
        if not actual:
            review_ads.append((label, f'optimization goal "{raw}" not recognized'))
            continue
        if actual != expected:
            mismatched.append((label, str(raw)))

    if mismatched:
        first_label, first_actual = mismatched[0]
        more = f" (+{len(mismatched) - 1} more)" if len(mismatched) > 1 else ""
        return _fix(
            row,
            f'Expected optimization goal "{row.builder_input}", but {first_label} '
            f'is "{first_actual}"{more}',
        )
    if review_ads:
        label, reason = review_ads[0]
        more = f" (+{len(review_ads) - 1} more)" if len(review_ads) > 1 else ""
        return _review(row, f"{label}: {reason}{more}. Verify manually.")
    if missing:
        if len(missing) == len(ad_sets):
            return _review(
                row,
                "Optimization goal not available in BigQuery for any ad set; verify manually."
                + _known_context(evidence),
            )
        return _pass(row, f"({len(missing)} of {len(ad_sets)} ad sets missing optimization goal)")
    return _pass(row)


# === Ad-set level: bidirectional presence checks (Brandon calibration 2026-06-01) ==
#
# Spend Minimum / Spend Maximum / Interests-or-Custom-Audiences / Audience
# Exclusions are "Yes/No" rows. Brandon's rule: builder "Yes" => the setting
# must be PRESENT (Fix if absent); but EVEN WHEN the builder doesn't say Yes,
# still check that it isn't there — to catch settings "accidentally included."
# So these are bidirectional AND always-run (registered in ALWAYS_RUN_CHECK_IDS
# so a blank builder input doesn't skip them). Unexpected-present => Review (it
# may be intentional — escalate, don't false-Fix); expected-but-absent => Fix.
# (Location is NOT here — per Brandon it's a value-match vs a builder-provided
# location, which is `adset_countries`.)

_AFFIRMATIVE = {"yes", "y", "true", "required", "needed", "applicable", "present"}
_NEGATIVE = {"no", "n", "false", "na", "none", "not applicable", "n a", ""}


def _expected_presence(value: Any) -> bool | None:
    """True if the builder marked the setting expected (Yes), False if not
    (No/blank/N/A), None if the input is uninterpretable (→ Review)."""
    norm = _norm(value)
    if norm in _AFFIRMATIVE:
        return True
    if norm in _NEGATIVE:
        return False
    return None


def _adset_presence_check(
    row: CheckRow,
    *,
    evidence: dict[str, Any] | None,
    present_fn,
    label: str,
    verifiable_fn=None,
) -> CheckResult:
    """Bidirectional presence check over a campaign's ad sets.

    present_fn(adset)    -> is the setting present (a positive amount, a non-empty
                            list, …)?
    verifiable_fn(adset) -> is the underlying field even SYNCED to BigQuery for
                            this ad set (regardless of value)? Defaults to "always
                            verifiable". When a field isn't synced (e.g. some
                            clients don't have `custom_audiences`), present_fn
                            would read absent-as-"none" and a builder "Yes" would
                            FALSELY Fix. So an unverifiable field → Review ("can't
                            confirm"), never Fix (Peacock rule: never a wrong flag).
    """
    ad_sets = _ad_sets(evidence)
    if not ad_sets:
        return _review(row, "No ad sets found in BigQuery for this campaign.")

    expected = _expected_presence(row.builder_input)
    if expected is None:
        return _review(
            row,
            f'Could not interpret "{row.builder_input}" for {label}; expected Yes/No. '
            "Verify manually.",
        )

    verify = verifiable_fn or (lambda _adset: True)
    missing_when_expected: list[str] = []   # builder said Yes, verifiably absent → Fix
    unverifiable: list[str] = []            # field not synced to BQ → can't confirm → Review
    present_when_not: list[str] = []        # not expected, but present → Review

    for adset in ad_sets:
        if not verify(adset):
            unverifiable.append(_adset_label(adset))
            continue
        actual_present = bool(present_fn(adset))
        if expected and not actual_present:
            missing_when_expected.append(_adset_label(adset))
        elif not expected and actual_present:
            present_when_not.append(_adset_label(adset))

    # A verifiable, genuine miss is a real Fix and outranks everything.
    if missing_when_expected:
        first = missing_when_expected[0]
        more = f" (+{len(missing_when_expected) - 1} more)" if len(missing_when_expected) > 1 else ""
        return _fix(
            row,
            f"Builder expected {label}, but {first} has none{more}.",
        )
    # Field isn't in BigQuery → we can't confirm either way → Review, never Fix.
    if unverifiable:
        first = unverifiable[0]
        more = f" (+{len(unverifiable) - 1} more)" if len(unverifiable) > 1 else ""
        return _review(
            row,
            f"{label} isn't available in BigQuery for {first}{more}; verify manually.",
        )
    if present_when_not:
        first = present_when_not[0]
        more = f" (+{len(present_when_not) - 1} more)" if len(present_when_not) > 1 else ""
        return _review(
            row,
            f"{first} has {label} set but it wasn't marked expected{more} — "
            "confirm it isn't accidentally included.",
        )
    return _pass(row)


def _budget_present(adset: dict[str, Any], field: str) -> bool:
    """A spend min/max is 'present' when the field holds a positive amount."""
    val = _parse_int(adset.get(field))
    return val is not None and val > 0


def _budget_synced(adset: dict[str, Any], field: str) -> bool:
    """Is the budget column synced for this ad set (present, even if 0)? A missing
    column reads as None → we can't tell 'no minimum' from 'not synced'."""
    return adset.get(field) is not None


def _targeting_list_present(adset: dict[str, Any], field: str) -> bool:
    value = read_targeting(adset).get(field)
    return isinstance(value, (list, tuple)) and len(value) > 0


def _targeting_synced(adset: dict[str, Any], field: str) -> bool:
    """Is the targeting field synced at all (key present, even if empty list)?
    Some clients don't sync `custom_audiences` — absent ≠ 'no audiences'."""
    return field in read_targeting(adset)


def check_adset_spend_minimum(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _adset_presence_check(
        row, evidence=evidence,
        present_fn=lambda a: _budget_present(a, "daily_min_spend_target"),
        verifiable_fn=lambda a: _budget_synced(a, "daily_min_spend_target"),
        label="a spend minimum",
    )


def check_adset_spend_maximum(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _adset_presence_check(
        row, evidence=evidence,
        present_fn=lambda a: _budget_present(a, "daily_spend_cap"),
        verifiable_fn=lambda a: _budget_synced(a, "daily_spend_cap"),
        label="a spend maximum",
    )


def check_adset_audiences(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _adset_presence_check(
        row, evidence=evidence,
        present_fn=lambda a: _targeting_list_present(a, "custom_audiences"),
        verifiable_fn=lambda a: _targeting_synced(a, "custom_audiences"),
        label="interests/custom audiences",
    )


def check_adset_audience_exclusions(row: CheckRow, *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return _adset_presence_check(
        row, evidence=evidence,
        present_fn=lambda a: _targeting_list_present(a, "excluded_custom_audiences"),
        verifiable_fn=lambda a: _targeting_synced(a, "excluded_custom_audiences"),
        label="audience exclusions",
    )
