"""Resolves a Meta account_id to a Wpromote client_id (the BQ dataset selector).

Queries the cross-client `summary.facebook_ads__account_performance` table,
which carries both account_id and client_id. Because BigQuery is columnar,
`SELECT DISTINCT client_id WHERE account_id = X` only scans those two columns,
so the lack of a partition filter is cheap.

Known limitation: that table only contains accounts with performance data, so a
brand-new, zero-spend account won't resolve. resolve_client_id returns None in
that case; orchestration surfaces a clear "couldn't map account" error rather
than silently failing.

Conforms to the AccountResolver Protocol in app.core.contracts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from google.cloud import bigquery

_logger = logging.getLogger("paid_social_qa_buddy.resolver")

_ACCOUNT_ID_PATTERN = re.compile(r"^\d+$")


class AccountResolutionError(Exception):
    """Base for resolver errors."""


class InvalidAccountIdError(AccountResolutionError):
    """Raised when account_id is not a numeric string."""


class AmbiguousAccountError(AccountResolutionError):
    """Raised when an account_id maps to more than one client_id.

    Unusual (accounts normally belong to one client), so we refuse to guess
    and surface it for a human rather than silently picking one. Aligns with
    the default-to-Review-on-uncertainty principle.
    """

    def __init__(self, account_id: str, client_ids: list[str]) -> None:
        self.account_id = account_id
        self.client_ids = client_ids
        super().__init__(
            f"account_id {account_id} maps to multiple client_ids: {client_ids}"
        )


@dataclass(frozen=True)
class ResolverConfig:
    project: str = "polaris-data-317717"
    summary_dataset: str = "summary"


class BigQueryAccountResolver:
    """Concrete AccountResolver backed by the summary performance table."""

    def __init__(
        self,
        config: ResolverConfig | None = None,
        client: bigquery.Client | None = None,
    ) -> None:
        self.config = config or ResolverConfig()
        self._client = client or bigquery.Client(project=self.config.project)

    def resolve_client_id(self, account_id: str) -> str | None:
        if not _ACCOUNT_ID_PATTERN.match(account_id or ""):
            raise InvalidAccountIdError(
                f"account_id must be a numeric string, got: {account_id!r}"
            )

        table = (
            f"`{self.config.project}.{self.config.summary_dataset}"
            f".facebook_ads__account_performance`"
        )
        query = f"""
            SELECT DISTINCT client_id
            FROM {table}
            WHERE account_id = @account_id
              AND client_id IS NOT NULL
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("account_id", "INT64", int(account_id)),
            ]
        )
        rows = list(self._client.query(query, job_config=job_config).result())
        client_ids = sorted(
            {str(row["client_id"]).strip() for row in rows if row["client_id"]}
            - {""}
        )

        if not client_ids:
            _logger.warning(
                "account_resolution_not_found",
                extra={"account_id": account_id},
            )
            return None
        if len(client_ids) > 1:
            raise AmbiguousAccountError(account_id, client_ids)

        _logger.info(
            "account_resolved",
            extra={"account_id": account_id, "client_id": client_ids[0]},
        )
        return client_ids[0]
