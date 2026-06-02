"""Peacock special-case data adapter (reads nbc-287716, not the standard sync)."""

from app.adapters.peacock.client import (
    InvalidCampaignIdError,
    PeacockMetaClient,
    PeacockMetaConfig,
    PeacockMetaClientError,
    split_final_copy,
)
from app.adapters.peacock.routing import RoutingMetaClient

__all__ = [
    "PeacockMetaClient",
    "PeacockMetaConfig",
    "PeacockMetaClientError",
    "InvalidCampaignIdError",
    "split_final_copy",
    "RoutingMetaClient",
]
