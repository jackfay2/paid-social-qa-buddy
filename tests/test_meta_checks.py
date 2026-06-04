"""Unit tests for Meta deterministic check functions."""

from __future__ import annotations

from datetime import UTC

from app.checks.meta_checks import (
    check_ad_call_to_action,
    check_ad_count,
    check_ad_creative_dimensions,
    check_ad_destination_url,
    check_ad_flight_window,
    check_ad_status,
    check_adset_age_max,
    check_adset_age_min,
    check_adset_attribution_setting,
    check_adset_audience_exclusions,
    check_adset_audiences,
    check_adset_conversion_event,
    check_adset_countries,
    check_adset_end_date,
    check_adset_genders,
    check_adset_optimization_goal,
    check_adset_placements,
    check_adset_spend_maximum,
    check_adset_spend_minimum,
    check_adset_start_date,
    check_adset_status,
    check_campaign_bid_strategy,
    check_campaign_budget,
    check_campaign_buying_type,
    check_campaign_objective,
    check_campaign_start_date,
    check_campaign_status,
)
from app.checks.registry import CHECK_REGISTRY, run_check
from app.models import CheckRow


def _row(check_id: str, builder_input: str) -> CheckRow:
    return CheckRow(row_index=2, check_id=check_id, builder_input=builder_input)


def _evidence(campaign: dict) -> dict:
    return {"campaign": campaign, "ad_sets": [], "ads": []}


def _adset_evidence(ad_sets: list[dict]) -> dict:
    """Evidence shaped for ad-set-level checks. Campaign field is irrelevant."""
    return {"campaign": {}, "ad_sets": ad_sets, "ads": []}


def _ad_evidence(ads: list[dict]) -> dict:
    """Evidence shaped for ad-level checks. Campaign + ad_sets are irrelevant."""
    return {"campaign": {}, "ad_sets": [], "ads": ads}


# --- campaign_objective ----------------------------------------------------


def test_objective_exact_enum_match_passes() -> None:
    row = _row("campaign_objective", "OUTCOME_TRAFFIC")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "OUTCOME_TRAFFIC"}))
    assert result.verdict == "Pass"


def test_objective_friendly_synonym_passes() -> None:
    """Builder types 'Traffic', Meta stores 'OUTCOME_TRAFFIC' — must Pass, not Fix."""
    row = _row("campaign_objective", "Traffic")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "OUTCOME_TRAFFIC"}))
    assert result.verdict == "Pass"


def test_objective_real_mismatch_is_fix() -> None:
    row = _row("campaign_objective", "Sales")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "OUTCOME_TRAFFIC"}))
    assert result.verdict == "Fix"
    assert "Sales" in result.action
    assert "OUTCOME_TRAFFIC" in result.action


def test_objective_missing_field_is_review() -> None:
    row = _row("campaign_objective", "Traffic")
    result = check_campaign_objective(row, evidence=_evidence({}))
    assert result.verdict == "Review"
    assert "not available" in result.action.lower()


def test_objective_uninterpretable_expected_is_review() -> None:
    """Unknown builder input -> Review, never a false Fix."""
    row = _row("campaign_objective", "make me go viral")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "OUTCOME_TRAFFIC"}))
    assert result.verdict == "Review"


def test_objective_unrecognized_actual_is_review() -> None:
    row = _row("campaign_objective", "Traffic")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "SOME_LEGACY_OBJECTIVE"}))
    assert result.verdict == "Review"


# --- campaign_objective: legacy enum migration (Brandon calibration 2026-05-28) -----


def test_objective_legacy_conversions_passes_for_sales() -> None:
    """Meta migrated CONVERSIONS -> OUTCOME_SALES; bot treats them as equivalent."""
    row = _row("campaign_objective", "Sales")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "CONVERSIONS"}))
    assert result.verdict == "Pass"


def test_objective_legacy_lead_generation_passes_for_leads() -> None:
    row = _row("campaign_objective", "Leads")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "LEAD_GENERATION"}))
    assert result.verdict == "Pass"


def test_objective_legacy_page_likes_passes_for_engagement() -> None:
    row = _row("campaign_objective", "Engagement")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "PAGE_LIKES"}))
    assert result.verdict == "Pass"


def test_objective_legacy_link_clicks_passes_for_traffic() -> None:
    row = _row("campaign_objective", "Traffic")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "LINK_CLICKS"}))
    assert result.verdict == "Pass"


def test_objective_legacy_app_installs_passes_for_app_promotion() -> None:
    row = _row("campaign_objective", "App Promotion")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "APP_INSTALLS"}))
    assert result.verdict == "Pass"


# --- campaign_buying_type --------------------------------------------------


def test_buying_type_match_passes() -> None:
    row = _row("campaign_buying_type", "Auction")
    result = check_campaign_buying_type(row, evidence=_evidence({"buying_type": "AUCTION"}))
    assert result.verdict == "Pass"


def test_buying_type_mismatch_is_fix() -> None:
    row = _row("campaign_buying_type", "Reserved")
    result = check_campaign_buying_type(row, evidence=_evidence({"buying_type": "AUCTION"}))
    assert result.verdict == "Fix"


def test_buying_type_missing_field_is_review() -> None:
    row = _row("campaign_buying_type", "Auction")
    result = check_campaign_buying_type(row, evidence=_evidence({}))
    assert result.verdict == "Review"


def test_buying_type_unrecognized_expected_is_review() -> None:
    row = _row("campaign_buying_type", "whatever")
    result = check_campaign_buying_type(row, evidence=_evidence({"buying_type": "AUCTION"}))
    assert result.verdict == "Review"


def test_buying_type_dropdown_reservation_maps_to_reserved() -> None:
    """MASTER DATA VALIDATION dropdown offers 'Reservation'; Meta enum is RESERVED.
    Calibration bug fix: this must Pass, not false-Review."""
    row = _row("campaign_buying_type", "Reservation")
    result = check_campaign_buying_type(row, evidence=_evidence({"buying_type": "RESERVED"}))
    assert result.verdict == "Pass"


def test_buying_type_reservation_vs_auction_is_fix() -> None:
    row = _row("campaign_buying_type", "Reservation")
    result = check_campaign_buying_type(row, evidence=_evidence({"buying_type": "AUCTION"}))
    assert result.verdict == "Fix"


# --- Peacock-mode vocabulary (objective + buying_type) ---------------------
# In Peacock mode both sides are Peacock's own vocab (Acquisition / Biddable),
# compared directly — no Meta-enum mapping.


def _peacock_ev(campaign: dict) -> dict:
    return {"campaign": campaign, "peacock_mode": True}


def test_objective_peacock_mode_direct_match_passes() -> None:
    row = _row("campaign_objective", "Acquisition")
    result = check_campaign_objective(row, evidence=_peacock_ev({"objective": "Acquisition"}))
    assert result.verdict == "Pass"


def test_objective_peacock_mode_mismatch_is_fix() -> None:
    row = _row("campaign_objective", "Awareness")
    result = check_campaign_objective(row, evidence=_peacock_ev({"objective": "Acquisition"}))
    assert result.verdict == "Fix"


def test_objective_peacock_mode_blank_actual_is_review() -> None:
    row = _row("campaign_objective", "Acquisition")
    result = check_campaign_objective(row, evidence=_peacock_ev({}))
    assert result.verdict == "Review"


def test_buying_type_peacock_mode_biddable_passes() -> None:
    row = _row("campaign_buying_type", "Biddable")
    result = check_campaign_buying_type(row, evidence=_peacock_ev({"buying_type": "Biddable"}))
    assert result.verdict == "Pass"


def test_objective_standard_mode_unchanged_for_peacock_vocab() -> None:
    """Guard: outside Peacock mode, Peacock's vocab ('Acquisition') is still an
    unrecognized Meta objective → Review (standard behavior unchanged)."""
    row = _row("campaign_objective", "Acquisition")
    result = check_campaign_objective(row, evidence=_evidence({"objective": "OUTCOME_SALES"}))
    assert result.verdict == "Review"


# --- campaign_budget -------------------------------------------------------
# BigQuery stores budgets in minor units (cents): daily_budget=200000 → $2,000.


def test_budget_daily_match_passes() -> None:
    row = _row("campaign_budget", "$2,000")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 200000, "lifetime_budget": 0}))
    assert result.verdict == "Pass"


def test_budget_plain_number_dollars_passes() -> None:
    row = _row("campaign_budget", "10")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 1000, "lifetime_budget": 0}))
    assert result.verdict == "Pass"  # 1000 cents = $10.00


def test_budget_lifetime_match_passes() -> None:
    row = _row("campaign_budget", "330")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 0, "lifetime_budget": 33000}))
    assert result.verdict == "Pass"


def test_budget_real_mismatch_is_fix() -> None:
    row = _row("campaign_budget", "$500")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 200000, "lifetime_budget": 0}))
    assert result.verdict == "Fix"
    assert "2,000.00" in result.action and "500" in result.action


def test_budget_cents_convention_is_review_not_fix() -> None:
    """If the builder's number equals the raw cents value, it's a dollars-vs-cents
    ambiguity — Review, never a wrong Fix (defensive until Kerri confirms format)."""
    row = _row("campaign_budget", "200000")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 200000, "lifetime_budget": 0}))
    assert result.verdict == "Review"
    assert "dollars" in result.action.lower()


def test_budget_unparseable_is_review() -> None:
    row = _row("campaign_budget", "two grand")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 200000, "lifetime_budget": 0}))
    assert result.verdict == "Review"


def test_budget_non_finite_input_is_review() -> None:
    """Audit #5: float() accepts 'nan'/'inf' without raising; those must become
    Review, not a bogus comparison → wrong Fix."""
    for bad in ("nan", "inf", "-inf", "Infinity"):
        row = _row("campaign_budget", bad)
        result = check_campaign_budget(
            row, evidence=_evidence({"daily_budget": 200000, "lifetime_budget": 0})
        )
        assert result.verdict == "Review", bad


def test_budget_no_campaign_level_budget_is_review() -> None:
    """Both daily and lifetime are 0 → budget is at the ad-set level (CBO off)."""
    row = _row("campaign_budget", "$2,000")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 0, "lifetime_budget": 0}))
    assert result.verdict == "Review"
    assert "ad set" in result.action.lower()


def test_budget_field_absent_is_review() -> None:
    row = _row("campaign_budget", "$2,000")
    result = check_campaign_budget(row, evidence=_evidence({}))
    assert result.verdict == "Review"


def test_budget_both_set_is_review() -> None:
    row = _row("campaign_budget", "$2,000")
    result = check_campaign_budget(row, evidence=_evidence({"daily_budget": 200000, "lifetime_budget": 50000}))
    assert result.verdict == "Review"


# --- campaign_status -------------------------------------------------------


def test_status_active_match_passes() -> None:
    row = _row("campaign_status", "Live")
    result = check_campaign_status(row, evidence=_evidence({"effective_status": "ACTIVE"}))
    assert result.verdict == "Pass"


def test_status_paused_match_passes() -> None:
    row = _row("campaign_status", "Paused")
    result = check_campaign_status(row, evidence=_evidence({"effective_status": "PAUSED"}))
    assert result.verdict == "Pass"


def test_status_mismatch_is_fix() -> None:
    row = _row("campaign_status", "Live")
    result = check_campaign_status(row, evidence=_evidence({"effective_status": "PAUSED"}))
    assert result.verdict == "Fix"


def test_status_missing_field_is_review() -> None:
    row = _row("campaign_status", "Live")
    result = check_campaign_status(row, evidence=_evidence({}))
    assert result.verdict == "Review"


def test_status_unrecognized_expected_is_review() -> None:
    row = _row("campaign_status", "blue")
    result = check_campaign_status(row, evidence=_evidence({"effective_status": "ACTIVE"}))
    assert result.verdict == "Review"


# --- campaign_start_date ---------------------------------------------------


def test_start_date_match_datetime_actual_passes() -> None:
    from datetime import datetime

    row = _row("campaign_start_date", "10/05/2024")
    actual = datetime(2024, 10, 5, 12, 30, 0, tzinfo=UTC)
    result = check_campaign_start_date(row, evidence=_evidence({"start_time": actual}))
    assert result.verdict == "Pass"


def test_start_date_match_iso_string_passes() -> None:
    row = _row("campaign_start_date", "2024-10-05")
    result = check_campaign_start_date(
        row, evidence=_evidence({"start_time": "2024-10-05 12:30:00"})
    )
    assert result.verdict == "Pass"


def test_start_date_mismatch_is_fix() -> None:
    row = _row("campaign_start_date", "10/05/2024")
    result = check_campaign_start_date(
        row, evidence=_evidence({"start_time": "2024-11-15"})
    )
    assert result.verdict == "Fix"


def test_start_date_unparseable_expected_is_review() -> None:
    row = _row("campaign_start_date", "next tuesday")
    result = check_campaign_start_date(
        row, evidence=_evidence({"start_time": "2024-10-05"})
    )
    assert result.verdict == "Review"


def test_start_date_missing_field_is_review() -> None:
    row = _row("campaign_start_date", "10/05/2024")
    result = check_campaign_start_date(row, evidence=_evidence({}))
    assert result.verdict == "Review"


# --- campaign_bid_strategy -------------------------------------------------


def test_bid_strategy_friendly_synonym_passes() -> None:
    """'Lowest cost' (Meta UI) matches LOWEST_COST_WITHOUT_CAP (Meta enum)."""
    row = _row("campaign_bid_strategy", "Lowest cost")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "LOWEST_COST_WITHOUT_CAP"})
    )
    assert result.verdict == "Pass"


def test_bid_strategy_exact_enum_match_passes() -> None:
    row = _row("campaign_bid_strategy", "COST_CAP")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "COST_CAP"})
    )
    assert result.verdict == "Pass"


def test_bid_strategy_dropdown_highest_volume_or_value_passes() -> None:
    """MASTER DATA VALIDATION dropdown label 'Highest volume or value' maps to
    LOWEST_COST_WITHOUT_CAP. Calibration bug fix: was false-Review before."""
    row = _row("campaign_bid_strategy", "Highest volume or value")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "LOWEST_COST_WITHOUT_CAP"})
    )
    assert result.verdict == "Pass"


def test_bid_strategy_dropdown_cost_per_result_goal_passes() -> None:
    row = _row("campaign_bid_strategy", "Cost per result goal")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "COST_CAP"})
    )
    assert result.verdict == "Pass"


def test_bid_strategy_dropdown_roas_goal_passes() -> None:
    row = _row("campaign_bid_strategy", "ROAS goal")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "LOWEST_COST_WITH_MIN_ROAS"})
    )
    assert result.verdict == "Pass"


def test_bid_strategy_dropdown_bid_cap_passes() -> None:
    row = _row("campaign_bid_strategy", "Bid cap")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "LOWEST_COST_WITH_BID_CAP"})
    )
    assert result.verdict == "Pass"


def test_bid_strategy_mismatch_is_fix() -> None:
    row = _row("campaign_bid_strategy", "Bid Cap")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "LOWEST_COST_WITHOUT_CAP"})
    )
    assert result.verdict == "Fix"


def test_bid_strategy_missing_field_is_review() -> None:
    row = _row("campaign_bid_strategy", "Lowest cost")
    result = check_campaign_bid_strategy(row, evidence=_evidence({}))
    assert result.verdict == "Review"


def test_bid_strategy_unrecognized_expected_is_review() -> None:
    row = _row("campaign_bid_strategy", "magic")
    result = check_campaign_bid_strategy(
        row, evidence=_evidence({"bid_strategy": "LOWEST_COST_WITHOUT_CAP"})
    )
    assert result.verdict == "Review"


# --- adset_status ----------------------------------------------------------


def test_adset_status_all_active_matches_live() -> None:
    row = _row("adset_status", "Live")
    result = check_adset_status(
        row,
        evidence=_adset_evidence(
            [
                {"id": 1, "effective_status": "ACTIVE"},
                {"id": 2, "effective_status": "ACTIVE"},
            ]
        ),
    )
    assert result.verdict == "Pass"


def test_adset_status_one_diverges_is_fix_with_adset_label() -> None:
    row = _row("adset_status", "Live")
    result = check_adset_status(
        row,
        evidence=_adset_evidence(
            [
                {"id": 1, "name": "Adset A", "effective_status": "ACTIVE"},
                {"id": 2, "name": "Adset B", "effective_status": "PAUSED"},
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "Adset B" in result.action
    assert "PAUSED" in result.action


def test_adset_status_multiple_mismatches_summarized() -> None:
    row = _row("adset_status", "Live")
    result = check_adset_status(
        row,
        evidence=_adset_evidence(
            [
                {"id": 1, "effective_status": "PAUSED"},
                {"id": 2, "effective_status": "PAUSED"},
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "+1 more" in result.action


def test_adset_status_no_ad_sets_is_review() -> None:
    row = _row("adset_status", "Live")
    result = check_adset_status(row, evidence=_adset_evidence([]))
    assert result.verdict == "Review"
    assert "no ad sets" in result.action.lower()


def test_adset_status_all_missing_is_review() -> None:
    row = _row("adset_status", "Live")
    result = check_adset_status(
        row, evidence=_adset_evidence([{"id": 1}, {"id": 2}])
    )
    assert result.verdict == "Review"
    assert "not available" in result.action.lower()


def test_adset_status_unrecognized_expected_is_review() -> None:
    row = _row("adset_status", "purple")
    result = check_adset_status(
        row, evidence=_adset_evidence([{"id": 1, "effective_status": "ACTIVE"}])
    )
    assert result.verdict == "Review"


# --- adset_start_date / adset_end_date -------------------------------------


def test_adset_start_date_match_passes() -> None:
    row = _row("adset_start_date", "10/05/2024")
    result = check_adset_start_date(
        row, evidence=_adset_evidence([{"id": 1, "start_time": "2024-10-05 12:30:00"}])
    )
    assert result.verdict == "Pass"


def test_adset_start_date_mismatch_is_fix() -> None:
    row = _row("adset_start_date", "10/05/2024")
    result = check_adset_start_date(
        row,
        evidence=_adset_evidence(
            [
                {"id": 1, "name": "Adset A", "start_time": "2024-10-05"},
                {"id": 2, "name": "Adset B", "start_time": "2024-11-15"},
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "Adset B" in result.action


def test_adset_end_date_match_passes() -> None:
    row = _row("adset_end_date", "2024-12-31")
    result = check_adset_end_date(
        row,
        evidence=_adset_evidence(
            [
                {"id": 1, "end_time": "2024-12-31T23:59:59"},
                {"id": 2, "end_time": "2024-12-31"},
            ]
        ),
    )
    assert result.verdict == "Pass"


def test_adset_end_date_all_missing_is_review() -> None:
    row = _row("adset_end_date", "2024-12-31")
    result = check_adset_end_date(
        row, evidence=_adset_evidence([{"id": 1}, {"id": 2}])
    )
    assert result.verdict == "Review"


def test_adset_start_date_unparseable_expected_is_review() -> None:
    row = _row("adset_start_date", "next tuesday")
    result = check_adset_start_date(
        row, evidence=_adset_evidence([{"id": 1, "start_time": "2024-10-05"}])
    )
    assert result.verdict == "Review"


# --- adset_age_min / adset_age_max -----------------------------------------


def test_adset_age_min_nested_targeting_match_passes() -> None:
    row = _row("adset_age_min", "18")
    result = check_adset_age_min(
        row,
        evidence=_adset_evidence(
            [{"id": 1, "targeting": {"age_min": 18, "age_max": 65}}]
        ),
    )
    assert result.verdict == "Pass"


def test_adset_age_min_flat_schema_match_passes() -> None:
    """Some clients store targeting as flat columns — read_targeting handles it."""
    row = _row("adset_age_min", "25")
    result = check_adset_age_min(
        row, evidence=_adset_evidence([{"id": 1, "age_min": 25, "age_max": 45}])
    )
    assert result.verdict == "Pass"


def test_adset_age_max_mismatch_is_fix() -> None:
    row = _row("adset_age_max", "65")
    result = check_adset_age_max(
        row,
        evidence=_adset_evidence(
            [
                {"id": 1, "name": "Adset A", "targeting": {"age_max": 65}},
                {"id": 2, "name": "Adset B", "targeting": {"age_max": 45}},
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "Adset B" in result.action
    assert "45" in result.action


def test_adset_age_min_missing_targeting_is_review() -> None:
    row = _row("adset_age_min", "18")
    result = check_adset_age_min(
        row, evidence=_adset_evidence([{"id": 1, "name": "Adset A"}])
    )
    assert result.verdict == "Review"


def test_adset_age_min_non_integer_expected_is_review() -> None:
    row = _row("adset_age_min", "young adults")
    result = check_adset_age_min(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"age_min": 18}}])
    )
    assert result.verdict == "Review"


# --- adset_genders ---------------------------------------------------------


def test_adset_genders_all_label_matches_meta_default() -> None:
    """Meta absent/empty = all; builder typed 'All' = {1, 2}."""
    row = _row("adset_genders", "All")
    result = check_adset_genders(
        row,
        evidence=_adset_evidence(
            [
                {"id": 1, "targeting": {"genders": [1, 2]}},
                {"id": 2, "targeting": {}},  # absent → all
            ]
        ),
    )
    assert result.verdict == "Pass"


def test_adset_genders_men_only_matches() -> None:
    row = _row("adset_genders", "Men")
    result = check_adset_genders(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"genders": [1]}}])
    )
    assert result.verdict == "Pass"


def test_adset_genders_women_only_mismatch_is_fix() -> None:
    row = _row("adset_genders", "Women")
    result = check_adset_genders(
        row,
        evidence=_adset_evidence(
            [{"id": 1, "name": "Adset A", "targeting": {"genders": [1]}}]
        ),
    )
    assert result.verdict == "Fix"
    assert "Men" in result.action
    assert "Women" in result.action


def test_adset_genders_unknown_label_is_review() -> None:
    row = _row("adset_genders", "everybody who clicks")
    result = check_adset_genders(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"genders": [1, 2]}}])
    )
    assert result.verdict == "Review"


def test_adset_genders_unparseable_actual_is_review() -> None:
    row = _row("adset_genders", "Men")
    result = check_adset_genders(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"genders": [99]}}])
    )
    assert result.verdict == "Review"


# --- adset_countries -------------------------------------------------------


def test_adset_countries_iso_codes_match_passes() -> None:
    row = _row("adset_countries", "US, CA")
    result = check_adset_countries(
        row,
        evidence=_adset_evidence([{"id": 1, "targeting": {"countries": ["US", "CA"]}}]),
    )
    assert result.verdict == "Pass"


def test_adset_countries_full_name_matches_iso_code() -> None:
    """'United States' on builder side maps to 'US' in BQ."""
    row = _row("adset_countries", "United States")
    result = check_adset_countries(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"countries": ["US"]}}])
    )
    assert result.verdict == "Pass"


def test_adset_countries_order_independent() -> None:
    row = _row("adset_countries", "CA, US")
    result = check_adset_countries(
        row,
        evidence=_adset_evidence([{"id": 1, "targeting": {"countries": ["US", "CA"]}}]),
    )
    assert result.verdict == "Pass"


def test_adset_countries_extra_country_is_fix() -> None:
    row = _row("adset_countries", "US")
    result = check_adset_countries(
        row,
        evidence=_adset_evidence(
            [{"id": 1, "name": "Adset A", "targeting": {"countries": ["US", "MX"]}}]
        ),
    )
    assert result.verdict == "Fix"
    assert "Adset A" in result.action


def test_adset_countries_unknown_input_is_review() -> None:
    row = _row("adset_countries", "Atlantis")
    result = check_adset_countries(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"countries": ["US"]}}])
    )
    assert result.verdict == "Review"


def test_adset_countries_all_missing_is_review() -> None:
    row = _row("adset_countries", "US")
    result = check_adset_countries(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {}}])
    )
    assert result.verdict == "Review"


def test_adset_countries_uk_alias_matches_gb() -> None:
    """Audit #1: builder types informal 'UK'; Meta/BQ stores 'GB'. Must Pass,
    not wrong-Fix (the 2-letter passthrough used to shadow the uk->GB alias)."""
    row = _row("adset_countries", "UK")
    result = check_adset_countries(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"countries": ["GB"]}}])
    )
    assert result.verdict == "Pass"


def test_adset_countries_unrelated_two_letter_still_passes_through() -> None:
    """Guard: a genuine 2-letter code not in the alias map still works."""
    row = _row("adset_countries", "CA")
    result = check_adset_countries(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"countries": ["CA"]}}])
    )
    assert result.verdict == "Pass"


def test_adset_countries_empty_list_is_review_not_fix() -> None:
    """Audit #4: an empty countries list ([]) means we can't confirm the
    location → Review, never a wrong Fix."""
    row = _row("adset_countries", "US")
    result = check_adset_countries(
        row, evidence=_adset_evidence([{"id": 1, "targeting": {"countries": []}}])
    )
    assert result.verdict == "Review"


# --- ad_status -------------------------------------------------------------


def test_ad_status_all_active_passes() -> None:
    row = _row("ad_status", "Live")
    result = check_ad_status(
        row,
        evidence=_ad_evidence(
            [
                {"id": 1, "effective_status": "ACTIVE"},
                {"id": 2, "effective_status": "ACTIVE"},
            ]
        ),
    )
    assert result.verdict == "Pass"


def test_ad_status_one_diverges_is_fix() -> None:
    row = _row("ad_status", "Live")
    result = check_ad_status(
        row,
        evidence=_ad_evidence(
            [
                {"id": 1, "name": "Hero", "effective_status": "ACTIVE"},
                {"id": 2, "name": "Variant B", "effective_status": "PAUSED"},
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "Variant B" in result.action
    assert "PAUSED" in result.action


def test_ad_status_no_ads_is_review() -> None:
    row = _row("ad_status", "Live")
    result = check_ad_status(row, evidence=_ad_evidence([]))
    assert result.verdict == "Review"


def test_ad_status_all_missing_is_review() -> None:
    row = _row("ad_status", "Live")
    result = check_ad_status(
        row, evidence=_ad_evidence([{"id": 1}, {"id": 2}])
    )
    assert result.verdict == "Review"


def test_ad_status_unrecognized_expected_is_review() -> None:
    row = _row("ad_status", "purple")
    result = check_ad_status(
        row, evidence=_ad_evidence([{"id": 1, "effective_status": "ACTIVE"}])
    )
    assert result.verdict == "Review"


# --- ad_count --------------------------------------------------------------


def test_ad_count_match_passes() -> None:
    row = _row("ad_count", "3")
    result = check_ad_count(
        row, evidence=_ad_evidence([{"id": 1}, {"id": 2}, {"id": 3}])
    )
    assert result.verdict == "Pass"


def test_ad_count_zero_match_passes() -> None:
    """Zero is a legit data point — campaign with no ads, builder expects 0."""
    row = _row("ad_count", "0")
    result = check_ad_count(row, evidence=_ad_evidence([]))
    assert result.verdict == "Pass"


def test_ad_count_mismatch_is_fix() -> None:
    row = _row("ad_count", "5")
    result = check_ad_count(row, evidence=_ad_evidence([{"id": 1}, {"id": 2}]))
    assert result.verdict == "Fix"
    assert "5" in result.action
    assert "2" in result.action


def test_ad_count_non_integer_is_review() -> None:
    row = _row("ad_count", "many")
    result = check_ad_count(row, evidence=_ad_evidence([{"id": 1}]))
    assert result.verdict == "Review"


def test_ad_count_negative_is_review() -> None:
    row = _row("ad_count", "-1")
    result = check_ad_count(row, evidence=_ad_evidence([{"id": 1}]))
    assert result.verdict == "Review"


# --- ad_destination_url ----------------------------------------------------


def test_ad_url_exact_match_passes() -> None:
    row = _row("ad_destination_url", "https://example.com/products")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence(
            [{"id": 1, "link_url": "https://example.com/products"}]
        ),
    )
    assert result.verdict == "Pass"


def test_ad_url_trailing_slash_normalized() -> None:
    """Trailing slash on path is normalized away; same URL passes."""
    row = _row("ad_destination_url", "https://example.com/products/")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence(
            [{"id": 1, "link_url": "https://example.com/products"}]
        ),
    )
    assert result.verdict == "Pass"


def test_ad_url_scheme_and_host_lowercased() -> None:
    row = _row("ad_destination_url", "HTTPS://EXAMPLE.COM/path")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence(
            [{"id": 1, "link_url": "https://example.com/path"}]
        ),
    )
    assert result.verdict == "Pass"


def test_ad_url_utm_difference_is_fix() -> None:
    """Strict on query params: different UTMs flag as Fix, not a false Pass."""
    row = _row(
        "ad_destination_url", "https://example.com/p?utm_source=facebook"
    )
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence(
            [
                {
                    "id": 1,
                    "name": "Ad A",
                    "link_url": "https://example.com/p?utm_source=google",
                }
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "Ad A" in result.action


def test_ad_url_nested_creative_field_read() -> None:
    """Falls through to creative.link_url when top-level link_url is absent."""
    row = _row("ad_destination_url", "https://example.com/page")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence(
            [{"id": 1, "creative": {"link_url": "https://example.com/page"}}]
        ),
    )
    assert result.verdict == "Pass"


def test_ad_url_deeply_nested_object_story_spec_path() -> None:
    """Some BQ schemas put it under creative.object_story_spec.link_data.link."""
    row = _row("ad_destination_url", "https://example.com/deep")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence(
            [
                {
                    "id": 1,
                    "creative": {
                        "object_story_spec": {
                            "link_data": {"link": "https://example.com/deep"}
                        }
                    },
                }
            ]
        ),
    )
    assert result.verdict == "Pass"


def test_ad_url_one_ad_diverges_is_fix() -> None:
    row = _row("ad_destination_url", "https://example.com/lp")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence(
            [
                {"id": 1, "link_url": "https://example.com/lp"},
                {
                    "id": 2,
                    "name": "Bad URL Ad",
                    "link_url": "https://wrongsite.com/lp",
                },
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "Bad URL Ad" in result.action


def test_ad_url_no_ads_is_review() -> None:
    row = _row("ad_destination_url", "https://example.com")
    result = check_ad_destination_url(row, evidence=_ad_evidence([]))
    assert result.verdict == "Review"


def test_ad_url_all_missing_url_is_review() -> None:
    row = _row("ad_destination_url", "https://example.com")
    result = check_ad_destination_url(
        row, evidence=_ad_evidence([{"id": 1}, {"id": 2}])
    )
    assert result.verdict == "Review"


def test_ad_url_unparseable_expected_is_review() -> None:
    row = _row("ad_destination_url", "::: not a url :::")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence([{"id": 1, "link_url": "https://example.com"}]),
    )
    assert result.verdict == "Review"


def test_ad_url_bare_host_in_builder_input_normalized() -> None:
    """Builder pastes 'example.com'; we default to https for comparison."""
    row = _row("ad_destination_url", "example.com")
    result = check_ad_destination_url(
        row,
        evidence=_ad_evidence([{"id": 1, "link_url": "https://example.com"}]),
    )
    assert result.verdict == "Pass"


# --- ad_call_to_action -----------------------------------------------------


def test_cta_dropdown_label_matches_enum_passes() -> None:
    """Builder picks 'Learn More'; Meta stores LEARN_MORE → Pass."""
    row = _row("ad_call_to_action", "Learn More")
    result = check_ad_call_to_action(
        row, evidence=_ad_evidence([{"id": 1, "call_to_action_type": "LEARN_MORE"}])
    )
    assert result.verdict == "Pass"


def test_cta_special_send_message_maps_to_message_page() -> None:
    """'Send Message' (dropdown) maps to Meta's MESSAGE_PAGE enum."""
    row = _row("ad_call_to_action", "Send Message")
    result = check_ad_call_to_action(
        row, evidence=_ad_evidence([{"id": 1, "call_to_action_type": "MESSAGE_PAGE"}])
    )
    assert result.verdict == "Pass"


def test_cta_nested_creative_path_read() -> None:
    row = _row("ad_call_to_action", "Shop Now")
    result = check_ad_call_to_action(
        row,
        evidence=_ad_evidence(
            [
                {
                    "id": 1,
                    "creative": {
                        "object_story_spec": {
                            "link_data": {"call_to_action": {"type": "SHOP_NOW"}}
                        }
                    },
                }
            ]
        ),
    )
    assert result.verdict == "Pass"


def test_cta_mismatch_is_fix() -> None:
    row = _row("ad_call_to_action", "Sign Up")
    result = check_ad_call_to_action(
        row,
        evidence=_ad_evidence(
            [{"id": 1, "name": "Ad A", "call_to_action_type": "LEARN_MORE"}]
        ),
    )
    assert result.verdict == "Fix"
    assert "Ad A" in result.action


def test_cta_unrecognized_expected_is_review() -> None:
    row = _row("ad_call_to_action", "Do a backflip")
    result = check_ad_call_to_action(
        row, evidence=_ad_evidence([{"id": 1, "call_to_action_type": "LEARN_MORE"}])
    )
    assert result.verdict == "Review"


def test_cta_all_missing_is_review() -> None:
    row = _row("ad_call_to_action", "Learn More")
    result = check_ad_call_to_action(
        row, evidence=_ad_evidence([{"id": 1}, {"id": 2}])
    )
    assert result.verdict == "Review"


def test_cta_no_ads_is_review() -> None:
    row = _row("ad_call_to_action", "Learn More")
    result = check_ad_call_to_action(row, evidence=_ad_evidence([]))
    assert result.verdict == "Review"


# --- adset_conversion_event (the Peacock-Olympics check) -------------------


def _adset_with_event(event: str, *, name: str = "", nested: bool = True) -> dict:
    if nested:
        return {"id": 1, "name": name, "promoted_object": {"custom_event_type": event}}
    return {"id": 1, "name": name, "custom_event_type": event}


def test_conversion_event_exact_match_passes() -> None:
    row = _row("adset_conversion_event", "Purchase")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("PURCHASE")])
    )
    assert result.verdict == "Pass"


def test_conversion_event_casing_only_passes() -> None:
    """'Purchase' vs stored 'purchase' is just casing → Pass."""
    row = _row("adset_conversion_event", "Purchase")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("purchase")])
    )
    assert result.verdict == "Pass"


def test_conversion_event_PEACOCK_near_match_is_NOT_pass() -> None:
    """THE canonical case: builder expects 'Purchase', ad set is set to
    'purchase event'. A human glossed over this; the bot must NOT Pass —
    it escalates to Review (never a silent pass on a near-match)."""
    row = _row("adset_conversion_event", "Purchase")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("purchase event")])
    )
    assert result.verdict == "Review"
    assert result.verdict != "Pass"
    assert "not a recognized standard event" in result.action


def test_conversion_event_confident_mismatch_is_fix() -> None:
    """Two recognized standard events that differ → confident Fix."""
    row = _row("adset_conversion_event", "Purchase")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("LEAD", name="AS1")])
    )
    assert result.verdict == "Fix"
    assert "AS1" in result.action


def test_conversion_event_friendly_synonym_passes() -> None:
    row = _row("adset_conversion_event", "Registration")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("COMPLETE_REGISTRATION")])
    )
    assert result.verdict == "Pass"


def test_conversion_event_flat_field_read() -> None:
    row = _row("adset_conversion_event", "Add to Cart")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("ADD_TO_CART", nested=False)])
    )
    assert result.verdict == "Pass"


def test_conversion_event_custom_exact_match_passes() -> None:
    """Both sides a non-standard custom event, identical → Pass (exact match)."""
    row = _row("adset_conversion_event", "lead_q4_2026")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("lead_q4_2026")])
    )
    assert result.verdict == "Pass"


def test_conversion_event_custom_differ_is_review() -> None:
    """Two unmappable customs that differ → Review, not Fix (can't be confident)."""
    row = _row("adset_conversion_event", "lead_q4_2026")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("lead_q3_2026")])
    )
    assert result.verdict == "Review"


def test_conversion_event_uninterpretable_expected_is_review() -> None:
    row = _row("adset_conversion_event", "make me money")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([_adset_with_event("PURCHASE")])
    )
    assert result.verdict == "Review"


def test_conversion_event_all_missing_is_review() -> None:
    row = _row("adset_conversion_event", "Purchase")
    result = check_adset_conversion_event(
        row, evidence=_adset_evidence([{"id": 1}, {"id": 2}])
    )
    assert result.verdict == "Review"


def test_conversion_event_no_ad_sets_is_review() -> None:
    row = _row("adset_conversion_event", "Purchase")
    result = check_adset_conversion_event(row, evidence=_adset_evidence([]))
    assert result.verdict == "Review"


def test_conversion_event_one_of_many_diverges_is_fix() -> None:
    row = _row("adset_conversion_event", "Purchase")
    result = check_adset_conversion_event(
        row,
        evidence=_adset_evidence(
            [
                _adset_with_event("PURCHASE", name="AS1"),
                _adset_with_event("LEAD", name="AS2"),
            ]
        ),
    )
    assert result.verdict == "Fix"
    assert "AS2" in result.action


# --- adset_attribution_setting ---------------------------------------------


def _adset_with_attr(spec, *, name: str = "") -> dict:
    return {"id": 1, "name": name, "attribution_spec": spec}


def test_attribution_click_and_view_match_passes() -> None:
    """Builder '7-day click, 1-day view' matches the real BQ spec shape."""
    row = _row("adset_attribution_setting", "7-day click, 1-day view")
    spec = [
        {"event_type": "CLICK_THROUGH", "window_days": 7},
        {"event_type": "VIEW_THROUGH", "window_days": 1},
    ]
    result = check_adset_attribution_setting(
        row, evidence=_adset_evidence([_adset_with_attr(spec)])
    )
    assert result.verdict == "Pass"


def test_attribution_order_independent() -> None:
    row = _row("adset_attribution_setting", "1-day view, 7-day click")
    spec = [
        {"event_type": "CLICK_THROUGH", "window_days": 7},
        {"event_type": "VIEW_THROUGH", "window_days": 1},
    ]
    result = check_adset_attribution_setting(
        row, evidence=_adset_evidence([_adset_with_attr(spec)])
    )
    assert result.verdict == "Pass"


def test_attribution_single_click_match_passes() -> None:
    row = _row("adset_attribution_setting", "7-day click")
    spec = [{"event_type": "CLICK_THROUGH", "window_days": 7}]
    result = check_adset_attribution_setting(
        row, evidence=_adset_evidence([_adset_with_attr(spec)])
    )
    assert result.verdict == "Pass"


def test_attribution_mismatch_is_fix() -> None:
    row = _row("adset_attribution_setting", "1-day click")
    spec = [{"event_type": "CLICK_THROUGH", "window_days": 7}]
    result = check_adset_attribution_setting(
        row, evidence=_adset_evidence([_adset_with_attr(spec, name="AS1")])
    )
    assert result.verdict == "Fix"
    assert "AS1" in result.action
    assert "7-day click" in result.action


def test_attribution_empty_spec_is_review() -> None:
    """Empty attribution_spec = no window (non-conversion ad set) → Review."""
    row = _row("adset_attribution_setting", "7-day click")
    result = check_adset_attribution_setting(
        row, evidence=_adset_evidence([_adset_with_attr([])])
    )
    assert result.verdict == "Review"


def test_attribution_missing_column_is_review() -> None:
    row = _row("adset_attribution_setting", "7-day click")
    result = check_adset_attribution_setting(
        row, evidence=_adset_evidence([{"id": 1}])
    )
    assert result.verdict == "Review"


def test_attribution_unparseable_expected_is_review() -> None:
    row = _row("adset_attribution_setting", "whenever")
    spec = [{"event_type": "CLICK_THROUGH", "window_days": 7}]
    result = check_adset_attribution_setting(
        row, evidence=_adset_evidence([_adset_with_attr(spec)])
    )
    assert result.verdict == "Review"


# --- adset_optimization_goal -----------------------------------------------


def test_optimization_goal_exact_enum_passes() -> None:
    row = _row("adset_optimization_goal", "OFFSITE_CONVERSIONS")
    result = check_adset_optimization_goal(
        row, evidence=_adset_evidence([{"id": 1, "optimization_goal": "OFFSITE_CONVERSIONS"}])
    )
    assert result.verdict == "Pass"


def test_optimization_goal_friendly_conversions_passes() -> None:
    row = _row("adset_optimization_goal", "Conversions")
    result = check_adset_optimization_goal(
        row, evidence=_adset_evidence([{"id": 1, "optimization_goal": "OFFSITE_CONVERSIONS"}])
    )
    assert result.verdict == "Pass"


def test_optimization_goal_mismatch_is_fix() -> None:
    row = _row("adset_optimization_goal", "Conversions")
    result = check_adset_optimization_goal(
        row,
        evidence=_adset_evidence(
            [{"id": 1, "name": "AS1", "optimization_goal": "LINK_CLICKS"}]
        ),
    )
    assert result.verdict == "Fix"
    assert "AS1" in result.action


def test_optimization_goal_unrecognized_expected_is_review() -> None:
    row = _row("adset_optimization_goal", "make it go viral")
    result = check_adset_optimization_goal(
        row, evidence=_adset_evidence([{"id": 1, "optimization_goal": "CLICKS"}])
    )
    assert result.verdict == "Review"


def test_optimization_goal_unrecognized_actual_is_review() -> None:
    row = _row("adset_optimization_goal", "Conversions")
    result = check_adset_optimization_goal(
        row, evidence=_adset_evidence([{"id": 1, "optimization_goal": "SOME_NEW_GOAL"}])
    )
    assert result.verdict == "Review"


def test_optimization_goal_missing_is_review() -> None:
    row = _row("adset_optimization_goal", "Conversions")
    result = check_adset_optimization_goal(
        row, evidence=_adset_evidence([{"id": 1}])
    )
    assert result.verdict == "Review"


# --- ad-set bidirectional presence checks (Brandon 2026-06-01) -------------


def test_spend_minimum_yes_present_passes() -> None:
    row = _row("adset_spend_minimum", "Yes")
    result = check_adset_spend_minimum(
        row, evidence=_adset_evidence([{"id": 1, "daily_min_spend_target": 5000}])
    )
    assert result.verdict == "Pass"


def test_spend_minimum_yes_but_absent_is_fix() -> None:
    row = _row("adset_spend_minimum", "Yes")
    result = check_adset_spend_minimum(
        row,
        evidence=_adset_evidence([{"id": 1, "name": "AS1", "daily_min_spend_target": 0}]),
    )
    assert result.verdict == "Fix"
    assert "AS1" in result.action


def test_spend_minimum_no_and_absent_passes() -> None:
    row = _row("adset_spend_minimum", "No")
    result = check_adset_spend_minimum(
        row, evidence=_adset_evidence([{"id": 1, "daily_min_spend_target": 0}])
    )
    assert result.verdict == "Pass"


def test_spend_minimum_no_but_present_is_review_accidental() -> None:
    """The 'accidentally included' catch: builder said No, but a min IS set."""
    row = _row("adset_spend_minimum", "No")
    result = check_adset_spend_minimum(
        row,
        evidence=_adset_evidence([{"id": 1, "name": "AS1", "daily_min_spend_target": 9000}]),
    )
    assert result.verdict == "Review"
    assert "accidentally" in result.action.lower()


def test_spend_minimum_blank_present_is_review() -> None:
    """Blank builder input still checks for accidental inclusion (always-run)."""
    row = _row("adset_spend_minimum", "")
    result = check_adset_spend_minimum(
        row, evidence=_adset_evidence([{"id": 1, "daily_min_spend_target": 9000}])
    )
    assert result.verdict == "Review"


def test_spend_maximum_yes_present_passes() -> None:
    row = _row("adset_spend_maximum", "Yes")
    result = check_adset_spend_maximum(
        row, evidence=_adset_evidence([{"id": 1, "daily_spend_cap": 100000}])
    )
    assert result.verdict == "Pass"


def test_audiences_yes_present_passes() -> None:
    row = _row("adset_audiences", "Yes")
    result = check_adset_audiences(
        row,
        evidence=_adset_evidence(
            [{"id": 1, "targeting": {"custom_audiences": [{"id": "123"}]}}]
        ),
    )
    assert result.verdict == "Pass"


def test_audiences_yes_but_empty_is_fix() -> None:
    row = _row("adset_audiences", "Yes")
    result = check_adset_audiences(
        row,
        evidence=_adset_evidence([{"id": 1, "name": "AS1", "targeting": {"custom_audiences": []}}]),
    )
    assert result.verdict == "Fix"


def test_audiences_yes_but_field_not_synced_is_review_not_fix() -> None:
    """custom_audiences isn't synced for every client (e.g. C61854560). With the
    field absent, a builder 'Yes' must NOT falsely Fix — we can't verify, so
    Review (Peacock rule: never a wrong flag)."""
    row = _row("adset_audiences", "Yes")
    result = check_adset_audiences(
        row,
        evidence=_adset_evidence(
            [{"id": 1, "name": "AS1", "targeting": {"age_min": 25, "countries": ["US"]}}]
        ),
    )
    assert result.verdict == "Review"
    assert "bigquery" in result.action.lower()


def test_spend_minimum_field_not_synced_is_review_not_fix() -> None:
    """If daily_min_spend_target isn't synced (column absent), 'Yes' → Review,
    not a false Fix."""
    row = _row("adset_spend_minimum", "Yes")
    result = check_adset_spend_minimum(
        row, evidence=_adset_evidence([{"id": 1, "name": "AS1", "daily_spend_cap": 0}])
    )
    assert result.verdict == "Review"
    assert "bigquery" in result.action.lower()


def test_exclusions_no_but_present_is_review() -> None:
    row = _row("adset_audience_exclusions", "No")
    result = check_adset_audience_exclusions(
        row,
        evidence=_adset_evidence(
            [{"id": 1, "name": "AS1", "targeting": {"excluded_custom_audiences": [{"id": "9"}]}}]
        ),
    )
    assert result.verdict == "Review"


def test_presence_uninterpretable_input_is_review() -> None:
    row = _row("adset_spend_minimum", "maybe")
    result = check_adset_spend_minimum(
        row, evidence=_adset_evidence([{"id": 1, "daily_min_spend_target": 0}])
    )
    assert result.verdict == "Review"


def test_presence_checks_always_run_on_blank_input() -> None:
    """ALWAYS_RUN: a blank builder input must NOT short-circuit to N/A — the
    check still runs (to catch accidental inclusion)."""
    from app.core.pipeline import ALWAYS_RUN_CHECK_IDS
    for cid in ("adset_spend_minimum", "adset_spend_maximum", "adset_audiences", "adset_audience_exclusions"):
        assert cid in ALWAYS_RUN_CHECK_IDS


# --- registry integration --------------------------------------------------


def test_checks_registered() -> None:
    assert "campaign_objective" in CHECK_REGISTRY
    assert "campaign_buying_type" in CHECK_REGISTRY
    assert "campaign_status" in CHECK_REGISTRY
    assert "campaign_start_date" in CHECK_REGISTRY
    assert "campaign_bid_strategy" in CHECK_REGISTRY
    # Ad-set checks
    assert "adset_status" in CHECK_REGISTRY
    assert "adset_start_date" in CHECK_REGISTRY
    assert "adset_end_date" in CHECK_REGISTRY
    assert "adset_age_min" in CHECK_REGISTRY
    assert "adset_age_max" in CHECK_REGISTRY
    assert "adset_genders" in CHECK_REGISTRY
    assert "adset_countries" in CHECK_REGISTRY
    assert "adset_conversion_event" in CHECK_REGISTRY
    assert "adset_attribution_setting" in CHECK_REGISTRY
    assert "adset_optimization_goal" in CHECK_REGISTRY
    assert "adset_spend_minimum" in CHECK_REGISTRY
    assert "adset_spend_maximum" in CHECK_REGISTRY
    assert "adset_audiences" in CHECK_REGISTRY
    assert "adset_audience_exclusions" in CHECK_REGISTRY
    # Ad checks
    assert "ad_status" in CHECK_REGISTRY
    assert "ad_count" in CHECK_REGISTRY
    assert "ad_destination_url" in CHECK_REGISTRY
    assert "ad_call_to_action" in CHECK_REGISTRY


def test_run_check_dispatches_to_objective() -> None:
    row = _row("campaign_objective", "Traffic")
    result = run_check(row, evidence=_evidence({"objective": "OUTCOME_TRAFFIC"}))
    assert result.verdict == "Pass"


def test_run_check_unknown_id_still_errors() -> None:
    row = _row("not_a_real_check", "x")
    result = run_check(row, evidence=_evidence({}))
    assert result.verdict == "Error"
    assert "Unrecognized" in result.action


# --- ad_creative_dimensions (Peacock Phase B: Frame_Size) ------------------


def _peacock_dim_evidence(frame_sizes: list[str | None]) -> dict:
    """Peacock ad evidence: each entry is a trafficked Frame_Size, or None for an
    ad whose trafficking row didn't sync a frame size."""
    ads: list[dict] = []
    for i, fs in enumerate(frame_sizes):
        ad: dict = {"id": f"cr{i}", "creative": {}}
        if fs is not None:
            ad["trafficking"] = {"frame_size": fs}
        ads.append(ad)
    return {"campaign": {}, "ad_sets": [], "ads": ads, "peacock_mode": True}


def test_dimensions_non_peacock_is_manual_review() -> None:
    """For standard accounts the function itself defends to manual Review (the
    pipeline also short-circuits before reaching it)."""
    row = _row("ad_creative_dimensions", "1080x1920")
    result = check_ad_creative_dimensions(
        row, evidence=_ad_evidence([{"trafficking": {"frame_size": "1080x1920"}}])
    )
    assert result.verdict == "Review"
    assert "manual" in result.action.lower()


def test_dimensions_peacock_exact_match_passes() -> None:
    row = _row("ad_creative_dimensions", "1080x1920, 1080x1080")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence(["1080x1920", "1080x1080"])
    )
    assert result.verdict == "Pass"


def test_dimensions_peacock_ratio_input_matches_pixels() -> None:
    """Builder may type a ratio (9x16) and match the trafficked pixel size."""
    row = _row("ad_creative_dimensions", "9x16, 1x1")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence(["1080x1920", "1080x1080"])
    )
    assert result.verdict == "Pass"


def test_dimensions_peacock_missing_expected_all_seen_is_fix() -> None:
    """Expected 1:1 but every creative is 9:16 (all sizes seen) → confident Fix."""
    row = _row("ad_creative_dimensions", "9:16, 1:1")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence(["1080x1920", "1080x1920"])
    )
    assert result.verdict == "Fix"
    assert "1:1" in result.action


def test_dimensions_peacock_missing_but_unsynced_ad_downgrades_to_review() -> None:
    """An unsynced ad could BE the 'missing' size, so a would-be Fix → Review
    (cardinal rule: never a false Fix)."""
    row = _row("ad_creative_dimensions", "9:16, 1:1")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence(["1080x1920", None])
    )
    assert result.verdict == "Review"


def test_dimensions_peacock_extra_size_is_review() -> None:
    row = _row("ad_creative_dimensions", "9:16")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence(["1080x1920", "1080x1080"])
    )
    assert result.verdict == "Review"
    assert "1:1" in result.action


def test_dimensions_peacock_unparseable_builder_is_review() -> None:
    row = _row("ad_creative_dimensions", "square-ish")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence(["1080x1920"])
    )
    assert result.verdict == "Review"


def test_dimensions_peacock_no_frame_data_is_review() -> None:
    row = _row("ad_creative_dimensions", "9:16")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence([None, None])
    )
    assert result.verdict == "Review"
    assert "not available" in result.action.lower()


def test_dimensions_peacock_unparseable_actual_is_review() -> None:
    row = _row("ad_creative_dimensions", "9:16")
    result = check_ad_creative_dimensions(
        row, evidence=_peacock_dim_evidence(["weird-value"])
    )
    assert result.verdict == "Review"


# --- adset_audiences via Peacock adapter-shaping (Lever 1) ------------------


def test_audiences_peacock_shaped_present_and_expected_passes() -> None:
    ev = _adset_evidence([{"name": "as1", "targeting": {"custom_audiences": ["Broad", "SubscriberLAL"]}}])
    assert check_adset_audiences(_row("adset_audiences", "Yes"), evidence=ev).verdict == "Pass"


def test_audiences_peacock_present_but_not_expected_is_review() -> None:
    ev = _adset_evidence([{"name": "as1", "targeting": {"custom_audiences": ["Broad"]}}])
    assert check_adset_audiences(_row("adset_audiences", "No"), evidence=ev).verdict == "Review"


def test_audiences_peacock_synced_empty_and_expected_is_fix() -> None:
    # Synced (column present) but empty + builder Yes -> Fix (expected, none present).
    ev = _adset_evidence([{"name": "as1", "targeting": {"custom_audiences": []}}])
    assert check_adset_audiences(_row("adset_audiences", "Yes"), evidence=ev).verdict == "Fix"


# --- adset_placements (Lever 1) --------------------------------------------


def test_placements_exact_match_passes() -> None:
    ev = _adset_evidence([{"name": "as1", "placements": ["Stories", "Reels", "In-Feed"]}])
    assert check_adset_placements(_row("adset_placements", "Stories, Reels, In-Feed"), evidence=ev).verdict == "Pass"


def test_placements_normalizes_spaces_and_hyphens() -> None:
    ev = _adset_evidence([{"name": "as1", "placements": ["Stories", "In-Feed"]}])
    assert check_adset_placements(_row("adset_placements", "stories, in feed"), evidence=ev).verdict == "Pass"


def test_placements_mismatch_is_review_not_fix() -> None:
    ev = _adset_evidence([{"name": "as1", "placements": ["Stories", "Reels"]}])
    assert check_adset_placements(_row("adset_placements", "Stories"), evidence=ev).verdict == "Review"


def test_placements_no_data_is_review() -> None:
    ev = _adset_evidence([{"name": "as1"}])
    assert check_adset_placements(_row("adset_placements", "Stories"), evidence=ev).verdict == "Review"


# --- ad_flight_window QC surface (Lever 1) ---------------------------------


def _flight_evidence(flags: list[str | None], peacock: bool = True) -> dict:
    ads: list[dict] = []
    for i, f in enumerate(flags):
        ad: dict = {"id": f"cr{i}"}
        if f is not None:
            ad["trafficking"] = {"flight_window_flag": f}
        ads.append(ad)
    ev = {"campaign": {}, "ad_sets": [], "ads": ads}
    if peacock:
        ev["peacock_mode"] = True
    return ev


def test_flight_window_all_clear_passes() -> None:
    ev = _flight_evidence(["🚦 All Clear: Live within Flight Window 🚦"])
    assert check_ad_flight_window(_row("ad_flight_window", ""), evidence=ev).verdict == "Pass"


def test_flight_window_caution_is_review() -> None:
    ev = _flight_evidence(["🚦 All Clear: Live within Flight Window 🚦", "‼️ Caution: Approaching End Date ‼️"])
    result = check_ad_flight_window(_row("ad_flight_window", ""), evidence=ev)
    assert result.verdict == "Review"
    assert "Caution" in result.action


def test_flight_window_no_flag_is_review() -> None:
    ev = _flight_evidence([None, None])
    assert check_ad_flight_window(_row("ad_flight_window", ""), evidence=ev).verdict == "Review"


def test_flight_window_non_peacock_is_review() -> None:
    ev = _flight_evidence(["🚦 All Clear 🚦"], peacock=False)
    assert check_ad_flight_window(_row("ad_flight_window", ""), evidence=ev).verdict == "Review"


# --- flight dates: Peacock = Review-on-mismatch (never a false Fix) ---------


def _peacock_date_ev(start_time: str) -> dict:
    return {"campaign": {}, "ad_sets": [{"name": "as1", "start_time": start_time}], "ads": [], "peacock_mode": True}


def test_adset_start_date_peacock_mismatch_is_review_not_fix() -> None:
    ev = _peacock_date_ev("2025-11-24")
    assert check_adset_start_date(_row("adset_start_date", "06/01/2026"), evidence=ev).verdict == "Review"


def test_adset_start_date_peacock_match_passes() -> None:
    ev = _peacock_date_ev("2025-11-24")
    assert check_adset_start_date(_row("adset_start_date", "2025-11-24"), evidence=ev).verdict == "Pass"


def test_adset_start_date_standard_mismatch_still_fixes() -> None:
    # Non-Peacock keeps the confident Fix on a date mismatch.
    ev = {"campaign": {}, "ad_sets": [{"name": "as1", "start_time": "2025-11-24"}], "ads": []}
    assert check_adset_start_date(_row("adset_start_date", "06/01/2026"), evidence=ev).verdict == "Fix"


# --- ad_destination_url: domain mode (Lever 1) -----------------------------


def test_destination_url_domain_mode_passes_across_unique_urls() -> None:
    """A bare-domain expectation matches every ad by host, even when each ad's
    full tracking URL is unique (Peacock's per-creative cid/utm scheme)."""
    ads = [
        {"id": "1", "creative": {"link_url": "https://www.peacocktv.com/?cid=abc&utm_x=1"}},
        {"id": "2", "creative": {"link_url": "https://www.peacocktv.com/stream/show?cid=def"}},
        {"id": "3", "creative": {"link_url": "https://peacocktv.com/?gid=2"}},
    ]
    result = check_ad_destination_url(_row("ad_destination_url", "peacocktv.com"), evidence=_ad_evidence(ads))
    assert result.verdict == "Pass"
    assert "peacocktv.com" in result.action


def test_destination_url_domain_mode_strips_www_both_sides() -> None:
    ads = [{"id": "1", "creative": {"link_url": "https://peacocktv.com/x"}}]
    result = check_ad_destination_url(_row("ad_destination_url", "www.peacocktv.com"), evidence=_ad_evidence(ads))
    assert result.verdict == "Pass"


def test_destination_url_domain_mode_fixes_wrong_domain() -> None:
    ads = [
        {"id": "1", "creative": {"link_url": "https://www.peacocktv.com/?cid=1"}},
        {"id": "2", "creative": {"link_url": "https://example.com/landing"}},
    ]
    result = check_ad_destination_url(_row("ad_destination_url", "peacocktv.com"), evidence=_ad_evidence(ads))
    assert result.verdict == "Fix"
    assert "example.com" in result.action


def test_destination_url_full_url_still_exact() -> None:
    # A full-URL expectation keeps exact comparison — a differing query Fixes.
    ads = [{"id": "1", "creative": {"link_url": "https://www.peacocktv.com/?cid=1"}}]
    result = check_ad_destination_url(
        _row("ad_destination_url", "https://www.peacocktv.com/?cid=2"), evidence=_ad_evidence(ads)
    )
    assert result.verdict == "Fix"
