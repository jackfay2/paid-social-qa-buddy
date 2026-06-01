"""Social-delta tests for the parser: the 10-digit account_id rule is relaxed
to Meta's ~17-digit length on social channels; Search channels stay strict.

Run from listener/:  cd listener && pytest
"""

from __future__ import annotations

from app.listener.slack_parser import parse_and_validate_slack_request

_SHEET = "https://docs.google.com/spreadsheets/d/1b8hp0UFjLyMgJs4G5inNME3Vyg4Cn9Etq7kjIitt4c8/edit"


def _text(account_id: str) -> str:
    return (
        f"<@U0B3EJ7PZ5Z>\n"
        f"account_id: {account_id}\n"
        f"campaign_id: 6065738140956\n"
        f"campaign_name: Test\n"
        f"sheet_url: {_SHEET}\n"
    )


def test_meta_17_digit_account_accepted_on_social_channel(monkeypatch) -> None:
    monkeypatch.setenv("SOCIAL_CHANNEL_IDS", "C0B6ASW9R9V")
    result = parse_and_validate_slack_request(
        text=_text("10152426494631116"), channel_id="C0B6ASW9R9V",
        thread_ts="1.2", user_id="U1",
    )
    assert result.accepted, result.errors
    assert result.request.customer_id == "10152426494631116"


def test_meta_17_digit_rejected_on_non_social_channel(monkeypatch) -> None:
    """Same long id on a Search channel still fails the 10-digit rule."""
    monkeypatch.setenv("SOCIAL_CHANNEL_IDS", "C0B6ASW9R9V")
    result = parse_and_validate_slack_request(
        text=_text("10152426494631116"), channel_id="C_SEARCH",
        thread_ts="1.2", user_id="U1",
    )
    assert not result.accepted
    assert any(e.field == "customer_id" for e in result.errors)


def test_google_10_digit_still_accepted_on_search_channel(monkeypatch) -> None:
    monkeypatch.setenv("SOCIAL_CHANNEL_IDS", "C0B6ASW9R9V")
    result = parse_and_validate_slack_request(
        text=_text("1234567890"), channel_id="C_SEARCH",
        thread_ts="1.2", user_id="U1",
    )
    assert result.accepted, result.errors
