"""RunStore implementations: in-memory (dev/test) and Firestore (production).

Both conform to the RunStore Protocol in app.core.contracts.

Run records live in a single Firestore collection (default `qa_runs`, shared
with Search), tagged with `qa_app="social"` so the two platforms coexist and
can be filtered by platform. This matches the "single collection indexed by
qa_app" decision in the project memory.

The notification-dedup methods (has_worker_notification / mark_worker_notification)
exist so Cloud Task retries don't double-post the terminal Slack message. They
key on request_id, which is deterministic per the listener's envelope.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
from typing import Any

from app.core.contracts import RunRecord

_logger = logging.getLogger("paid_social_qa_buddy.run_store")

_DEFAULT_COLLECTION = "qa_runs"
_DEFAULT_QA_APP = "social"


def record_to_dict(record: RunRecord) -> dict[str, Any]:
    """Serialize a RunRecord to a plain dict for Firestore."""
    return dataclasses.asdict(record)


def record_from_dict(data: dict[str, Any]) -> RunRecord:
    """Deserialize a Firestore dict back to a RunRecord.

    Filters out keys that aren't RunRecord fields (e.g., the `qa_app` tag we
    add on write) so we don't hit an unexpected-keyword TypeError.
    """
    field_names = {f.name for f in dataclasses.fields(RunRecord)}
    filtered = {k: v for k, v in (data or {}).items() if k in field_names}
    return RunRecord(**filtered)


class InMemoryRunStore:
    """In-memory RunStore for local dev and tests. Not for production.

    Stores deep copies on write and returns deep copies on read, so the store
    behaves like Firestore (no shared mutable state between caller and store).
    """

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def get_run(self, run_id: str) -> RunRecord | None:
        record = self._runs.get(run_id)
        return copy.deepcopy(record) if record is not None else None

    def create_run(self, record: RunRecord) -> None:
        self._runs[record.run_id] = copy.deepcopy(record)

    def update_run(self, record: RunRecord) -> None:
        self._runs[record.run_id] = copy.deepcopy(record)

    def find_by_request_id(self, request_id: str) -> RunRecord | None:
        if not request_id:
            return None
        for record in self._runs.values():
            if record.request_id == request_id:
                return copy.deepcopy(record)
        return None

    def has_worker_notification(self, request_id: str) -> bool:
        record = self.find_by_request_id(request_id)
        return bool(record and record.worker_notified)

    def mark_worker_notification(self, request_id: str) -> None:
        if not request_id:
            return
        for record in self._runs.values():
            if record.request_id == request_id:
                record.worker_notified = True
                return


class FirestoreRunStore:
    """Firestore-backed RunStore for production run tracking.

    Document ID is the run_id, so get_run is a direct document lookup.
    find_by_request_id queries on the request_id field.
    """

    def __init__(
        self,
        collection_name: str = _DEFAULT_COLLECTION,
        client: Any | None = None,
        project: str = "",
        qa_app: str = _DEFAULT_QA_APP,
    ) -> None:
        self.collection_name = collection_name
        self.qa_app = qa_app
        if client is not None:
            self._client = client
        else:
            from google.cloud import firestore

            self._client = (
                firestore.Client(project=project) if project else firestore.Client()
            )

    def _collection(self):
        return self._client.collection(self.collection_name)

    def get_run(self, run_id: str) -> RunRecord | None:
        snapshot = self._collection().document(run_id).get()
        if not snapshot.exists:
            return None
        return record_from_dict(snapshot.to_dict())

    def create_run(self, record: RunRecord) -> None:
        data = record_to_dict(record)
        data["qa_app"] = self.qa_app
        self._collection().document(record.run_id).set(data)

    def update_run(self, record: RunRecord) -> None:
        data = record_to_dict(record)
        data["qa_app"] = self.qa_app
        self._collection().document(record.run_id).set(data, merge=True)

    def find_by_request_id(self, request_id: str) -> RunRecord | None:
        if not request_id:
            return None
        query = self._collection().where("request_id", "==", request_id).limit(1)
        docs = list(query.stream())
        if not docs:
            return None
        return record_from_dict(docs[0].to_dict())

    def has_worker_notification(self, request_id: str) -> bool:
        record = self.find_by_request_id(request_id)
        return bool(record and record.worker_notified)

    def mark_worker_notification(self, request_id: str) -> None:
        record = self.find_by_request_id(request_id)
        if record is None:
            return
        self._collection().document(record.run_id).set(
            {"worker_notified": True}, merge=True
        )
