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

    project: GCP project holding the per-client datasets. Defaults to the
        Wpromote data warehouse project. Override in tests or for fixtures.
    """
    project: str = "polaris-data-317717"


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
        # Inject a client in tests; default uses ADC for real BQ.
        self._client = client or bigquery.Client(project=self.config.project)
        self._cache: dict[tuple[str, str, str], Any] = {}

    def get_campaign(self, client_id: str, campaign_id: str) -> dict[str, Any]:
        """Fetch one campaign row by (client_id, campaign_id).

        Returns the row as a dict, or {} if no row matches.
        """
        self._validate_ids(client_id, campaign_id)
        cache_key = ("campaign", client_id, campaign_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        table = self._table_ref(client_id, "facebook_ads__campaigns")
        query = f"""
            SELECT
              id,
              account_id,
              campaign_id,
              name,
              objective,
              effective_status,
              buying_type,
              spend_cap,
              start_time,
              created_time
            FROM {table}
            WHERE id = @campaign_id
            LIMIT 1
        """
        rows = self._run_query(query, campaign_id)
        result = dict(rows[0]) if rows else {}
        self._cache[cache_key] = result
        return result

    def get_ad_sets(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        """Fetch all ad sets under a campaign, joined with targeting rows.

        Each result dict includes both adset-level fields and the targeting
        fields from facebook_ads__adset_targetings (age, gender, location,
        excluded audiences, optimization). Returns [] if no rows match.
        """
        self._validate_ids(client_id, campaign_id)
        cache_key = ("ad_sets", client_id, campaign_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        adsets_table = self._table_ref(client_id, "facebook_ads__adsets")
        targetings_table = self._table_ref(client_id, "facebook_ads__adset_targetings")
        query = f"""
            SELECT
              a.id,
              a.adset_id,
              a.campaign_id,
              a.name,
              a.effective_status,
              a.budget_remaining,
              a.start_time,
              a.created_time,
              a.updated_time,
              a.countries AS adset_countries,
              t.age_min,
              t.age_max,
              t.genders,
              t.countries AS targeting_countries,
              t.location_types,
              t.excluded_custom_audiences,
              t.optimization,
              t.brand_safety_content_filter_levels
            FROM {adsets_table} a
            LEFT JOIN {targetings_table} t
              ON t.adset_id = a.id
            WHERE a.campaign_id = @campaign_id
        """
        rows = self._run_query(query, campaign_id)
        result = [dict(row) for row in rows]
        self._cache[cache_key] = result
        return result

    def get_ads(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]:
        """Fetch all ads under a campaign with their denormalized creative record.

        The ads table holds a `creative` RECORD that mirrors facebook_ads__ad_creatives,
        so no separate JOIN is needed for copy/headline/CTA/landing-URL fields.
        Returns [] if no rows match.
        """
        self._validate_ids(client_id, campaign_id)
        cache_key = ("ads", client_id, campaign_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        ads_table = self._table_ref(client_id, "facebook_ads__ads")
        query = f"""
            SELECT
              id,
              ad_id,
              adset_id,
              campaign_id,
              ad_creative_id,
              name,
              effective_status,
              status,
              bid_type,
              bid_amount,
              adlabels,
              creative.title AS creative_title,
              creative.body AS creative_body,
              creative.call_to_action_type AS creative_cta,
              creative.image_url AS creative_image_url,
              creative.object_type AS creative_object_type,
              creative.object_url AS creative_object_url
            FROM {ads_table}
            WHERE campaign_id = @campaign_id
        """
        rows = self._run_query(query, campaign_id)
        result = [dict(row) for row in rows]
        self._cache[cache_key] = result
        return result

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
