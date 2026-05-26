"""Unit tests for the RunStore implementations.

InMemoryRunStore is tested directly. FirestoreRunStore is tested with a mocked
firestore client. Serialization helpers are tested standalone.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.storage.run_store import (
    FirestoreRunStore,
    InMemoryRunStore,
    record_from_dict,
    record_to_dict,
)
from app.core.contracts import RunRecord


def _record(**overrides) -> RunRecord:
    base = {
        "run_id": "run-1",
        "request_id": "req-1",
        "status": "accepted",
        "campaign_id": "123",
    }
    base.update(overrides)
    return RunRecord(**base)


# --- Serialization helpers -------------------------------------------------


def test_record_round_trips_through_dict() -> None:
    record = _record(pass_count=3, fix_items=["a", "b"])
    data = record_to_dict(record)
    restored = record_from_dict(data)
    assert restored == record


def test_record_from_dict_ignores_unknown_keys() -> None:
    """The qa_app tag (and any other extra keys) must not break deserialization."""
    data = record_to_dict(_record())
    data["qa_app"] = "social"
    data["some_future_field"] = "whatever"
    restored = record_from_dict(data)
    assert restored.run_id == "run-1"


# --- InMemoryRunStore ------------------------------------------------------


def test_inmemory_create_and_get_round_trip() -> None:
    store = InMemoryRunStore()
    record = _record()
    store.create_run(record)

    got = store.get_run("run-1")
    assert got == record


def test_inmemory_get_nonexistent_returns_none() -> None:
    store = InMemoryRunStore()
    assert store.get_run("does-not-exist") is None


def test_inmemory_update_overwrites() -> None:
    store = InMemoryRunStore()
    store.create_run(_record(status="accepted"))
    store.update_run(_record(status="completed", pass_count=5))

    got = store.get_run("run-1")
    assert got.status == "completed"
    assert got.pass_count == 5


def test_inmemory_returns_copies_not_references() -> None:
    """Mutating a returned record must not change the stored one."""
    store = InMemoryRunStore()
    store.create_run(_record())

    got = store.get_run("run-1")
    got.status = "mutated"

    fresh = store.get_run("run-1")
    assert fresh.status == "accepted"


def test_inmemory_find_by_request_id() -> None:
    store = InMemoryRunStore()
    store.create_run(_record(run_id="run-1", request_id="req-abc"))
    store.create_run(_record(run_id="run-2", request_id="req-xyz"))

    got = store.find_by_request_id("req-xyz")
    assert got is not None
    assert got.run_id == "run-2"


def test_inmemory_find_by_request_id_not_found() -> None:
    store = InMemoryRunStore()
    store.create_run(_record(request_id="req-abc"))
    assert store.find_by_request_id("req-nope") is None


def test_inmemory_find_by_empty_request_id_returns_none() -> None:
    store = InMemoryRunStore()
    store.create_run(_record(request_id=""))
    assert store.find_by_request_id("") is None


def test_inmemory_worker_notification_lifecycle() -> None:
    store = InMemoryRunStore()
    store.create_run(_record(request_id="req-1"))

    assert store.has_worker_notification("req-1") is False
    store.mark_worker_notification("req-1")
    assert store.has_worker_notification("req-1") is True


# --- FirestoreRunStore (mocked client) ------------------------------------


def _make_firestore_mock() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Returns (client, collection, document) mocks wired together."""
    client = MagicMock()
    collection = MagicMock()
    document = MagicMock()
    client.collection.return_value = collection
    collection.document.return_value = document
    return client, collection, document


def test_firestore_create_run_tags_qa_app() -> None:
    client, collection, document = _make_firestore_mock()
    store = FirestoreRunStore(client=client, qa_app="social")

    store.create_run(_record())

    document.set.assert_called_once()
    written = document.set.call_args.args[0]
    assert written["qa_app"] == "social"
    assert written["run_id"] == "run-1"


def test_firestore_get_run_returns_record_when_exists() -> None:
    client, collection, document = _make_firestore_mock()
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {
        "run_id": "run-1",
        "request_id": "req-1",
        "status": "completed",
        "qa_app": "social",
    }
    document.get.return_value = snapshot

    store = FirestoreRunStore(client=client)
    got = store.get_run("run-1")

    assert got is not None
    assert got.run_id == "run-1"
    assert got.status == "completed"


def test_firestore_get_run_returns_none_when_missing() -> None:
    client, collection, document = _make_firestore_mock()
    snapshot = MagicMock()
    snapshot.exists = False
    document.get.return_value = snapshot

    store = FirestoreRunStore(client=client)
    assert store.get_run("nope") is None


def test_firestore_update_run_uses_merge() -> None:
    client, collection, document = _make_firestore_mock()
    store = FirestoreRunStore(client=client)

    store.update_run(_record(status="running"))

    document.set.assert_called_once()
    assert document.set.call_args.kwargs.get("merge") is True


def test_firestore_find_by_request_id_queries_field() -> None:
    client, collection, document = _make_firestore_mock()
    query = MagicMock()
    collection.where.return_value = query
    query.limit.return_value = query

    snapshot = MagicMock()
    snapshot.to_dict.return_value = {"run_id": "run-9", "request_id": "req-9"}
    query.stream.return_value = [snapshot]

    store = FirestoreRunStore(client=client)
    got = store.find_by_request_id("req-9")

    assert got is not None
    assert got.run_id == "run-9"
    collection.where.assert_called_once_with("request_id", "==", "req-9")


def test_firestore_find_by_request_id_empty_returns_none_without_query() -> None:
    client, collection, document = _make_firestore_mock()
    store = FirestoreRunStore(client=client)

    assert store.find_by_request_id("") is None
    collection.where.assert_not_called()
