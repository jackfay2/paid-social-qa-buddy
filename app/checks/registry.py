"""Check registry. Direct dict lookup keyed by check_id. No fuzzy matching.

Unknown check_id values surface as `Error: Unrecognized check_id` rather than
being guessed at. This is the contract between the QA sheet's column A and the
worker's check functions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.models import CheckResult, CheckRow

CheckFunction = Callable[..., CheckResult]

_logger = logging.getLogger("paid_social_qa_buddy.registry")

CHECK_REGISTRY: dict[str, CheckFunction] = {
    # Populated as check modules land. Pending Carrie's locked check_id list.
    # Suggested organization (per the original handoff §7.2):
    #   campaign/*   — bid_strategy, budget, optimization_event, objective, dates, special_ad_categories
    #   ad_set/*     — audience, placements, age, geo, schedule
    #   ad/*         — creative_format, headlines, primary_text, CTA, landing_url, UTM
    #   text/*       — spellcheck, capitalization, promo_language, fair_housing (Gemini-batched)
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
