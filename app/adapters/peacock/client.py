"""Peacock-specific MetaDataClient.

Peacock (Wpromote client C22848672) is a special case: its live Meta data does
NOT flow to the standard `polaris-data-317717.C<client>.facebook_ads__*` Airbyte
sync (that's frozen at 2023). It lives in a standalone GCP project, in a single
unified cross-platform reporting table:

    nbc-287716.prod_peacock_final_data.creative_and_audience_data

This adapter reads that table (filtered to Platform='Meta'), dedups the daily
performance rows down to distinct entities, and shapes them into the SAME
`evidence` dicts the standard checks consume — so the existing check registry
runs unchanged. Checks whose fields aren't present in Peacock's data degrade to
Review automatically (the presence/verifiability guards added 2026-06-02).

Conforms to the MetaDataClient Protocol in app.core.contracts. Routing to this
client (vs the standard BigQueryMetaClient) is by client_id; see
app.adapters.peacock.routing.RoutingMetaClient.

Coverage (see docs/peacock_adapter_spec.md): strong on creative/copy/landing-URL
/objective/status; the Meta campaign-SETTINGS checks (bid strategy, targeting,
attribution, conversion event, spend caps, audiences, …) have no column here and
safely return Review.

Field map (verified against live data 2026-06-02):
  campaign.objective              <- Objective       ("Acquisition" — Peacock vocab)
  campaign.buying_type            <- Buy_Type        ("Biddable")
  campaign.name                   <- Campaign
  ad_set.name                     <- Ad_Set_Name
  ad.effective_status             <- Creative_Status ("Live"/"Paused")
  ad.creative.title  (headline)   <- FinalCopy "Headline: …" part
  ad.creative.body   (copy)       <- FinalCopy "Body: …" part (78% populated)
  ad.creative.link_url            <- URL             (93% populated)
  ad.creative.call_to_action_type <- CTABundle       ("Sign Up"/"Learn More"/…)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from google.cloud import bigquery

_logger = logging.getLogger("paid_social_qa_buddy.peacock")

# Campaign_ID in the unified table is the Meta campaign id (numeric, ~18 digits).
_CAMPAIGN_ID_PATTERN = re.compile(r"^\d+$")


class PeacockMetaClientError(Exception):
    """Base for Peacock adapter errors."""


class InvalidCampaignIdError(PeacockMetaClientError):
    """Raised when campaign_id is not a numeric string."""


@dataclass(frozen=True)
class PeacockMetaConfig:
    """Connection settings for Peacock's standalone data project.

    project: the GCP project holding Peacock's table (the data warehouse).
    billing_project: where the BQ job runs/bills (our app project, where the SA
        has jobUser). Blank -> jobs run in `project` (works locally with a
        privileged ADC; the deployed SA needs dataViewer on `project`).
    """

    project: str = "nbc-287716"
    dataset: str = "prod_peacock_final_data"
    table: str = "creative_and_audience_data"
    billing_project: str = ""
    platform: str = "Meta"
    # The table is large + partitioned by month on Date. Bound the per-campaign
    # scan to recent partitions: QA cares about current settings, not all-time
    # history (an always-on campaign can have thousands of all-time creatives).
    lookback_days: int = 365


def split_final_copy(text: Any) -> tuple[str, str]:
    """Split Peacock's `FinalCopy` into (headline, body).

    Live format is ``"Headline: <h>\\nBody: <b>"``. Tolerant: if only a Body
    marker is present, headline is ""; if no markers at all, the whole string is
    treated as the body (so spelling still runs on it). Returns ("", "") for empty.
    """
    if not text:
        return "", ""
    s = str(text).strip()
    low = s.lower()
    h_idx = low.find("headline:")
    b_idx = low.find("body:")
    if h_idx != -1 and b_idx != -1 and b_idx > h_idx:
        headline = s[h_idx + len("headline:") : b_idx].strip()
        body = s[b_idx + len("body:") :].strip()
        return headline, body
    if b_idx != -1:
        return "", s[b_idx + len("body:") :].strip()
    return "", s


class PeacockMetaClient:
    """MetaDataClient backed by Peacock's unified BigQuery table.

    One query per campaign (cached for the run): pull the distinct creatives +
    their settings, then derive campaign / ad_sets / ads from that one result.
    The unified table is keyed by Meta campaign_id (Platform='Meta'); account_id
    and client_id are accepted for signature/route compatibility but not needed
    to locate the data (the table is fixed, not per-client).
    """

    def __init__(
        self,
        config: PeacockMetaConfig | None = None,
        client: bigquery.Client | None = None,
    ) -> None:
        self.config = config or PeacockMetaConfig()
        self._client = client or bigquery.Client(
            project=self.config.billing_project or self.config.project
        )
        self._cache: dict[str, list[dict[str, Any]]] = {}

    # --- MetaDataClient protocol ------------------------------------------

    def get_campaign(self, client_id: str, campaign_id: str) -> dict[str, Any]:
        rows = self._load(campaign_id)
        if not rows:
            return {}
        first = rows[0]
        return {
            "campaign_id": campaign_id,
            "name": first.get("campaign_name"),
            "objective": first.get("objective"),
            "buying_type": first.get("buy_type"),
        }

    def get_ad_sets(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._load(campaign_id)
        seen: dict[str, dict[str, Any]] = {}
        for r in rows:
            asid = r.get("adset_id")
            if asid and asid not in seen:
                seen[asid] = {"adset_id": asid, "name": r.get("adset_name")}
        return list(seen.values())

    def get_ads(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._load(campaign_id)
        ads: list[dict[str, Any]] = []
        for r in rows:
            headline, body = split_final_copy(r.get("copy"))
            ads.append(
                {
                    "id": r.get("creative_id"),
                    "ad_id": r.get("creative_id"),
                    "name": r.get("creative_name"),
                    "adset_id": r.get("adset_id"),
                    "effective_status": r.get("status"),
                    "creative": {
                        "title": headline,
                        "body": body,
                        "link_url": r.get("url"),
                        "call_to_action_type": r.get("cta"),
                    },
                }
            )
        return ads

    # --- internals ---------------------------------------------------------

    def _load(self, campaign_id: str) -> list[dict[str, Any]]:
        """Fetch + dedup the campaign's Meta creatives (one query, cached).

        DISTINCT by Creative_ID with ANY_VALUE on the (per-creative-stable)
        settings columns — never aggregates metrics; this is a settings-QA read.
        """
        if not _CAMPAIGN_ID_PATTERN.match(campaign_id or ""):
            raise InvalidCampaignIdError(
                f"campaign_id must be a numeric string, got: {campaign_id!r}"
            )
        if campaign_id in self._cache:
            return self._cache[campaign_id]

        table = f"`{self.config.project}.{self.config.dataset}.{self.config.table}`"
        query = f"""
            SELECT
              CAST(Creative_ID AS STRING) AS creative_id,
              ANY_VALUE(Creative) AS creative_name,
              ANY_VALUE(Creative_Status) AS status,
              ANY_VALUE(FinalCopy) AS copy,
              ANY_VALUE(URL) AS url,
              ANY_VALUE(CTABundle) AS cta,
              CAST(ANY_VALUE(Ad_Set_ID) AS STRING) AS adset_id,
              ANY_VALUE(Ad_Set_Name) AS adset_name,
              ANY_VALUE(Objective) AS objective,
              ANY_VALUE(Buy_Type) AS buy_type,
              ANY_VALUE(Campaign) AS campaign_name
            FROM {table}
            WHERE Platform = @platform
              AND CAST(Campaign_ID AS STRING) = @campaign_id
              AND Date >= DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_days DAY)
            GROUP BY creative_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("platform", "STRING", self.config.platform),
                bigquery.ScalarQueryParameter("campaign_id", "STRING", campaign_id),
                bigquery.ScalarQueryParameter("lookback_days", "INT64", self.config.lookback_days),
            ]
        )
        result = [dict(r) for r in self._client.query(query, job_config=job_config).result()]
        _logger.info(
            "peacock_query_done",
            extra={"campaign_id": campaign_id, "row_count": len(result)},
        )
        self._cache[campaign_id] = result
        return result
