"""SOCIAL ADDITION — platform routing for the shared listener.

Maya's `SlackCloudTasksEnqueueService` enqueues through a single injected
`queue` object with an `.enqueue(payload) -> CloudTasksEnqueueResult` method.
`RoutingQAQueue` implements that exact interface but delegates to one of two
real `CloudTasksQAQueue` instances based on `payload.qa_app`:

    qa_app == "social"  -> social queue (qa-buddy-runs-social-* -> Meta worker)
    anything else       -> search queue (Maya's existing path, unchanged)

This is deliberately a thin wrapper so **her enqueue service is not modified**:
we inject a RoutingQAQueue as its `queue`, and the Search path stays
byte-identical (default qa_app="search"). Only social requests divert.
"""

from __future__ import annotations

from app.adapters.tasks import CloudTasksEnqueueResult, CloudTasksRequest

DEFAULT_QA_APP = "search"


class RoutingQAQueue:
    """Routes enqueue calls to the search or social queue by `payload.qa_app`.

    Both `search_queue` and `social_queue` must expose
    `enqueue(payload: CloudTasksRequest) -> CloudTasksEnqueueResult` (i.e. the
    `CloudTasksQAQueue` interface). `social_queue` may be None — if a social
    request arrives without a configured social queue, that's a wiring error
    and we raise rather than silently sending it to the Search worker.
    """

    def __init__(self, *, search_queue, social_queue=None, social_channel_ids=None) -> None:
        self._search = search_queue
        self._social = social_queue
        # Channel-based inference (the locked "listener infers qa_app from the
        # channel" decision). Her enqueue service leaves qa_app="search" by
        # default, so without this the router could never see a social request.
        # A request from one of these channels is treated as social even when
        # the envelope's qa_app wasn't explicitly set. Her code stays untouched.
        self._social_channel_ids = frozenset(social_channel_ids or ())

    @staticmethod
    def _normalize(qa_app: str | None) -> str:
        return (qa_app or DEFAULT_QA_APP).strip().lower() or DEFAULT_QA_APP

    def _resolve_qa_app(self, payload: CloudTasksRequest) -> str:
        qa_app = self._normalize(getattr(payload, "qa_app", DEFAULT_QA_APP))
        if qa_app == "social":
            return "social"
        # Infer from channel when not explicitly social.
        if getattr(payload, "channel_id", "") in self._social_channel_ids:
            return "social"
        return qa_app

    def enqueue(self, payload: CloudTasksRequest) -> CloudTasksEnqueueResult:
        qa_app = self._resolve_qa_app(payload)
        if qa_app == "social":
            if self._social is None:
                raise ValueError(
                    "qa_app=social but no social queue is configured "
                    "(RoutingQAQueue.social_queue is None)"
                )
            return self._social.enqueue(payload)
        return self._search.enqueue(payload)
