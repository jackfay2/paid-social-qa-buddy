"""Request/response models for the worker HTTP endpoint."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class SocialTaskRequest(BaseModel):
    """The Cloud Tasks payload the listener enqueues for a Social QA run.

    Accepts `account_id` or the legacy `customer_id` field name (the existing
    Search envelope uses `customer_id`); both map to account_id here. Numeric
    IDs are coerced to strings so a JSON integer account_id parses cleanly.
    """

    model_config = ConfigDict(coerce_numbers_to_str=True, populate_by_name=True)

    @field_validator("*", mode="before")
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        """A JSON null for any string field would otherwise 422 at validation
        (before the handler runs), bypassing the graceful "missing field" reject
        that posts a user-visible message to Slack. Collapse null → "" so a null
        id flows into that same reject path instead of a silent 422."""
        return "" if value is None else value

    request_id: str = ""
    channel_id: str = ""
    thread_ts: str = ""
    sheet_url: str = ""
    account_id: str = Field(
        default="",
        validation_alias=AliasChoices("account_id", "customer_id"),
    )
    campaign_id: str = ""
    campaign_name: str = ""
    qa_app: str = "social"


class SocialTaskResponse(BaseModel):
    status: str
    message: str = ""
    run_id: str = ""
    request_id: str = ""
    error_code: str = ""
