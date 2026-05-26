"""Unit tests for BigQueryAccountResolver, using a mocked bigquery.Client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.bigquery.resolver import (
    AmbiguousAccountError,
    BigQueryAccountResolver,
    InvalidAccountIdError,
    ResolverConfig,
)


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
