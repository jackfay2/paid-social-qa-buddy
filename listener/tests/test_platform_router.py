"""Tests for the social-routing layer (RoutingQAQueue).

Run from listener/ so `app` resolves to the vendored listener:
    cd listener && pytest
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.tasks import CloudTasksEnqueueResult, CloudTasksRequest
from app.listener.platform_router import RoutingQAQueue


def _payload(qa_app: str = "search", channel_id: str = "C1") -> CloudTasksRequest:
    return CloudTasksRequest(
        request_id="r1",
        channel_id=channel_id,
        thread_ts="1.2",
        sheet_url="https://docs.google.com/spreadsheets/d/x/edit",
        customer_id="10152426494631116",
        campaign_id="6065738140956",
        campaign_name="Test",
        requester_user_id="U1",
        requester_text="@qa-buddy ...",
        qa_app=qa_app,
    )


def _mock_queue(name: str) -> MagicMock:
    q = MagicMock()
    q.enqueue.return_value = CloudTasksEnqueueResult(task_name=name, request_id="r1")
    return q


def test_social_request_routes_to_social_queue() -> None:
    search, social = _mock_queue("search"), _mock_queue("social")
    router = RoutingQAQueue(search_queue=search, social_queue=social)
    result = router.enqueue(_payload("social"))
    social.enqueue.assert_called_once()
    search.enqueue.assert_not_called()
    assert result.task_name == "social"


def test_search_request_routes_to_search_queue() -> None:
    search, social = _mock_queue("search"), _mock_queue("social")
    router = RoutingQAQueue(search_queue=search, social_queue=social)
    router.enqueue(_payload("search"))
    search.enqueue.assert_called_once()
    social.enqueue.assert_not_called()


def test_default_qa_app_routes_to_search() -> None:
    """Backward compat: a payload with the default qa_app stays on Search."""
    search, social = _mock_queue("search"), _mock_queue("social")
    router = RoutingQAQueue(search_queue=search, social_queue=social)
    router.enqueue(_payload())  # default "search"
    search.enqueue.assert_called_once()
    social.enqueue.assert_not_called()


def test_qa_app_normalized_case_insensitive() -> None:
    search, social = _mock_queue("search"), _mock_queue("social")
    router = RoutingQAQueue(search_queue=search, social_queue=social)
    router.enqueue(_payload("SOCIAL"))
    social.enqueue.assert_called_once()


def test_social_without_social_queue_raises_not_silently_search() -> None:
    """A social request with no social queue is a wiring error — never silently
    send it to the Search worker."""
    search = _mock_queue("search")
    router = RoutingQAQueue(search_queue=search, social_queue=None)
    with pytest.raises(ValueError):
        router.enqueue(_payload("social"))
    search.enqueue.assert_not_called()


def test_social_channel_infers_social_even_when_qa_app_default() -> None:
    """The locked decision: infer qa_app from the channel. A request from a
    configured social channel routes social even though her enqueue service
    left qa_app='search'."""
    search, social = _mock_queue("search"), _mock_queue("social")
    router = RoutingQAQueue(
        search_queue=search, social_queue=social,
        social_channel_ids={"C0B6ASW9R9V"},
    )
    router.enqueue(_payload(qa_app="search", channel_id="C0B6ASW9R9V"))
    social.enqueue.assert_called_once()
    search.enqueue.assert_not_called()


def test_non_social_channel_stays_search() -> None:
    search, social = _mock_queue("search"), _mock_queue("social")
    router = RoutingQAQueue(
        search_queue=search, social_queue=social,
        social_channel_ids={"C0B6ASW9R9V"},
    )
    router.enqueue(_payload(qa_app="search", channel_id="C_SEARCH"))
    search.enqueue.assert_called_once()
    social.enqueue.assert_not_called()


def test_envelope_defaults_qa_app_to_search() -> None:
    """The vendored CloudTasksRequest defaults qa_app=search (no regression)."""
    p = CloudTasksRequest(
        request_id="r", channel_id="c", thread_ts="t", sheet_url="s",
        customer_id="1", campaign_id="2", campaign_name="n",
        requester_user_id="u", requester_text="x",
    )
    assert p.qa_app == "search"
