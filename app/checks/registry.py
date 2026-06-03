"""Check registry. Direct dict lookup keyed by check_id. No fuzzy matching.

Unknown check_id values surface as `Error: Unrecognized check_id` rather than
being guessed at. This is the contract between the QA sheet's column A and the
worker's check functions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.checks.meta_checks import (
    check_ad_call_to_action,
    check_ad_count,
    check_ad_creative_dimensions,
    check_ad_destination_url,
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
from app.models import CheckResult, CheckRow

CheckFunction = Callable[..., CheckResult]

_logger = logging.getLogger("paid_social_qa_buddy.registry")

CHECK_REGISTRY: dict[str, CheckFunction] = {
    # Campaign level (BigQuery-backed, fields confirmed present on rich clients).
    "campaign_objective": check_campaign_objective,
    "campaign_buying_type": check_campaign_buying_type,
    "campaign_status": check_campaign_status,
    "campaign_start_date": check_campaign_start_date,
    "campaign_bid_strategy": check_campaign_bid_strategy,
    "campaign_budget": check_campaign_budget,
    # Ad set level. Targeting fields (age_*, genders, countries) read via
    # _targeting.read_targeting so nested vs flat schemas both work.
    "adset_status": check_adset_status,
    "adset_start_date": check_adset_start_date,
    "adset_end_date": check_adset_end_date,
    "adset_age_min": check_adset_age_min,
    "adset_age_max": check_adset_age_max,
    "adset_genders": check_adset_genders,
    "adset_countries": check_adset_countries,
    # The Peacock-Olympics check: optimization/conversion event. Strict match,
    # Review on any ambiguity — never a silent Pass on a near-match.
    "adset_conversion_event": check_adset_conversion_event,
    # Conversion config (shapes confirmed against live BQ): attribution_spec
    # (list of {event_type, window_days}) and optimization_goal (string enum).
    "adset_attribution_setting": check_adset_attribution_setting,
    "adset_optimization_goal": check_adset_optimization_goal,
    # Bidirectional presence checks (Brandon 2026-06-01): builder Yes → must be
    # present; even on No/blank, still verify it's NOT accidentally included.
    # Always-run (see ALWAYS_RUN_CHECK_IDS in pipeline).
    "adset_spend_minimum": check_adset_spend_minimum,
    "adset_spend_maximum": check_adset_spend_maximum,
    "adset_audiences": check_adset_audiences,
    "adset_audience_exclusions": check_adset_audience_exclusions,
    # Ad level. Destination URL read defensively across several BQ schemas
    # (link_url, destination_url, creative.link_url, object_story_spec.link).
    "ad_status": check_ad_status,
    "ad_count": check_ad_count,
    "ad_destination_url": check_ad_destination_url,
    "ad_call_to_action": check_ad_call_to_action,
    # Creative dimensions: manual Review for standard clients (see
    # ALWAYS_REVIEW_CHECK_ACTIONS), but deterministic in Peacock mode where the
    # trafficking table carries Frame_Size (the pipeline routes Peacock runs here
    # instead of the manual note — see PEACOCK_DETERMINISTIC_CHECK_IDS).
    "ad_creative_dimensions": check_ad_creative_dimensions,
    # Text checks (Gemini): defined in app/checks/text_checks.py, NOT here.
    # The pipeline skips text-check rows in execute_checks and routes them
    # through execute_text_checks. Adding a text check is a TEXT_CHECK_DEFINITIONS
    # entry, not a CHECK_REGISTRY entry.
}


def run_check(row: CheckRow, evidence: dict[str, Any] | None = None) -> CheckResult:
    check_fn = CHECK_REGISTRY.get(row.check_id)
    if check_fn is None:
        _logger.warning(
            "unknown_check_id",
            extra={"check_id": row.check_id, "row_index": row.row_index},
        )
        return CheckResult(
            row_index=row.row_index,
            check_id=row.check_id,
            verdict="Error",
            action=f"Unrecognized check_id: {row.check_id}",
            builder_input=row.builder_input,
            builder_notes=row.builder_notes,
        )

    try:
        try:
            return check_fn(row, evidence=evidence)
        except TypeError:
            return check_fn(row)
    except Exception as exc:
        return CheckResult(
            row_index=row.row_index,
            check_id=row.check_id,
            verdict="Error",
            action=f"Check execution failed: {exc}",
            builder_input=row.builder_input,
            builder_notes=row.builder_notes,
        )
