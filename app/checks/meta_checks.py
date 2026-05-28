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
    # Already a 2-letter code.
    if len(text) == 2 and text.isalpha():
        return text.upper()
    # Try the friendly-name map (underscores/casing already normalized by _norm).
    return _COUNTRY_NAME_TO_CODE.get(_norm(text), "")


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
