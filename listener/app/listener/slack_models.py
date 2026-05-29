from __future__ import annotations

from dataclasses import dataclass, field

from app.listener.entity_filter import EntityFilter


@dataclass(frozen=True)
class SlackFieldError:
    field: str
    reason: str


@dataclass(frozen=True)
class SlackParsedRequest:
    sheet_url: str
    customer_id: str
    entity_filter: EntityFilter
    channel_id: str
    thread_ts: str
    user_id: str
    raw_text: str
    template_family: str | None = None

    @property
    def campaign_id(self) -> str:
        """Backward-compat: return single campaign_id for legacy callers."""
        ef = self.entity_filter
        if (
            len(ef.campaign_ids) == 1
            and not ef.ad_group_ids
            and not ef.ad_ids
        ):
            return ef.campaign_ids[0]
        # Multi-id or ad-group/ad filter: return first campaign or empty
        return ef.campaign_ids[0] if ef.campaign_ids else ""

    @property
    def campaign_name(self) -> str:
        """Backward-compat: return single campaign_name for legacy callers."""
        ef = self.entity_filter
        if len(ef.campaign_names) == 1:
            return ef.campaign_names[0]
        return ef.campaign_names[0] if ef.campaign_names else ""


@dataclass(frozen=True)
class SlackValidationResult:
    accepted: bool
    request: SlackParsedRequest | None = None
    errors: list[SlackFieldError] = field(default_factory=list)


@dataclass(frozen=True)
class SlackSubmitResult:
    outcome: str
    message: str = ""
    request_id: str = ""
