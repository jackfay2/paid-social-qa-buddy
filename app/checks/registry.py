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
    check_campaign_bid_strategy,
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
    # Ad set / ad / text checks land as Kerri's check_ids lock and Riley/Nikki
    # add columns. Checks for fields not yet in BigQuery return Review until the
    # column lands. Organization (handoff §7.2): campaign / ad_set / ad / text.
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
