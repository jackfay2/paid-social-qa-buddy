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

Phase B (the trafficking table `AirTable_v2.wp_live_trafficking`): joined per
creative by distribution ID (perf.DistributionID <-> traf.Distribution_, with
VersionNumber <-> Version_ as the fallback for un-reused historical creatives —
Pamela, 2026-06-03). Adds a `trafficking` sub-dict to each ad with the
build-time spec:
  ad.trafficking.frame_size         <- Frame_Size    ("1080x1920" — drives ad_creative_dimensions)
  ad.trafficking.asset_type         <- Asset_Type
  ad.trafficking.flight_start_date  <- Media_Flight_Date (DATE)
  ad.trafficking.flight_end_date    <- Media_End_Date    (DATE)
  ad.trafficking.flight_window_flag <- Live_After_End_Date_Warning (pre-computed QC)
  ad.trafficking.trafficking_status <- Trafficking_Status
  ad.trafficking.offer/show/genre   <- Offer[0]/Show_Name_For_File_Name/Genre
The merge is best-effort: a trafficking query failure logs + degrades to
perf-only (checks that need trafficking fields then return Review, never Fix).
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
    # Phase B: the Airtable trafficking mirror (same project). Joined per creative
    # by distribution ID. Blank trafficking_table -> merge skipped (Phase-A
    # behavior: perf-only). See docs/peacock_phase_b_spec.md.
    trafficking_dataset: str = "AirTable_v2"
    trafficking_table: str = "wp_live_trafficking"


def _creative_sort_key(creative_id: Any) -> tuple[int, int, str]:
    """Stable, total sort key by creative id (numeric ids sort numerically).

    The text-check pipeline samples only the first N creatives on a large
    campaign; without a deterministic order, two runs spell-check a different
    subset and can disagree. Same reproducibility guard the standard adapter uses.
    """
    s = "" if creative_id is None else str(creative_id)
    return (0, int(s), s) if s.isdigit() else (1, 0, s)


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
        # Phase B trafficking lookup, cached per campaign: maps the join key
        # (distribution id, and version number as fallback) -> trafficking row.
        self._traf_cache: dict[str, dict[str, dict[str, Any]]] = {}

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
        traffic = self._trafficking_lookup(campaign_id)
        ads: list[dict[str, Any]] = []
        for r in rows:
            headline, body = split_final_copy(r.get("copy"))
            ad: dict[str, Any] = {
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
            # Phase B: merge the trafficked build-spec for this creative, joined
            # by distribution id (version number as the documented fallback).
            traf = self._match_trafficking(traffic, r)
            if traf:
                ad["trafficking"] = traf
            ads.append(ad)
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
              ANY_VALUE(Campaign) AS campaign_name,
              CAST(ANY_VALUE(DistributionID) AS STRING) AS distribution_id,
              CAST(ANY_VALUE(VersionNumber) AS STRING) AS version_number
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
        # Deterministic order so the text-check "first N creatives" sample is
        # reproducible run-to-run (BigQuery GROUP BY order is unspecified).
        result.sort(key=lambda r: _creative_sort_key(r.get("creative_id")))
        _logger.info(
            "peacock_query_done",
            extra={"campaign_id": campaign_id, "row_count": len(result)},
        )
        self._cache[campaign_id] = result
        return result

    # --- Phase B: trafficking-table merge ---------------------------------

    def _trafficking_lookup(self, campaign_id: str) -> dict[str, dict[str, dict[str, Any]]]:
        """Fetch the trafficked build-spec for this campaign's creatives, keyed
        for the per-creative join (one query, cached).

        Returns {"by_dist": {distribution_id: row}, "by_version": {version: row}}.
        Best-effort: if the trafficking table isn't configured, the campaign has
        no distribution ids to join on, or the query fails, returns {} so the run
        degrades cleanly to perf-only (Phase A) — never raises into the run.
        """
        if not self.config.trafficking_table:
            return {}
        if campaign_id in self._traf_cache:
            return self._traf_cache[campaign_id]

        perf = self._load(campaign_id)
        dist_ids = sorted({r["distribution_id"] for r in perf if r.get("distribution_id")})
        versions = sorted({r["version_number"] for r in perf if r.get("version_number")})
        if not dist_ids and not versions:
            self._traf_cache[campaign_id] = {}
            return {}

        traf_table = (
            f"`{self.config.project}.{self.config.trafficking_dataset}."
            f"{self.config.trafficking_table}`"
        )
        query = f"""
            SELECT
              CAST(Distribution_ AS STRING) AS distribution_id,
              CAST(Version_ AS STRING) AS version_number,
              Frame_Size AS frame_size,
              Asset_Type AS asset_type,
              Media_Flight_Date AS flight_start_date,
              Media_End_Date AS flight_end_date,
              Live_After_End_Date_Warning AS flight_window_flag,
              Trafficking_Status AS trafficking_status,
              Confirmed_Paused_Creative AS confirmed_paused,
              (SELECT x FROM UNNEST(Offer) x WHERE x IS NOT NULL AND x != '' LIMIT 1) AS offer,
              Show_Name_For_File_Name AS show_name,
              Genre AS genre
            FROM {traf_table}
            WHERE @platform IN UNNEST(Platform)
              AND (CAST(Distribution_ AS STRING) IN UNNEST(@dist_ids)
                   OR CAST(Version_ AS STRING) IN UNNEST(@versions))
            ORDER BY distribution_id, version_number
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("platform", "STRING", self.config.platform),
                bigquery.ArrayQueryParameter("dist_ids", "STRING", dist_ids),
                bigquery.ArrayQueryParameter("versions", "STRING", versions),
            ]
        )
        try:
            rows = [dict(r) for r in self._client.query(query, job_config=job_config).result()]
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
            _logger.warning(
                "peacock_trafficking_query_failed",
                extra={"campaign_id": campaign_id, "error": str(exc)},
            )
            self._traf_cache[campaign_id] = {}
            return {}

        by_dist: dict[str, dict[str, Any]] = {}
        by_version: dict[str, dict[str, Any]] = {}
        for r in rows:
            shaped = self._shape_trafficking_row(r)
            d = r.get("distribution_id")
            v = r.get("version_number")
            if d and d not in by_dist:
                by_dist[d] = shaped
            if v and v not in by_version:
                by_version[v] = shaped
        lookup = {"by_dist": by_dist, "by_version": by_version}
        _logger.info(
            "peacock_trafficking_done",
            extra={
                "campaign_id": campaign_id,
                "trafficking_rows": len(rows),
                "matched_distributions": len(by_dist),
            },
        )
        self._traf_cache[campaign_id] = lookup
        return lookup

    @staticmethod
    def _shape_trafficking_row(r: dict[str, Any]) -> dict[str, Any]:
        """Map a raw trafficking BQ row to the `ad.trafficking` sub-dict. Dates
        are ISO strings so the evidence stays JSON-clean for the Firestore audit
        log; the date checks parse ISO fine."""
        def _iso(value: Any) -> Any:
            return value.isoformat() if hasattr(value, "isoformat") else value

        return {
            "frame_size": r.get("frame_size"),
            "asset_type": r.get("asset_type"),
            "flight_start_date": _iso(r.get("flight_start_date")),
            "flight_end_date": _iso(r.get("flight_end_date")),
            "flight_window_flag": r.get("flight_window_flag"),
            "trafficking_status": r.get("trafficking_status"),
            "confirmed_paused": r.get("confirmed_paused"),
            "offer": r.get("offer"),
            "show": r.get("show_name"),
            "genre": r.get("genre"),
        }

    @staticmethod
    def _match_trafficking(
        lookup: dict[str, dict[str, dict[str, Any]]], perf_row: dict[str, Any]
    ) -> dict[str, Any]:
        """Match a perf creative to its trafficking row: distribution id first
        (the primary key), version number as the fallback (Pamela's rule for
        un-reused historical creatives whose distribution id is blank)."""
        if not lookup:
            return {}
        d = perf_row.get("distribution_id")
        v = perf_row.get("version_number")
        if d and d in lookup.get("by_dist", {}):
            return lookup["by_dist"][d]
        if v and v in lookup.get("by_version", {}):
            return lookup["by_version"][v]
        return {}
