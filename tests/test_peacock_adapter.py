"""Unit tests for the Peacock special-case adapter + routing."""

from __future__ import annotations

from datetime import date
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


def test_get_ads_sorted_by_creative_id_for_reproducible_sampling() -> None:
    """Creatives come back in stable numeric-id order regardless of BQ row
    order, so the text-check 'first N' sample is reproducible run-to-run."""
    def row(cid):
        return {"creative_id": cid, "creative_name": "", "status": "Live", "copy": "x",
                "url": "u", "cta": "Sign Up", "adset_id": "as1", "adset_name": "AS1",
                "objective": "Acquisition", "buy_type": "Biddable", "campaign_name": "C"}
    ads = _client([row("100"), row("2"), row("30")]).get_ads("C22848672", CAMPAIGN)
    assert [a["id"] for a in ads] == ["2", "30", "100"]


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


# --- PeacockMetaClient trafficking merge (Phase B) -------------------------


def _perf_row(cid: str, dist=None, ver=None, adset: str = "as1") -> dict:
    return {
        "creative_id": cid, "creative_name": f"{cid}_name", "status": "Live",
        "copy": "Headline: H\nBody: B", "url": "https://www.peacocktv.com/x",
        "cta": "Sign Up", "adset_id": adset, "adset_name": "AS",
        "objective": "Acquisition", "buy_type": "Biddable", "campaign_name": "C",
        "distribution_id": dist, "version_number": ver,
    }


def _traf_row(dist=None, ver=None, frame: str = "1080x1920", show: str = "REALHOUS") -> dict:
    return {
        "distribution_id": dist, "version_number": ver, "frame_size": frame,
        "asset_type": "Video", "flight_start_date": date(2025, 11, 24),
        "flight_end_date": date(2026, 6, 30), "flight_window_flag": "All Clear",
        "trafficking_status": "Live", "confirmed_paused": False, "offer": None,
        "show_name": show, "genre": "Reality",
    }


def _job(rows: list[dict]) -> MagicMock:
    """A fake BQ query job whose .result() yields `rows`."""
    job = MagicMock()
    job.result.return_value = rows
    return job


def _client_traf(perf_rows: list[dict], traf_rows: list[dict]) -> PeacockMetaClient:
    """Mock BQ where the 1st query returns perf rows and the 2nd returns
    trafficking rows (matches get_ads' call order: _load then _trafficking_lookup)."""
    mock_bq = MagicMock()
    mock_bq.query.side_effect = [_job(perf_rows), _job(traf_rows)]
    return PeacockMetaClient(config=PeacockMetaConfig(billing_project="bill"), client=mock_bq)


def test_get_ads_merges_trafficking_by_distribution_id() -> None:
    client = _client_traf(
        [_perf_row("cr1", dist="231270"), _perf_row("cr2", dist="253713")],
        [_traf_row(dist="231270", frame="1080x1920", show="REALHOUS"),
         _traf_row(dist="253713", frame="1080x1080", show="LOVEISLA")],
    )
    by_id = {a["id"]: a for a in client.get_ads("C22848672", CAMPAIGN)}
    assert by_id["cr1"]["trafficking"]["frame_size"] == "1080x1920"
    assert by_id["cr1"]["trafficking"]["show"] == "REALHOUS"
    # DATE columns are ISO-stringified so the evidence stays JSON-clean.
    assert by_id["cr1"]["trafficking"]["flight_start_date"] == "2025-11-24"
    assert by_id["cr1"]["trafficking"]["flight_end_date"] == "2026-06-30"
    assert by_id["cr2"]["trafficking"]["frame_size"] == "1080x1080"


def test_get_ads_trafficking_version_fallback() -> None:
    """Blank distribution id -> join on version number (Pamela's documented
    fallback for un-reused historical creatives)."""
    client = _client_traf(
        [_perf_row("cr1", dist="", ver="v9")],
        [_traf_row(dist=None, ver="v9", frame="1080x1350")],
    )
    ads = client.get_ads("C22848672", CAMPAIGN)
    assert ads[0]["trafficking"]["frame_size"] == "1080x1350"


def test_get_ads_no_distribution_ids_skips_trafficking_query() -> None:
    """Perf rows with no distribution/version ids -> no second query, no
    trafficking sub-dict (clean Phase-A degrade)."""
    mock_bq = MagicMock()
    mock_bq.query.side_effect = [_job([_perf_row("cr1")])]  # only ONE query expected
    client = PeacockMetaClient(config=PeacockMetaConfig(billing_project="bill"), client=mock_bq)
    ads = client.get_ads("C22848672", CAMPAIGN)
    assert mock_bq.query.call_count == 1
    assert "trafficking" not in ads[0]


def test_get_ads_trafficking_query_failure_degrades_gracefully() -> None:
    """A trafficking query error must not break the run — ads come back perf-only."""
    mock_bq = MagicMock()
    mock_bq.query.side_effect = [_job([_perf_row("cr1", dist="231270")]), RuntimeError("permission denied")]
    client = PeacockMetaClient(config=PeacockMetaConfig(billing_project="bill"), client=mock_bq)
    ads = client.get_ads("C22848672", CAMPAIGN)
    assert len(ads) == 1
    assert "trafficking" not in ads[0]


def test_trafficking_lookup_is_cached() -> None:
    """get_ads twice -> perf + trafficking each queried once (both cached)."""
    client = _client_traf([_perf_row("cr1", dist="231270")], [_traf_row(dist="231270")])
    client.get_ads("C22848672", CAMPAIGN)
    client.get_ads("C22848672", CAMPAIGN)
    assert client._client.query.call_count == 2  # 1 perf + 1 trafficking, then cached


def test_trafficking_disabled_when_table_blank() -> None:
    """Blank trafficking_table -> merge skipped entirely (no second query)."""
    mock_bq = MagicMock()
    mock_bq.query.side_effect = [_job([_perf_row("cr1", dist="231270")])]
    cfg = PeacockMetaConfig(billing_project="bill", trafficking_table="")
    client = PeacockMetaClient(config=cfg, client=mock_bq)
    ads = client.get_ads("C22848672", CAMPAIGN)
    assert mock_bq.query.call_count == 1
    assert "trafficking" not in ads[0]
