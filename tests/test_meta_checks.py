"""Unit tests for Meta deterministic check functions."""

from __future__ import annotations

from app.checks.meta_checks import (
    check_campaign_bid_strategy,
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
    from datetime import datetime, timezone

    row = _row("campaign_start_date", "10/05/2024")
    actual = datetime(2024, 10, 5, 12, 30, 0, tzinfo=timezone.utc)
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


# --- registry integration --------------------------------------------------


def test_checks_registered() -> None:
    assert "campaign_objective" in CHECK_REGISTRY
    assert "campaign_buying_type" in CHECK_REGISTRY
    assert "campaign_status" in CHECK_REGISTRY
    assert "campaign_start_date" in CHECK_REGISTRY
    assert "campaign_bid_strategy" in CHECK_REGISTRY


def test_run_check_dispatches_to_objective() -> None:
    row = _row("campaign_objective", "Traffic")
    result = run_check(row, evidence=_evidence({"objective": "OUTCOME_TRAFFIC"}))
    assert result.verdict == "Pass"


def test_run_check_unknown_id_still_errors() -> None:
    row = _row("not_a_real_check", "x")
    result = run_check(row, evidence=_evidence({}))
    assert result.verdict == "Error"
    assert "Unrecognized" in result.action
