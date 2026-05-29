from __future__ import annotations

from typing import Optional

from app.listener.entity_filter import EntityFilter
from app.listener.slack_models import SlackFieldError


def format_entity_label(entity_type: str, entity_id: str, name_lookup: dict[str, str]) -> str:
    """Return 'name (entity_type=id)' or '(entity_type=id)' if name missing."""
    name = name_lookup.get(entity_id, "")
    if name:
        return f"{name} ({entity_type}={entity_id})"
    return f"({entity_type}={entity_id})"


def format_filter_header(
    entity_filter: EntityFilter,
    display_names: dict[str, str],
) -> str:
    """Render 'Filter: campaigns=[...], ad_groups=[...], ads=[...]' header line."""

    def _labels(ids: tuple[str, ...], entity_type: str) -> str:
        items: list[str] = []
        for eid in ids:
            name = display_names.get(eid, "")
            if name:
                items.append(f"{name} ({eid})")
            else:
                items.append(eid)
        # Truncate at 10 items
        if len(items) > 10:
            shown = items[:10]
            return "[" + ", ".join(shown) + f" … (+{len(items) - 10} more)]"
        return "[" + ", ".join(items) + "]"

    parts: list[str] = []
    if entity_filter.campaign_ids:
        parts.append(f"campaigns={_labels(entity_filter.campaign_ids, 'campaign_id')}")
    if entity_filter.ad_group_ids:
        parts.append(f"ad_groups={_labels(entity_filter.ad_group_ids, 'ad_group_id')}")
    if entity_filter.ad_ids:
        parts.append(f"ads={_labels(entity_filter.ad_ids, 'ad_id')}")
    return "Filter: " + ", ".join(parts)


def format_accepted_message() -> str:
    return "QA request received. Validating details now."


def format_duplicate_warning_message() -> str:
    return (
        "A recent successful QA run already exists for this account_id and "
        "campaign_id. Reply `proceed` to run again, or `cancel`."
    )


def format_duplicate_cancelled_message() -> str:
    return "Cancelled. No new QA run was started for this request."


def format_filter_cancelled_message() -> str:
    return "🛑 QA run cancelled."


def format_filter_expired_message() -> str:
    return "⌛ Confirmation window expired — please submit a new QA request."


def format_duplicate_reminder_message() -> str:
    return "Please reply `proceed` to run again, or `cancel`."


def format_validation_errors_message(errors: list[SlackFieldError]) -> str:
    bullets = [
        f"- `{_display_field_name(error.field)}`: {_display_reason_text(error.reason)}"
        for error in errors
    ]
    bullet_text = "\n".join(bullets) if bullets else "- invalid input"
    return (
        "Unable to start QA. Please fix the following fields:\n"
        f"{bullet_text}\n\n"
        "Example format:\n"
        "```\n"
        "@QA Bot\n"
        "account_id: 123-456-7890\n"
        "campaign_id: 9876543210\n"
        "campaign_name: Brand Search (optional)\n"
        "template: standard (optional; defaults to standard)\n"
        "sheet_url: https://docs.google.com/spreadsheets/d/abc123/edit#gid=0\n"
        "```"
    )


def format_completed_message(
    *,
    request_id: str,
    customer_id: str,
    campaign_id: str,
    campaign_name: str,
    sheet_url: str,
    summary_counts: dict[str, int],
    entity_filter: Optional[EntityFilter] = None,
    display_names: Optional[dict[str, str]] = None,
) -> str:
    campaign_label = (campaign_name or "").strip() or "Campaign"
    counts = {
        "pass": int(summary_counts.get("pass", 0)),
        "fix": int(summary_counts.get("fix", 0)),
        "review": int(summary_counts.get("review", 0)),
        "na": int(summary_counts.get("na", 0)),
        "error": int(summary_counts.get("error", 0)),
    }
    lines: list[str] = []

    # Filter header for non-legacy submissions
    if entity_filter and not entity_filter.is_legacy_single_campaign():
        lines.append(format_filter_header(entity_filter, display_names or {}))

    lines.append(
        f"QA completed for {campaign_label} "
        f"(account_id={customer_id}, campaign_id={campaign_id})"
    )
    lines.append(
        "Summary: "
        f"Pass {counts['pass']} | "
        f"Fix {counts['fix']} | "
        f"Review {counts['review']} | "
        f"N/A {counts['na']} | "
        f"Error {counts['error']}"
    )
    if counts["error"] > 0:
        lines.append(
            "Some checks returned Error. Review the sheet Action column for follow-up."
        )
    if sheet_url:
        lines.append(f"Sheet: {sheet_url}")
    lines.append(f"request_id: {(request_id or '').strip() or 'unknown'}")
    return "\n".join(lines)


def format_execution_error_message(
    *,
    request_id: str,
    error_code: str,
    status: str,
) -> str:
    reason = _error_reason(error_code)
    status_label = "rejected" if status == "rejected" else "failed"
    return (
        f"QA run {status_label}. {reason}\n"
        "Please fix the issue and reply `retry` in this thread.\n"
        f"request_id: {(request_id or '').strip() or 'unknown'}"
    )


def format_unknown_template_family_message(
    *,
    submitted_value: str,
    valid_options: list[str],
) -> str:
    options = ", ".join(sorted({opt for opt in valid_options if opt})) or "standard"
    submitted = (submitted_value or "").strip() or "(empty)"
    return (
        "Unable to start QA. The `template` value is not recognized.\n"
        f"- Submitted: `{submitted}`\n"
        f"- Valid options: {options}\n"
        "Leave `template:` off (or omit the value) to use `standard`."
    )


def _error_reason(error_code: str) -> str:
    code = (error_code or "").strip().lower()
    mapping = {
        "missing_field": "A required field was missing.",
        "invalid_customer_id": "Account ID format is invalid.",
        "invalid_campaign_id": "Campaign ID must be numeric.",
        "invalid_sheet_url": "Sheet URL is invalid.",
        "sheet_permission_denied": "The bot cannot access the sheet.",
        "sheet_not_found": "The sheet could not be found.",
        "sheet_tab_not_found": "The configured worksheet tab was not found.",
        "sheet_parse_error": "The sheet format could not be parsed.",
        "sheet_template_invalid": "This sheet template is not supported for QA Buddy Bot (missing required Check ID schema).",
        "sheet_inaccessible": "The sheet is not accessible.",
        "campaign_not_found": "Campaign could not be resolved for this account.",
        "customer_not_accessible": "Customer account is not accessible.",
        "ads_auth_failed": "Google Ads credentials are not valid.",
        "campaign_name_ambiguous": "Multiple campaigns matched this campaign name. Provide campaign_id.",
        "campaign_name_mismatch": "Campaign ID and campaign name do not refer to the same campaign.",
        "missing_campaign_selector": "Either campaign_id or campaign_name is required.",
        "no_route_match": "No MCC route matches this account and QA lane.",
        "ambiguous_route_match": "Multiple MCC routes match this account and QA lane.",
        "unsupported_route_dimensions": "Matched MCC route uses unsupported route dimensions for this QA phase.",
        "route_context_mismatch": "Route context did not match worker-side MCC revalidation.",
        "unknown_template_family": "The submitted template value is not recognized.",
        "ads_rate_limited": "Google Ads API rate limit was reached. Please retry shortly.",
        "ads_query_timeout": "Google Ads query timed out. Please retry.",
        "internal_error": "An internal processing error occurred.",
    }
    return mapping.get(code, "The request could not be completed.")


def _display_field_name(field: str) -> str:
    normalized = str(field or "").strip()
    if normalized == "customer_id":
        return "account_id"
    return normalized


def _display_reason_text(reason: str) -> str:
    text = str(reason or "")
    return text.replace("Customer ID", "Account ID").replace(
        "customer_id", "account_id"
    )
