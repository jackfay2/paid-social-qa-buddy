"""Unit tests for the Peacock special-case adapter + routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.peacock import (
    InvalidCampaignIdError,
    PeacockMetaClient,
    PeacockMetaConfig,
    RoutingMetaClient,
    split_final_copy,
)

CAMPAIGN = "120215246378710260"


# --- split_final_copy ------------------------------------------------------


def test_split_final_copy_headline_and_body() -> None:
    assert split_final_copy("Headline: Only on Peacock\nBody: Watch the drama now") == (
        "Only on Peacock",
        "Watch the drama now",
    )


def test_split_final_copy_body_only() -> None:
    assert split_final_copy("Body: just the body") == ("", "just the body")


def test_split_final_copy_no_markers_is_all_body() -> None:
    assert split_final_copy("Stream it now on Peacock") == ("", "Stream it now on Peacock")


def test_split_final_copy_empty() -> None:
    assert split_final_copy("") == ("", "")
    assert split_final_copy(None) == ("", "")


# --- PeacockMetaClient (mocked BigQuery) -----------------------------------


def _rows() -> list[dict]:
    return [
        {
            "creative_id": "cr1",
            "creative_name": "2501_video_trailer",
            "status": "Live",
            "copy": "Headline: Only on Peacock\nBody: The gripping new drama",
            "url": "https://www.peacocktv.com/stream-tv/show?utm_source=fb",
            "cta": "Sign Up",
            "adset_id": "as1",
            "adset_name": "Peacock_FBIG_ACQ",
            "objective": "Acquisition",
            "buy_type": "Biddable",
            "campaign_name": "Peacock_ACQ_Conversions",
        },
        {
            "creative_id": "cr2",
            "creative_name": "2501_image_static",
            "status": "Paused",
            "copy": "Stream it now",  # no markers -> all body
            "url": "https://www.peacocktv.com/x",
            "cta": "Watch More",
            "adset_id": "as1",  # same ad set
            "adset_name": "Peacock_FBIG_ACQ",
            "objective": "Acquisition",
            "buy_type": "Biddable",
            "campaign_name": "Peacock_ACQ_Conversions",
        },
    ]


def _client(rows: list[dict]) -> PeacockMetaClient:
    mock_bq = MagicMock()
    mock_bq.query.return_value.result.return_value = rows
    return PeacockMetaClient(config=PeacockMetaConfig(billing_project="bill"), client=mock_bq)


def test_get_campaign_shapes_evidence() -> None:
    c = _client(_rows()).get_campaign("C22848672", CAMPAIGN)
    assert c["objective"] == "Acquisition"
    assert c["buying_type"] == "Biddable"
    assert c["name"] == "Peacock_ACQ_Conversions"
    assert c["campaign_id"] == CAMPAIGN


def test_get_campaign_empty_when_no_rows() -> None:
    assert _client([]).get_campaign("C22848672", CAMPAIGN) == {}


def test_get_ad_sets_dedups() -> None:
    ad_sets = _client(_rows()).get_ad_sets("C22848672", CAMPAIGN)
    assert len(ad_sets) == 1  # both creatives share as1
    assert ad_sets[0]["adset_id"] == "as1"
    assert ad_sets[0]["name"] == "Peacock_FBIG_ACQ"


def test_get_ads_shapes_creative_and_splits_copy() -> None:
    ads = _client(_rows()).get_ads("C22848672", CAMPAIGN)
    assert len(ads) == 2
    a = ads[0]
    assert a["id"] == "cr1"
    assert a["effective_status"] == "Live"
    assert a["creative"]["title"] == "Only on Peacock"   # headline split
    assert a["creative"]["body"] == "The gripping new drama"
    assert a["creative"]["link_url"].startswith("https://www.peacocktv.com")
    assert a["creative"]["call_to_action_type"] == "Sign Up"
    # creative with no markers -> body holds the whole string
    assert ads[1]["creative"]["body"] == "Stream it now"
    assert ads[1]["creative"]["title"] == ""


def test_load_is_cached_within_instance() -> None:
    client = _client(_rows())
    client.get_campaign("C22848672", CAMPAIGN)
    client.get_ads("C22848672", CAMPAIGN)
    client.get_ad_sets("C22848672", CAMPAIGN)
    assert client._client.query.call_count == 1  # one query feeds all three


def test_invalid_campaign_id_raises() -> None:
    with pytest.raises(InvalidCampaignIdError):
        _client(_rows()).get_campaign("C22848672", "not-numeric")


# --- RoutingMetaClient -----------------------------------------------------


class _Stub:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def get_campaign(self, client_id, campaign_id):
        return {"tag": self.tag}

    def get_ad_sets(self, client_id, campaign_id):
        return [{"tag": self.tag}]

    def get_ads(self, client_id, campaign_id):
        return [{"tag": self.tag}]


def test_routing_sends_peacock_to_override_else_default() -> None:
    router = RoutingMetaClient(default=_Stub("standard"), overrides={"C22848672": _Stub("peacock")})
    assert router.get_campaign("C22848672", CAMPAIGN)["tag"] == "peacock"
    assert router.get_ads("C22848672", CAMPAIGN)[0]["tag"] == "peacock"
    # any other client_id -> default
    assert router.get_campaign("C61854560", "123")["tag"] == "standard"
    assert router.get_ad_sets("C00030334", "123")[0]["tag"] == "standard"


def test_routing_empty_overrides_all_default() -> None:
    router = RoutingMetaClient(default=_Stub("standard"))
    assert router.get_campaign("C22848672", CAMPAIGN)["tag"] == "standard"
