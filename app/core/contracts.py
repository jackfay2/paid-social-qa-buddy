"""Backing-service interfaces. 12-factor IV: treat backing services as attached resources.

Check functions and orchestration code depend on these Protocols, not on concrete
implementations. Swapping BigQuery for direct Meta API later (or anything else) is
a constructor-argument change, not a refactor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models import CheckResult, CheckRow


@dataclass
class SheetAccessResult:
    ok: bool
    reason: str = ""
    error_code: str = ""


@dataclass
class RunRecord:
    """Run-tracking record persisted to Firestore. Fields will expand as the
    orchestration lands; keep this aligned with the Search repo's RunRecord
    where the lifecycle fields are shared."""
    run_id: str
    request_id: str = ""
    status: str = "accepted"  # accepted | validating | running | completed | failed | rejected
    message: str = ""
    sheet_url: str = ""
    account_id: str = ""
    campaign_id: str = ""
    campaign_name: str = ""
    thread_ts: str = ""
    channel_id: str = ""
    pass_count: int = 0
    fix_count: int = 0
    review_count: int = 0
    na_count: int = 0
    error_count: int = 0
    error_codes: list[str] = field(default_factory=list)
    fix_items: list[str] = field(default_factory=list)


class SheetClient(Protocol):
    """QA sheet I/O. Concrete impl: gspread against Google Sheets."""
    def check_access(self, source: str) -> SheetAccessResult: ...
    def read_check_rows(self, source: str) -> list[CheckRow]: ...
    def write_results(
        self,
        source: str,
        results: Sequence[CheckResult],
        qa_initial: str,
        batch: bool = True,
    ) -> None: ...


class MetaDataClient(Protocol):
    """Meta campaign / ad-set / ad data retrieval.

    Concrete impl in Phase 1: BigQuery against polaris-data-317717.C<client_id>.facebook_ads__*
    (Airbyte-synced daily from the Meta Marketing API). Daily-stale.

    Future impls can swap in direct Meta API for fresh-launch cases.
    """
    def get_campaign(self, client_id: str, campaign_id: str) -> dict[str, Any]: ...
    def get_ad_sets(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]: ...
    def get_ads(self, client_id: str, campaign_id: str) -> list[dict[str, Any]]: ...


class PolarisClient(Protocol):
    """Wpromote client directory lookups. Polaris is NOT a Meta data source;
    it tells us which clients have Paid Social service and who to email.

    Reference impl: core/recipients.py in ps-social-daily-health-check.
    Auth header is `Token <token>` (DRF TokenAuthentication), NOT `Bearer`.
    """
    def fetch_paid_social_client_ids(self) -> set[str]: ...
    def resolve_recipients_for_client(self, client_id: str) -> list[str]: ...


class SlackClient(Protocol):
    """Posts to a Slack thread. Uses the shared @qa-buddy bot token."""
    def post_thread_message(
        self, *, channel_id: str, thread_ts: str, text: str,
    ) -> None: ...


class RunStore(Protocol):
    """Run-tracking persistence. Concrete impl: Firestore (collection qa_runs)."""
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def create_run(self, record: RunRecord) -> None: ...
    def update_run(self, record: RunRecord) -> None: ...
    def find_by_request_id(self, request_id: str) -> RunRecord | None: ...
    def has_worker_notification(self, request_id: str) -> bool: ...
    def mark_worker_notification(self, request_id: str) -> None: ...


class GeminiClient(Protocol):
    """Batched text-check classification. One call per job, all text checks batched.

    Default to Review on timeout, low confidence, or malformed response.
    Never auto-Pass on Gemini uncertainty (Peacock-Olympics rule).
    """
    def run_text_checks(self, batch: list[dict[str, Any]]) -> dict[str, Any]: ...
