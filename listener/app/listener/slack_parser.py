"""Slack message parser for QA Buddy Bot.

Supported entity filter keys (comma-separated lists accepted):
  campaign_id:   Numeric Google Ads campaign IDs        (Shape A=single, B=multi)
  campaign_name: Campaign display names (positional)    (aligns with campaign_id)
  ad_group_id:   Numeric ad-group IDs                   (Shape C=single, D=multi)
  ad_id:         Numeric ad IDs                         (Shape E=ad-only, F=campaign+ads)

Shape taxonomy:
  A — single campaign (legacy)
  B — multi-campaign
  C — single campaign + single ad_group
  D — single campaign + multi ad_groups
  E — ad-only (no campaign_id; parents auto-resolved later)
  F — single campaign + multi ads
"""

import os
import re
from urllib.parse import urlparse

from app.listener.entity_filter import EntityFilter
from app.listener.slack_models import (
    SlackFieldError,
    SlackParsedRequest,
    SlackValidationResult,
)

_CUSTOMER_ID_PATTERN = re.compile(r"^\d{10}$")
# SOCIAL ADDITION: Meta account IDs are ~15-17 digits, not Google's 10. For a
# request from a configured social channel, accept the Meta length; Search
# channels keep the strict 10-digit rule (byte-identical to Maya's).
# Lower bound is 11 (not 8) so we DON'T accept implausibly short ids and, more
# importantly, don't overlap Google's exact 10-digit shape — a Google customer_id
# pasted in a social channel should be rejected, not silently treated as a Meta id.
_SOCIAL_CUSTOMER_ID_PATTERN = re.compile(r"^\d{11,18}$")
_CAMPAIGN_ID_PATTERN = re.compile(r"^\d+$")


def _social_channel_ids() -> set[str]:
    raw = os.environ.get("SOCIAL_CHANNEL_IDS", "")
    return {c.strip() for c in raw.split(",") if c.strip()}
_SHEETS_PATH_PATTERN = re.compile(r"^/spreadsheets/(?:u/\d+/)?d/[^/]+")
_MENTION_PATTERN = re.compile(r"<@[^>]+>")
_KEY_VALUE_SPLIT_PATTERN = re.compile(r"\s*[:=]\s*", re.IGNORECASE)
_LINE_PREFIX_PATTERN = re.compile(r"^[\-•*\s]+")
_NORMALIZE_KEY_PATTERN = re.compile(r"[^a-z0-9]+")
_SLACK_LINK_TOKEN_PATTERN = re.compile(r"^<([^>|]+)(?:\|([^>]*))?>$")

_FIELD_ALIASES = {
    "sheeturl": "sheet_url",
    "sheet_url": "sheet_url",
    "sheet": "sheet_url",
    "customerid": "customer_id",
    "customer_id": "customer_id",
    "customer": "customer_id",
    "cid": "customer_id",
    "accountid": "customer_id",
    "account_id": "customer_id",
    "campaignid": "campaign_id",
    "campaign_id": "campaign_id",
    "campaignname": "campaign_name",
    "campaign_name": "campaign_name",
    "adgroupid": "ad_group_id",
    "ad_group_id": "ad_group_id",
    "adgroup_id": "ad_group_id",
    "adgroup": "ad_group_id",
    "adid": "ad_id",
    "ad_id": "ad_id",
    "template": "template_family",
    "templatefamily": "template_family",
    "template_family": "template_family",
}


def parse_and_validate_slack_request(
    *,
    text: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
) -> SlackValidationResult:
    fields = _parse_fields(text)
    errors: list[SlackFieldError] = []

    sheet_url = fields.get("sheet_url", "").strip()
    if not sheet_url:
        errors.append(
            SlackFieldError(field="sheet_url", reason="Google Sheets URL is required.")
        )
    elif not _is_valid_google_sheet_url(sheet_url):
        errors.append(
            SlackFieldError(
                field="sheet_url",
                reason=(
                    "Google Sheets URL is invalid. Use a URL like "
                    "https://docs.google.com/spreadsheets/d/<id>/edit."
                ),
            )
        )

    customer_id_raw = fields.get("customer_id", "").strip()
    if not customer_id_raw:
        errors.append(
            SlackFieldError(field="customer_id", reason="Customer ID is required.")
        )
        normalized_customer_id = ""
    else:
        normalized_customer_id = normalize_customer_id(customer_id_raw)
        # SOCIAL ADDITION: relax to the Meta account-id length on social channels.
        _is_social = channel_id in _social_channel_ids()
        _pattern = _SOCIAL_CUSTOMER_ID_PATTERN if _is_social else _CUSTOMER_ID_PATTERN
        if not _pattern.match(normalized_customer_id):
            errors.append(
                SlackFieldError(
                    field="customer_id",
                    reason=(
                        "Account ID is invalid (expected a numeric Meta account id)."
                        if _is_social
                        else "Customer ID is invalid. Use 10 digits (dashes optional)."
                    ),
                )
            )

    campaign_id = fields.get("campaign_id", "").strip()
    campaign_name = fields.get("campaign_name", "").strip()
    ad_group_id = fields.get("ad_group_id", "").strip()
    ad_id = fields.get("ad_id", "").strip()

    template_family_raw = fields.get("template_family", "").strip().lower()
    template_family = template_family_raw or None

    # Require at least one entity identifier (campaign_name alone is not sufficient)
    if not campaign_id and not ad_group_id and not ad_id:
        errors.append(
            SlackFieldError(
                field="campaign_id",
                reason="Provide campaign_id, ad_group_id, or ad_id.",
            )
        )

    # Validate each individual token in comma-separated lists
    if campaign_id:
        for token in campaign_id.split(","):
            token = token.strip()
            if token and not _CAMPAIGN_ID_PATTERN.match(token):
                errors.append(
                    SlackFieldError(
                        field="campaign_id",
                        reason=f"Campaign ID '{token}' looks malformed (unsupported characters).",
                    )
                )
                break

    if ad_group_id:
        for token in ad_group_id.split(","):
            token = token.strip()
            if token and not _CAMPAIGN_ID_PATTERN.match(token):
                errors.append(
                    SlackFieldError(
                        field="ad_group_id",
                        reason=f"Ad group ID '{token}' looks malformed (unsupported characters).",
                    )
                )
                break

    if ad_id:
        for token in ad_id.split(","):
            token = token.strip()
            if token and not _CAMPAIGN_ID_PATTERN.match(token):
                errors.append(
                    SlackFieldError(
                        field="ad_id",
                        reason=f"Ad ID '{token}' looks malformed (unsupported characters).",
                    )
                )
                break

    if errors:
        return SlackValidationResult(accepted=False, errors=errors)

    # Build EntityFilter — validate shape
    try:
        entity_filter = EntityFilter.from_raw_strings(
            campaign_id, campaign_name, ad_group_id, ad_id
        )
        entity_filter.validate_shape()
    except ValueError as exc:
        errors.append(
            SlackFieldError(
                field="entity_filter",
                reason=f"❌ Invalid filter: {exc}. Please split into separate submissions.",
            )
        )
        return SlackValidationResult(accepted=False, errors=errors)

    return SlackValidationResult(
        accepted=True,
        request=SlackParsedRequest(
            sheet_url=sheet_url,
            customer_id=normalized_customer_id,
            entity_filter=entity_filter,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            raw_text=text,
            template_family=template_family,
        ),
    )


def normalize_customer_id(customer_id: str) -> str:
    return "".join(char for char in customer_id if char.isdigit())


def _parse_fields(text: str) -> dict[str, str]:
    sanitized = _MENTION_PATTERN.sub(" ", text or "")
    fields: dict[str, str] = {}
    for raw_line in sanitized.splitlines():
        line = _LINE_PREFIX_PATTERN.sub("", raw_line.strip())
        if not line:
            continue
        parts = _KEY_VALUE_SPLIT_PATTERN.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        raw_key, raw_value = parts[0].strip(), parts[1].strip()
        if not raw_key:
            continue
        normalized_key = _normalize_field_key(raw_key)
        canonical_key = _FIELD_ALIASES.get(normalized_key)
        if not canonical_key or not raw_value:
            continue
        fields[canonical_key] = _normalize_field_value(canonical_key, raw_value)
    return fields


def _normalize_field_key(key: str) -> str:
    lowered = key.strip().lower()
    return _NORMALIZE_KEY_PATTERN.sub("", lowered)


def _is_valid_google_sheet_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc.lower() != "docs.google.com":
        return False
    return bool(_SHEETS_PATH_PATTERN.match(parsed.path))


def _normalize_field_value(field: str, raw_value: str) -> str:
    value = raw_value.strip()
    token_match = _SLACK_LINK_TOKEN_PATTERN.match(value)
    if not token_match:
        return value

    target = token_match.group(1).strip()
    label = (token_match.group(2) or "").strip()

    if field == "sheet_url":
        return target

    if field == "customer_id":
        if target.lower().startswith("tel:"):
            tel_value = target[4:]
            return label or tel_value
        return label or target

    return label or target
