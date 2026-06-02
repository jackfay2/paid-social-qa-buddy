"""Routing MetaDataClient: dispatch per-client to the right backend.

Most clients use the standard BigQueryMetaClient (polaris-data-317717). A few
are special-cased — Peacock (C22848672) reads its own GCP project via
PeacockMetaClient. This wrapper implements the MetaDataClient Protocol and routes
each call by client_id, so the orchestration service stays unchanged (it still
holds a single `meta_client`).

Conforms to the MetaDataClient Protocol in app.core.contracts.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("paid_social_qa_buddy.meta_routing")


class RoutingMetaClient:
    """Delegate get_campaign/get_ad_sets/get_ads to a per-client_id backend.

    overrides: {client_id: MetaDataClient}. Any client_id not in the map uses
    `default`. Routing is exact client_id match (no fuzzy logic), mirroring the
    direct-dict-lookup rule used elsewhere in the bot.
    """

    def __init__(self, default: Any, overrides: dict[str, Any] | None = None) -> None:
        self._default = default
        self._overrides = dict(overrides or {})

    def _client_for(self, client_id: str) -> Any:
        backend = self._overrides.get(client_id)
        if backend is not None:
            _logger.info(
                "meta_route_override", extra={"client_id": client_id, "backend": type(backend).__name__}
            )
            return backend
        return self._default

    def get_campaign(self, client_id: str, campaign_id: str) -> dict[str, Any]:
        return self._client_for(client_id).get_campaign(client_id, campaign_id)

    def get_ad_sets(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        return self._client_for(client_id).get_ad_sets(client_id, campaign_id)

    def get_ads(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        return self._client_for(client_id).get_ads(client_id, campaign_id)
