"""Unit tests for PolarisClient.

Uses a mocked requests Session so the tests don't hit live Polaris.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app.adapters.polaris.client import (
    PolarisAuthError,
    PolarisClient,
    PolarisConfig,
    PolarisRequestError,
)


def _make_response(data: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    response.status_code = status_code
    response.raise_for_status.return_value = None
    return response


def _make_session(responses: list[MagicMock]) -> MagicMock:
    session = MagicMock()
    session.get.side_effect = responses
    return session


def _make_client(session: MagicMock) -> PolarisClient:
    return PolarisClient(
        config=PolarisConfig(
            api_url="https://api.polaris.wpromote.com",
            api_token="test-token-123",
        ),
        session=session,
    )


# --- Auth errors -----------------------------------------------------------


def test_raises_when_url_missing() -> None:
    with pytest.raises(PolarisAuthError):
        PolarisClient(
            config=PolarisConfig(api_url="", api_token="x"),
            session=MagicMock(),
        )


def test_raises_when_token_missing() -> None:
    with pytest.raises(PolarisAuthError):
        PolarisClient(
            config=PolarisConfig(api_url="https://x", api_token=""),
            session=MagicMock(),
        )


# --- Auth header pattern ---------------------------------------------------


def test_uses_token_auth_header_not_bearer() -> None:
    """Polaris uses DRF's TokenAuthentication ('Token <token>'), NOT Bearer."""
    session = _make_session(
        [_make_response({"count": 0, "next": None, "results": []})]
    )
    client = _make_client(session)
    client.fetch_paid_social_client_ids()

    headers = session.get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Token test-token-123"
    assert not headers["Authorization"].startswith("Bearer")


# --- fetch_paid_social_client_ids -----------------------------------------


def test_fetch_client_ids_single_page() -> None:
    session = _make_session(
        [
            _make_response(
                {
                    "count": 2,
                    "next": None,
                    "previous": None,
                    "results": [
                        {"enabled": True, "client": {"id": "C00000001", "name": None}},
                        {"enabled": True, "client": {"id": "C00000002", "name": None}},
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.fetch_paid_social_client_ids()

    assert result == {"C00000001", "C00000002"}
    assert session.get.call_count == 1


def test_fetch_client_ids_multi_page_follows_next() -> None:
    session = _make_session(
        [
            _make_response(
                {
                    "count": 3,
                    "next": "https://api.polaris.wpromote.com/core/api/services/?page=2",
                    "results": [
                        {"enabled": True, "client": {"id": "C00000001"}},
                        {"enabled": True, "client": {"id": "C00000002"}},
                    ],
                }
            ),
            _make_response(
                {
                    "count": 3,
                    "next": None,
                    "results": [
                        {"enabled": True, "client": {"id": "C00000003"}},
                    ],
                }
            ),
        ]
    )
    client = _make_client(session)
    result = client.fetch_paid_social_client_ids()

    assert result == {"C00000001", "C00000002", "C00000003"}
    assert session.get.call_count == 2


def test_fetch_client_ids_skips_disabled_services() -> None:
    session = _make_session(
        [
            _make_response(
                {
                    "count": 2,
                    "next": None,
                    "results": [
                        {"enabled": False, "client": {"id": "C00000001"}},
                        {"enabled": True, "client": {"id": "C00000002"}},
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.fetch_paid_social_client_ids()

    assert result == {"C00000002"}


def test_fetch_client_ids_skips_records_with_missing_client_id() -> None:
    session = _make_session(
        [
            _make_response(
                {
                    "count": 2,
                    "next": None,
                    "results": [
                        {"enabled": True, "client": {"id": "", "name": None}},
                        {"enabled": True, "client": {"id": "C00000001"}},
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.fetch_paid_social_client_ids()

    assert result == {"C00000001"}


def test_params_cleared_after_first_page() -> None:
    """The 'next' URL carries its own query string; we must not double up filters."""
    session = _make_session(
        [
            _make_response(
                {
                    "count": 2,
                    "next": "https://api.polaris.wpromote.com/core/api/services/?page=2&service_type_name=Paid+Social",
                    "results": [{"enabled": True, "client": {"id": "C00000001"}}],
                }
            ),
            _make_response(
                {
                    "count": 2,
                    "next": None,
                    "results": [{"enabled": True, "client": {"id": "C00000002"}}],
                }
            ),
        ]
    )
    client = _make_client(session)
    client.fetch_paid_social_client_ids()

    # First call: params should include the filter.
    first_call = session.get.call_args_list[0]
    assert first_call.kwargs["params"] is not None
    assert first_call.kwargs["params"]["service_type_name"] == "Paid Social"

    # Second call: params should be None so the 'next' URL's own query string takes effect.
    second_call = session.get.call_args_list[1]
    assert second_call.kwargs["params"] is None


# --- resolve_recipients_for_client ----------------------------------------


def test_resolve_recipients_finds_matching_client() -> None:
    session = _make_session(
        [
            _make_response(
                {
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "enabled": True,
                            "client": {"id": "C00000001"},
                            "team_email": "team@wpromote.com",
                            "managers": [
                                {
                                    "user": {
                                        "is_active": True,
                                        "email": "manager@wpromote.com",
                                    }
                                }
                            ],
                            "accountable_director": {
                                "user": {
                                    "is_active": True,
                                    "email": "ad@wpromote.com",
                                    "full_name": "Someone",
                                }
                            },
                        }
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.resolve_recipients_for_client("C00000001")

    assert "team@wpromote.com" in result
    assert "manager@wpromote.com" in result
    assert "ad@wpromote.com" in result
    assert len(result) == 3


def test_resolve_recipients_empty_when_no_match() -> None:
    session = _make_session(
        [
            _make_response(
                {
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "enabled": True,
                            "client": {"id": "C00000001"},
                            "team_email": "team@wpromote.com",
                        }
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.resolve_recipients_for_client("C99999999")

    assert result == []


def test_resolve_recipients_dedupes() -> None:
    """If team_email and manager email are the same, return once."""
    session = _make_session(
        [
            _make_response(
                {
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "enabled": True,
                            "client": {"id": "C00000001"},
                            "team_email": "same@wpromote.com",
                            "managers": [
                                {
                                    "user": {
                                        "is_active": True,
                                        "email": "same@wpromote.com",
                                    }
                                }
                            ],
                        }
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.resolve_recipients_for_client("C00000001")

    assert result == ["same@wpromote.com"]


def test_resolve_recipients_skips_inactive_managers() -> None:
    session = _make_session(
        [
            _make_response(
                {
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "enabled": True,
                            "client": {"id": "C00000001"},
                            "managers": [
                                {
                                    "user": {
                                        "is_active": False,
                                        "email": "former@wpromote.com",
                                    }
                                },
                                {
                                    "user": {
                                        "is_active": True,
                                        "email": "current@wpromote.com",
                                    }
                                },
                            ],
                        }
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.resolve_recipients_for_client("C00000001")

    assert result == ["current@wpromote.com"]


def test_resolve_recipients_handles_flat_accountable_director() -> None:
    """accountable_director comes in two shapes; code defensively per the
    reference impl ('sometimes a flat dict, sometimes wraps a user sub-object')."""
    session = _make_session(
        [
            _make_response(
                {
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "enabled": True,
                            "client": {"id": "C00000001"},
                            "accountable_director": {
                                "email": "flat-ad@wpromote.com",
                                "full_name": "Someone",
                            },
                        }
                    ],
                }
            )
        ]
    )
    client = _make_client(session)
    result = client.resolve_recipients_for_client("C00000001")

    assert "flat-ad@wpromote.com" in result


def test_resolve_recipients_with_empty_client_id_returns_empty() -> None:
    client = _make_client(_make_session([]))
    assert client.resolve_recipients_for_client("") == []


# --- Pagination safety ----------------------------------------------------


def test_pagination_respects_max_pages_safety_limit() -> None:
    """If the API returns a never-ending 'next' chain, we cap at _MAX_PAGES (50)."""
    infinite_responses = [
        _make_response(
            {
                "count": 9999,
                "next": "https://api.polaris.wpromote.com/core/api/services/?page=2",
                "results": [],
            }
        )
        for _ in range(60)
    ]
    session = _make_session(infinite_responses)
    client = _make_client(session)
    client.fetch_paid_social_client_ids()

    # Stopped at the safety limit, did not blow through all 60.
    assert session.get.call_count == 50


# --- HTTP errors ----------------------------------------------------------


def test_http_error_raises_polaris_request_error() -> None:
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
    session = MagicMock()
    session.get.return_value = response

    client = _make_client(session)
    with pytest.raises(PolarisRequestError):
        client.fetch_paid_social_client_ids()


def test_network_error_raises_polaris_request_error() -> None:
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("Connection refused")

    client = _make_client(session)
    with pytest.raises(PolarisRequestError):
        client.fetch_paid_social_client_ids()
