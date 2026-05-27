"""Local end-to-end QA run against REAL BigQuery, with the Google Sheet simulated
in memory (Sheets OAuth scopes are blocked on this workspace, so the worker can't
read/write a live sheet via user ADC yet).

Runs the full SocialQAOrchestrationService: resolve account_id -> client_id, fetch
Meta evidence from BigQuery, "read" the check rows (from an in-memory stand-in for
the sheet), run the checks, "write" verdicts (captured + printed), and summarize.

Everything EXCEPT the live Google Sheets call is real. Swap InMemorySheetClient
for the real GoogleSheetsClient once service-account sheet auth is available.

NOT a unit test. Run manually with ADC:
    source .venv/bin/activate
    python scripts/local_qa_run.py
        Auto-discovers a campaign with an objective and runs the checks against it.

    python scripts/local_qa_run.py --account-id 123 --campaign-id 456
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import bigquery  # noqa: E402

from app.adapters.bigquery import (  # noqa: E402
    BigQueryAccountResolver,
    BigQueryMetaClient,
    BigQueryMetaConfig,
    ResolverConfig,
)
from app.adapters.storage import InMemoryRunStore  # noqa: E402
from app.checks.registry import run_check  # noqa: E402
from app.core.contracts import SheetAccessResult  # noqa: E402
from app.core.orchestration import (  # noqa: E402
    OrchestrationRequest,
    SocialQAOrchestrationService,
)
from app.models import CheckResult, CheckRow  # noqa: E402

PROJECT = "polaris-data-317717"

# The rows that would come from the test sheet (col A = check_id, col B = builder input).
_CHECK_ROWS = [
    CheckRow(row_index=2, check_id="campaign_objective", builder_input="Sales"),
    CheckRow(row_index=3, check_id="campaign_buying_type", builder_input="Auction"),
]


class InMemorySheetClient:
    """Stand-in for GoogleSheetsClient: serves predefined rows, captures writes.

    Lets us exercise the full orchestration (including the write step) without a
    live Google Sheets call. Conforms to the SheetClient Protocol.
    """

    def __init__(self, rows: list[CheckRow]) -> None:
        self._rows = rows
        self.written: list[CheckResult] = []

    def check_access(self, source: str) -> SheetAccessResult:
        return SheetAccessResult(ok=True, reason="in-memory")

    def read_check_rows(self, source: str) -> list[CheckRow]:
        return list(self._rows)

    def write_results(
        self, source: str, results: Sequence[CheckResult],
        qa_initial: str, batch: bool = True,
    ) -> None:
        self.written = list(results)


def discover_campaign(bq: bigquery.Client) -> dict[str, Any] | None:
    """Find a campaign with an objective from a Meta-active client."""
    candidates_query = f"""
        SELECT client_id, COUNT(*) AS perf_rows
        FROM `{PROJECT}.summary.facebook_ads__account_performance`
        WHERE client_id IS NOT NULL
        GROUP BY client_id ORDER BY perf_rows DESC LIMIT 25
    """
    for row in bq.query(candidates_query).result():
        client_id = str(row["client_id"]).strip()
        if not client_id:
            continue
        campaign_query = f"""
            SELECT account_id, campaign_id, name, objective, buying_type
            FROM `{PROJECT}.{client_id}.facebook_ads__campaigns`
            WHERE objective IS NOT NULL LIMIT 1
        """
        try:
            rows = [dict(r) for r in bq.query(campaign_query).result()]
        except Exception:
            continue
        if rows:
            return rows[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id")
    parser.add_argument("--campaign-id")
    args = parser.parse_args()

    bq = bigquery.Client(project=PROJECT)

    campaign_name = ""
    if args.account_id and args.campaign_id:
        account_id, campaign_id = args.account_id, args.campaign_id
    else:
        found = discover_campaign(bq)
        if not found:
            print("No campaign with an objective found.")
            return
        account_id = str(found["account_id"])
        campaign_id = str(found["campaign_id"])
        campaign_name = found.get("name", "") or ""
        print(
            f"Discovered: account_id={account_id} campaign_id={campaign_id} "
            f"objective={found['objective']} buying_type={found['buying_type']} "
            f"name={campaign_name!r}"
        )

    sheet = InMemorySheetClient(_CHECK_ROWS)
    service = SocialQAOrchestrationService(
        run_store=InMemoryRunStore(),
        resolver=BigQueryAccountResolver(config=ResolverConfig(project=PROJECT)),
        meta_client=BigQueryMetaClient(config=BigQueryMetaConfig(project=PROJECT)),
        sheet_client=sheet,
        check_runner=run_check,
    )
    request = OrchestrationRequest(
        request_id="local-test-1",
        account_id=account_id,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        sheet_url="memory://test-sheet",
    )

    print("\nRunning full orchestration against live BigQuery...")
    result = service.run(request)

    print("\n=== orchestration result ===")
    print(f"status:  {result.status}")
    print(f"message: {result.message}")
    print(f"summary: {result.summary_counts}")
    print(f"resolved client_id: {result.resolved_client_id}")
    if result.error_code:
        print(f"error_code: {result.error_code}")

    print("\n=== verdicts written to the (simulated) sheet ===")
    for r in sheet.written:
        print(f"  row {r.row_index}  {r.check_id}: {r.verdict}  — {r.action or '(no action)'}")


if __name__ == "__main__":
    main()
