"""Polaris-backed PolarisClient implementation.

Polaris is Wpromote's internal CRM / service directory at
https://api.polaris.wpromote.com. It is NOT a Meta data source. That's
BigQuery (see app.adapters.bigquery). This adapter handles only the
client-directory lookups:
  - Which clients have active Paid Social services
  - Who the recipients are for a given client (team_email + managers +
    accountable_director)

Implementation notes:
  - Auth: `Authorization: Token <api_token>` (DRF TokenAuthentication).
    NOT `Bearer`. Easy mistake to make.
  - Endpoint: GET /core/api/services/?service_type_name=Paid Social
  - Pagination: DRF style — response has count/next/previous/results.
    After page 1, the 'next' URL carries its own query string; clear our
    params dict when following so we don't double up filters.
  - Quirks (from the reference impl in ps-social-daily-health-check):
    * `client.name` is often null even when the client exists.
    * `service_type` field in responses is often null regardless of the
      query-string filter, so don't rely on it. Trust `service_type_name`
      in the params instead.
    * `accountable_director` sometimes wraps a `user` sub-object, sometimes
      is a flat dict with the user fields at the top level. Code defensively.
  - No retry logic. Fail-fast: HTTP errors raise PolarisRequestError.
  - Safety cap: max 50 pages to prevent infinite loops on bad pagination.

Conforms to the PolarisClient Protocol in app.core.contracts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import requests

_logger = logging.getLogger("paid_social_qa_buddy.polaris")

# 50 pages × 200 records/page = 10K records, well above any expected size.
_MAX_PAGES = 50
_DEFAULT_PAGE_SIZE = 200
_DEFAULT_TIMEOUT_SECONDS = 30


class PolarisClientError(Exception):
    """Base for adapter-level errors. Distinguishes our errors from raw requests errors."""


class PolarisAuthError(PolarisClientError):
    """Raised when required config (URL or token) is missing."""


class PolarisRequestError(PolarisClientError):
    """Raised when the HTTP request itself fails (network, HTTP 4xx/5xx)."""


@dataclass(frozen=True)
class PolarisConfig:
    """Polaris connection settings.

    api_url: Base URL (e.g., https://api.polaris.wpromote.com). Trailing
        slash is stripped on use.
    api_token: API token for the `Authorization: Token <token>` header.
    timeout_seconds: Per-request timeout (default 30).
    page_size: Records per page (DRF default 100; we use 200 to halve roundtrips).
    """
    api_url: str
    api_token: str
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    page_size: int = _DEFAULT_PAGE_SIZE


class PolarisClient:
    """Concrete PolarisClient backed by the Polaris REST API.

    No per-instance caching here. The service directory changes rarely;
    the orchestration layer (or check function) can cache results across
    checks within a single QA run if useful.
    """

    def __init__(
        self,
        config: PolarisConfig,
        session: requests.Session | None = None,
    ) -> None:
        if not config.api_url or not config.api_token:
            raise PolarisAuthError(
                "POLARIS_API_URL and POLARIS_API_TOKEN are both required."
            )
        self.config = config
        # Inject session in tests; default uses a fresh requests Session.
        self._session = session or requests.Session()

    def fetch_paid_social_client_ids(self) -> set[str]:
        """Return the set of client_ids with active Paid Social service.

        Filters out services where `enabled` is explicitly False.
        """
        client_ids: set[str] = set()
        for service in self._iter_services(service_type_name="Paid Social"):
            if not service.get("enabled", True):
                continue
            client_id = self._extract_client_id(service)
            if client_id:
                client_ids.add(client_id)
        return client_ids

    def resolve_recipients_for_client(self, client_id: str) -> list[str]:
        """Return the list of recipient emails for a given client_id.

        Pulls from `service.team_email`, `service.managers[].user.email`, and
        `service.accountable_director.user.email` (or the flat-dict variant).
        Dedupes while preserving order of first appearance.
        """
        if not client_id:
            return []

        recipients: list[str] = []
        seen: set[str] = set()
        for service in self._iter_services(service_type_name="Paid Social"):
            if not service.get("enabled", True):
                continue
            if self._extract_client_id(service) != client_id:
                continue
            for email in self._extract_recipient_emails(service):
                if email and email not in seen:
                    recipients.append(email)
                    seen.add(email)
        return recipients

    def _iter_services(self, *, service_type_name: str) -> Iterator[dict[str, Any]]:
        """Walk all pages of /core/api/services/ filtered by service_type_name."""
        headers = {"Authorization": f"Token {self.config.api_token}"}
        params: dict[str, Any] | None = {
            "service_type_name": service_type_name,
            "page_size": str(self.config.page_size),
        }
        url: str | None = f"{self.config.api_url.rstrip('/')}/core/api/services/"
        pages_left = _MAX_PAGES

        while url and pages_left > 0:
            pages_left -= 1
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                raise PolarisRequestError(
                    f"Polaris request failed: {exc}"
                ) from exc

            data = response.json()
            for service in data.get("results", []):
                yield service

            next_url = data.get("next")
            url = next_url if isinstance(next_url, str) and next_url else None
            # After page 1, the 'next' URL already embeds its own query string;
            # clear our params so we don't double-apply filters.
            params = None

        if url and pages_left <= 0:
            _logger.warning(
                "polaris_pagination_safety_limit_reached",
                extra={"max_pages": _MAX_PAGES, "service_type_name": service_type_name},
            )

    @staticmethod
    def _extract_client_id(service: dict[str, Any]) -> str:
        client = service.get("client") or {}
        return str(client.get("id") or "").strip()

    @staticmethod
    def _extract_recipient_emails(service: dict[str, Any]) -> list[str]:
        """Pull recipient emails from a service record.

        Sources:
          - service.team_email (string)
          - service.managers[].user.email (where user.is_active is not False)
          - service.accountable_director.user.email
            OR service.accountable_director.email (flat-dict variant)
        """
        emails: list[str] = []

        team_email = (service.get("team_email") or "").strip()
        if team_email:
            emails.append(team_email)

        for manager in service.get("managers") or []:
            user = manager.get("user") if isinstance(manager, dict) else None
            if not isinstance(user, dict):
                continue
            if user.get("is_active") is False:
                continue
            email = (user.get("email") or "").strip()
            if email:
                emails.append(email)

        ad = service.get("accountable_director")
        if isinstance(ad, dict):
            user = ad.get("user") if isinstance(ad.get("user"), dict) else ad
            email = (user.get("email") or "").strip() if isinstance(user, dict) else ""
            if email:
                emails.append(email)

        return emails
