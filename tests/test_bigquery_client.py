"""Unit tests for BigQueryMetaClient.

Uses a mocked bigquery.Client so the tests don't hit live BQ.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.bigquery.client import (
    BigQueryMetaClient,
    BigQueryMetaConfig,
    InvalidCampaignIdError,
    InvalidClientIdError,
)


def _make_client(mock_bq: MagicMock) -> BigQueryMetaClient:
    return BigQueryMetaClient(
        config=BigQueryMetaConfig(project="test-project"),
        client=mock_bq,
    )


def _set_rows(mock_bq: MagicMock, rows: list[dict]) -> None:
    """Configure the mock to return the given rows from query().result()."""
    mock_bq.query.return_value.result.return_value = rows


# --- ID validation ---------------------------------------------------------


def test_rejects_malformed_client_id() -> None:
    client = _make_client(MagicMock())
    with pytest.raises(InvalidClientIdError):
        client.get_campaign(client_id="not-a-client-id", campaign_id="123")


def test_rejects_empty_client_id() -> None:
    client = _make_client(MagicMock())
    with pytest.raises(InvalidClientIdError):
        client.get_campaign(client_id="", campaign_id="123")


def test_rejects_sql_injection_attempt_in_client_id() -> None:
    """Defense in depth: client_id is interpolated into SQL as the dataset name."""
    client = _make_client(MagicMock())
    with pytest.raises(InvalidClientIdError):
        client.get_campaign(
            client_id="C00030334`; DROP TABLE x;--",
            campaign_id="123",
        )


def test_rejects_non_numeric_campaign_id() -> None:
    client = _make_client(MagicMock())
    with pytest.raises(InvalidCampaignIdError):
        client.get_campaign(client_id="C00030334", campaign_id="abc")


def test_accepts_well_formed_ids() -> None:
    mock_bq = MagicMock()
    _set_rows(mock_bq, [])
    client = _make_client(mock_bq)
    # Should not raise.
    client.get_campaign(client_id="C00030334", campaign_id="9876543210")


# --- get_campaign ----------------------------------------------------------


def test_get_campaign_returns_first_row_as_dict() -> None:
    mock_bq = MagicMock()
    row = {"id": 123, "objective": "OUTCOME_TRAFFIC", "buying_type": "AUCTION"}
    _set_rows(mock_bq, [row])

    client = _make_client(mock_bq)
    result = client.get_campaign(client_id="C00030334", campaign_id="123")

    assert result == row
    mock_bq.query.assert_called_once()


def test_get_campaign_returns_empty_dict_when_no_rows() -> None:
    mock_bq = MagicMock()
    _set_rows(mock_bq, [])

    client = _make_client(mock_bq)
    result = client.get_campaign(client_id="C00030334", campaign_id="999")

    assert result == {}


# --- get_ad_sets -----------------------------------------------------------


def test_get_ad_sets_returns_list_of_dicts() -> None:
    mock_bq = MagicMock()
    _set_rows(
        mock_bq,
        [
            {"id": 1, "name": "Set A", "age_min": 18, "age_max": 65},
            {"id": 2, "name": "Set B", "age_min": 25, "age_max": 45},
        ],
    )

    client = _make_client(mock_bq)
    result = client.get_ad_sets(client_id="C00030334", campaign_id="123")

    assert len(result) == 2
    assert result[0]["name"] == "Set A"
    assert result[1]["age_min"] == 25


def test_get_ad_sets_returns_empty_list_when_no_rows() -> None:
    mock_bq = MagicMock()
    _set_rows(mock_bq, [])

    client = _make_client(mock_bq)
    result = client.get_ad_sets(client_id="C00030334", campaign_id="123")

    assert result == []


# --- get_ads ---------------------------------------------------------------


def test_get_ads_returns_list_of_dicts() -> None:
    mock_bq = MagicMock()
    _set_rows(
        mock_bq,
        [
            {
                "id": 10,
                "name": "Ad 1",
                "creative_title": "Big Sale",
                "creative_body": "Shop today",
                "creative_cta": "SHOP_NOW",
            },
        ],
    )

    client = _make_client(mock_bq)
    result = client.get_ads(client_id="C00030334", campaign_id="123")

    assert len(result) == 1
    assert result[0]["creative_title"] == "Big Sale"


# --- per-job caching -------------------------------------------------------


def test_get_campaign_caches_within_instance() -> None:
    mock_bq = MagicMock()
    _set_rows(mock_bq, [{"id": 1}])

    client = _make_client(mock_bq)
    client.get_campaign(client_id="C00030334", campaign_id="123")
    client.get_campaign(client_id="C00030334", campaign_id="123")

    assert mock_bq.query.call_count == 1


def test_cache_keys_separate_kinds() -> None:
    """get_campaign and get_ad_sets for the same (client, campaign) hit the
    database separately, since they're different kinds of data."""
    mock_bq = MagicMock()
    _set_rows(mock_bq, [{"id": 1}])

    client = _make_client(mock_bq)
    client.get_campaign(client_id="C00030334", campaign_id="123")
    client.get_ad_sets(client_id="C00030334", campaign_id="123")

    assert mock_bq.query.call_count == 2


def test_cache_keys_separate_campaigns() -> None:
    """Different campaign_ids on the same client don't share cache entries."""
    mock_bq = MagicMock()
    _set_rows(mock_bq, [{"id": 1}])

    client = _make_client(mock_bq)
    client.get_campaign(client_id="C00030334", campaign_id="111")
    client.get_campaign(client_id="C00030334", campaign_id="222")

    assert mock_bq.query.call_count == 2
