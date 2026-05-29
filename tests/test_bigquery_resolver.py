"""Unit tests for BigQueryAccountResolver, using a mocked bigquery.Client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.bigquery.resolver import (
    AmbiguousAccountError,
    BigQueryAccountResolver,
    InvalidAccountIdError,
    ResolverConfig,
)


# --- billing_project vs data project (the Cloud Run jobUser fix) ------------


def test_billing_project_used_for_bq_client_when_set() -> None:
    """Jobs must run in billing_project (where the SA has jobUser), not the
    data-warehouse project (where it only has dataViewer)."""
    with patch("app.adapters.bigquery.resolver.bigquery.Client") as MockClient:
        BigQueryAccountResolver(
            config=ResolverConfig(project="data-proj", billing_project="bill-proj")
        )
        MockClient.assert_called_once_with(project="bill-proj")


def test_bq_client_falls_back_to_data_project_when_billing_blank() -> None:
    with patch("app.adapters.bigquery.resolver.bigquery.Client") as MockClient:
        BigQueryAccountResolver(config=ResolverConfig(project="data-proj"))
        MockClient.assert_called_once_with(project="data-proj")


def test_table_namespace_uses_data_project_not_billing() -> None:
    """Even with a separate billing project, the query must reference the data
    warehouse's table (config.project), never the billing project."""
    mock_bq = MagicMock()
    mock_bq.query.return_value.result.return_value = [{"client_id": "C1"}]
    resolver = BigQueryAccountResolver(
        config=ResolverConfig(project="data-proj", billing_project="bill-proj"),
        client=mock_bq,
    )
    resolver.resolve_client_id("123456789")
    query = mock_bq.query.call_args[0][0]
    assert "data-proj.summary" in query
    assert "bill-proj" not in query


def _make_resolver(rows: list[dict]) -> BigQueryAccountResolver:
    mock_bq = MagicMock()
    mock_bq.query.return_value.result.return_value = rows
    return BigQueryAccountResolver(
        config=ResolverConfig(project="test-project"),
        client=mock_bq,
    )


def test_rejects_non_numeric_account_id() -> None:
    resolver = _make_resolver([])
    with pytest.raises(InvalidAccountIdError):
        resolver.resolve_client_id("not-a-number")


def test_rejects_empty_account_id() -> None:
    resolver = _make_resolver([])
    with pytest.raises(InvalidAccountIdError):
        resolver.resolve_client_id("")


def test_resolves_single_client_id() -> None:
    resolver = _make_resolver([{"client_id": "C00030334"}])
    assert resolver.resolve_client_id("123456789") == "C00030334"


def test_returns_none_when_account_not_found() -> None:
    resolver = _make_resolver([])
    assert resolver.resolve_client_id("999999999") is None


def test_raises_on_ambiguous_account() -> None:
    resolver = _make_resolver(
        [{"client_id": "C00000001"}, {"client_id": "C00000002"}]
    )
    with pytest.raises(AmbiguousAccountError) as excinfo:
        resolver.resolve_client_id("123456789")
    assert set(excinfo.value.client_ids) == {"C00000001", "C00000002"}


def test_collapses_duplicate_client_ids() -> None:
    """Performance table has many rows per account (per day/country); the same
    client_id appearing repeatedly should resolve cleanly to one."""
    resolver = _make_resolver(
        [{"client_id": "C00030334"}, {"client_id": "C00030334"}]
    )
    assert resolver.resolve_client_id("123456789") == "C00030334"


def test_filters_out_null_and_empty_client_ids() -> None:
    resolver = _make_resolver(
        [{"client_id": None}, {"client_id": ""}, {"client_id": "C00030334"}]
    )
    assert resolver.resolve_client_id("123456789") == "C00030334"
