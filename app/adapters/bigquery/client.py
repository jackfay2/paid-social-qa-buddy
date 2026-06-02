"""BigQuery-backed MetaDataClient implementation.

Queries the Airbyte-synced facebook_ads__* tables in polaris-data-317717.
One dataset per client (`C<8 digits>`); each dataset has the same table layout.

Conforms to the MetaDataClient Protocol in app.core.contracts.

Field coverage (per schema dig on 2026-05-22):
  - facebook_ads__campaigns      — objective, buying_type, effective_status
                                   (daily_budget, bid_strategy NOT YET in BQ)
  - facebook_ads__adsets         — name, start_time, effective_status, countries
  - facebook_ads__adset_targetings — age_min/max, genders, countries,
                                   location_types, excluded_custom_audiences,
                                   optimization
  - facebook_ads__ads            — name, effective_status, status, bid_type,
                                   bid_amount, denormalized `creative` record
                                   (title, body, call_to_action_type, object_url)

Missing-field checks return Review in the check function layer, not here.
The adapter's job is to fetch what BQ has; check functions decide what to do
when a field is None or absent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from google.cloud import bigquery

_logger = logging.getLogger("paid_social_qa_buddy.bigquery")

# Wpromote client_id format: "C" followed by 8 digits (e.g., "C18090595").
# Strictly validated because it's interpolated into the SQL as the dataset name
# (BigQuery doesn't accept parameterized dataset names).
_CLIENT_ID_PATTERN = re.compile(r"^C\d{8}$")

# Meta campaign_id is numeric. Treated as a string at the boundary to avoid
# 64-bit overflow surprises across the listener/worker JSON boundary, but
# parsed to int before passing to BQ as an INT64 parameter.
_CAMPAIGN_ID_PATTERN = re.compile(r"^\d+$")


class BigQueryMetaClientError(Exception):
    """Base for adapter-level errors. Distinguishes our errors from raw BQ errors."""


class InvalidClientIdError(BigQueryMetaClientError):
    """Raised when client_id does not match the expected C<8 digits> format."""


class InvalidCampaignIdError(BigQueryMetaClientError):
    """Raised when campaign_id is not a numeric string."""


@dataclass(frozen=True)
class BigQueryMetaConfig:
    """BigQuery connection settings.

    project: GCP project HOLDING the per-client datasets (the data warehouse) —
        used to build fully-qualified table names. Defaults to the Wpromote
        data warehouse project.
    billing_project: GCP project where BQ query JOBS run and bill. Must be a
        project where the caller has bigquery.jobUser. In prod this is our app
        project (the SA has jobUser there + dataViewer on `project`). Blank →
        jobs run in `project` (works locally with a privileged ADC; fails on
        Cloud Run where the SA only has dataViewer on the warehouse).
    """
    project: str = "polaris-data-317717"
    billing_project: str = ""


def _ad_sort_key(ad: dict[str, Any]) -> tuple[int, int, str]:
    """Total, deterministic sort key for an ad row by its id.

    Numeric ids (the Meta ad id case) sort first and *numerically* (so "9" <
    "10", not lexicographically); anything non-numeric or missing sorts after,
    by string. Every key is an (int, int, str) tuple so the ordering is total
    and stable across runs/Python versions — which is what makes the text-check
    sampling reproducible.
    """
    raw = ad.get("id")
    if raw is None:
        raw = ad.get("ad_id")
    s = "" if raw is None else str(raw)
    if s.isdigit():
        return (0, int(s), s)
    return (1, 0, s)


class BigQueryMetaClient:
    """Concrete MetaDataClient backed by BigQuery.

    Per-job caching: each instance caches results keyed by (kind, client_id,
    campaign_id). Lifetime is bound to the instance; orchestration creates one
    BigQueryMetaClient per QA run and discards it at the end. This satisfies
    the "cache the response in memory for the lifetime of the job" rule from
    the original handoff doc §5.3 step 6, and keeps the worker stateless
    across runs (12-factor VI).
    """

    def __init__(
        self,
        config: BigQueryMetaConfig | None = None,
        client: bigquery.Client | None = None,
    ) -> None:
        self.config = config or BigQueryMetaConfig()
        # Inject a client in tests; default uses ADC for real BQ. Jobs bill to
        # billing_project (where the SA has jobUser); tables resolve against
        # `project` (the data warehouse, read via dataViewer).
        self._client = client or bigquery.Client(
            project=self.config.billing_project or self.config.project
        )
        self._cache: dict[tuple[str, str, str], Any] = {}

    def get_campaign(self, client_id: str, campaign_id: str) -> dict[str, Any]:
        """Fetch one campaign row by (client_id, campaign_id), or {} if none.

        SELECT * because per-client datasets do NOT share an identical schema
        (confirmed live 2026: some clients' tables lack columns others have).
        Naming columns breaks on any client missing one; SELECT * is robust,
        and check functions read fields defensively (absent field -> Review).

        Filters on `campaign_id` (the Meta campaign ID, consistent with the
        adset/ad tables) rather than `id`.
        """
        self._validate_ids(client_id, campaign_id)
        cache_key = ("campaign", client_id, campaign_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        table = self._table_ref(client_id, "facebook_ads__campaigns")
        query = f"SELECT * FROM {table} WHERE campaign_id = @campaign_id LIMIT 1"
        rows = self._run_query(query, campaign_id)
        result = dict(rows[0]) if rows else {}
        self._cache[cache_key] = result
        return result

    def get_ad_sets(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        """Fetch all ad sets under a campaign (SELECT *). Returns [] if none.

        Targeting data (age, gender, location, audiences, optimization) lives in
        a separate facebook_ads__adset_targetings table. It is NOT merged here
        yet — that comes when ad-set-level checks land (it'll be a separate fetch
        merged by adset_id, not a SQL JOIN, to stay robust to schema variance).
        The campaign-level checks don't need it.

        SELECT * for the same per-client schema-variance reason as get_campaign:
        some clients' adsets tables are missing columns others have (e.g.
        `countries`), so naming columns is fragile.
        """
        self._validate_ids(client_id, campaign_id)
        cache_key = ("ad_sets", client_id, campaign_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        table = self._table_ref(client_id, "facebook_ads__adsets")
        query = f"SELECT * FROM {table} WHERE campaign_id = @campaign_id"
        rows = self._run_query(query, campaign_id)
        result = [dict(row) for row in rows]
        self._cache[cache_key] = result
        return result

    def get_ads(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        """Fetch all ads under a campaign (SELECT *), with creative copy merged.

        The ad COPY (body), headline (title), and object_story_spec live in a
        SEPARATE table `facebook_ads__ad_creatives`, linked by `ad_creative_id`
        — NOT denormalized on the ad (confirmed live 2026-06-01, C61854560).
        We fetch those and merge them into `ad["creative"]` so the creative-
        reading checks (copy/headline spelling, CTA, landing URL) get the real
        text. SELECT * for per-client schema-variance robustness (see get_campaign).
        """
        self._validate_ids(client_id, campaign_id)
        cache_key = ("ads", client_id, campaign_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        table = self._table_ref(client_id, "facebook_ads__ads")
        query = f"SELECT * FROM {table} WHERE campaign_id = @campaign_id"
        rows = self._run_query(query, campaign_id)
        result = [dict(row) for row in rows]
        # Stable order by ad id. BigQuery returns rows in an unspecified order,
        # and the text-check pipeline samples only the first TEXT_CHECK_AD_CAP
        # ads on large campaigns — without a deterministic order, two runs of the
        # same campaign would spell-check a different random subset and could
        # disagree (Pass one run, Fix the next). Sorting here makes the sample —
        # and the verdicts — reproducible. (No SQL ORDER BY: SELECT * spans
        # schema-variant per-client tables, so we sort in Python defensively.)
        result.sort(key=_ad_sort_key)
        self._attach_creatives(client_id, result)
        self._cache[cache_key] = result
        return result

    def _attach_creatives(self, client_id: str, ads: list[dict[str, Any]]) -> None:
        """Merge each ad's row from facebook_ads__ad_creatives into ad['creative'].

        Keyed by `ad_creative_id` → creatives table `id`. Defensive: if the
        table/columns are absent for this client, or the fetch fails, leave the
        ads as-is (the creative-reading checks then return Review, not Fix).
        """
        creative_ids = sorted(
            {str(a.get("ad_creative_id")) for a in ads if a.get("ad_creative_id")}
        )
        if not creative_ids:
            return
        table = self._table_ref(client_id, "facebook_ads__ad_creatives")
        query = f"SELECT * FROM {table} WHERE CAST(id AS STRING) IN UNNEST(@ids)"
        try:
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("ids", "STRING", creative_ids)
                ]
            )
            rows = list(self._client.query(query, job_config=job_config).result())
        except Exception as exc:  # noqa: BLE001 — schema variance; degrade to no-merge
            _logger.info(
                "ad_creatives_fetch_skipped", extra={"error": str(exc)[:200]}
            )
            return
        by_id = {str(dict(r).get("id")): dict(r) for r in rows}
        for ad in ads:
            creative = by_id.get(str(ad.get("ad_creative_id")))
            if creative:
                existing = ad.get("creative")
                base = existing if isinstance(existing, dict) else {}
                ad["creative"] = {**base, **creative}

    def _run_query(self, query: str, campaign_id: str) -> list[Any]:
        """Execute a parameterized query against the configured BQ project."""
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "campaign_id", "INT64", int(campaign_id)
                ),
            ]
        )
        _logger.debug(
            "bigquery_query_start",
            extra={"campaign_id": campaign_id},
        )
        result = list(self._client.query(query, job_config=job_config).result())
        _logger.info(
            "bigquery_query_done",
            extra={"campaign_id": campaign_id, "row_count": len(result)},
        )
        return result

    def _table_ref(self, client_id: str, table_name: str) -> str:
        """Build a fully-qualified, backtick-quoted BQ table reference."""
        return f"`{self.config.project}.{client_id}.{table_name}`"

    @staticmethod
    def _validate_ids(client_id: str, campaign_id: str) -> None:
        """Reject invalid IDs before they touch SQL.

        client_id is interpolated into the dataset name (BQ doesn't allow
        parameterized dataset names), so the strict `C\\d{8}` regex is the
        SQL injection defense. campaign_id is passed as a bound INT64 param
        but we still validate the format up front for clearer errors.
        """
        if not _CLIENT_ID_PATTERN.match(client_id or ""):
            raise InvalidClientIdError(
                f"client_id must match format 'C<8 digits>', got: {client_id!r}"
            )
        if not _CAMPAIGN_ID_PATTERN.match(campaign_id or ""):
            raise InvalidCampaignIdError(
                f"campaign_id must be a numeric string, got: {campaign_id!r}"
            )
