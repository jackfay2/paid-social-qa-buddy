"""Full QA run against a REAL Google Sheet, authenticating as the worker SA.

This is the milestone that matters: read the QA sheet template, run the checks
against live Meta data, and WRITE the verdicts back into the sheet's "Pass or
Fix" + "Action" columns — exactly what the deployed worker will do.

Auth: we can't use Jack's personal ADC for Sheets (Workspace blocks the
Sheets/Drive OAuth scopes for end users). Instead we impersonate the worker
service account `ppc-qa-buddy@…` — SAs aren't subject to that user-scope
policy, and it's the same identity the deployed Cloud Run worker runs as. This
requires `roles/iam.serviceAccountTokenCreator` on the SA (Jack has it).

The sheet must be shared with the SA email (Editor, so the bot can write):
    ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com

Everything is real here: BigQuery, the checks, the sheet read, the sheet write,
and (optionally) the Slack post. Nothing is stubbed.

Usage:
    source .venv/bin/activate
    # auto-discover a campaign, fill in the sheet:
    python scripts/sheet_run.py --sheet-url 'https://docs.google.com/spreadsheets/d/.../edit'
    # QA a specific campaign:
    python scripts/sheet_run.py --sheet-url '...' --account-id 123 --campaign-id 456
    # pick a worksheet tab:
    python scripts/sheet_run.py --sheet-url '...' --worksheet 'Meta'
    # read + compute but DON'T write back (safe inspection):
    python scripts/sheet_run.py --sheet-url '...' --dry-run
    # also post the summary to Slack:
    SLACK_BOT_TOKEN=$(gcloud secrets versions access latest \
        --secret=test-slack-bot-token --project=prj-prd-ai-ppc-qa-pkph) \
        python scripts/sheet_run.py --sheet-url '...' --channel C0B6ASW9R9V
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import google.auth  # noqa: E402
import gspread  # noqa: E402
from google.auth import impersonated_credentials  # noqa: E402
from google.cloud import bigquery  # noqa: E402

from app.adapters.bigquery import (  # noqa: E402
    BigQueryAccountResolver,
    BigQueryMetaClient,
    BigQueryMetaConfig,
    ResolverConfig,
)
from app.adapters.sheets.client import GoogleSheetsClient, GoogleSheetsConfig  # noqa: E402
from app.adapters.slack import SlackClient, SlackConfig, SlackPostError  # noqa: E402
from app.adapters.storage import InMemoryRunStore  # noqa: E402
from app.checks.registry import run_check  # noqa: E402
from app.core.orchestration import (  # noqa: E402
    OrchestrationRequest,
    SocialQAOrchestrationService,
)

from scripts.local_qa_run import discover_campaign  # noqa: E402

PROJECT = "polaris-data-317717"
WORKER_SA = "ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com"
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def build_impersonated_gspread() -> gspread.Client:
    """Authorize gspread as the worker SA via impersonation (no key file)."""
    source_creds, _ = google.auth.default()
    target_creds = impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal=WORKER_SA,
        target_scopes=SHEETS_SCOPES,
    )
    return gspread.authorize(target_creds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-url", required=True, help="Full Google Sheet URL.")
    parser.add_argument("--worksheet", default="", help="Worksheet/tab name (blank = first).")
    parser.add_argument("--account-id")
    parser.add_argument("--campaign-id")
    parser.add_argument("--channel", default="", help="Optional Slack channel to post summary.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the sheet + run checks, but DON'T write verdicts back.",
    )
    args = parser.parse_args()

    # --- auth as the SA ----------------------------------------------------
    print(f"Authorizing gspread as {WORKER_SA} (impersonation)...")
    try:
        gc = build_impersonated_gspread()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not impersonate {WORKER_SA}: {exc}", file=sys.stderr)
        return 2

    sheet_client = GoogleSheetsClient(
        config=GoogleSheetsConfig(worksheet_name=args.worksheet),
        gspread_client=gc,
    )

    # --- confirm the sheet is reachable + shared with the SA ---------------
    access = sheet_client.check_access(args.sheet_url)
    if not access.ok:
        print(
            f"ERROR: cannot access sheet ({access.error_code}): {access.reason}\n"
            f"  Make sure the sheet is shared with {WORKER_SA} as Editor.",
            file=sys.stderr,
        )
        return 1
    print("✅ sheet reachable and shared with the SA.")

    # --- pick a campaign to QA --------------------------------------------
    bq = bigquery.Client(project=PROJECT)
    campaign_name = ""
    if args.account_id and args.campaign_id:
        account_id, campaign_id = args.account_id, args.campaign_id
    else:
        found = discover_campaign(bq)
        if not found:
            print("No campaign with an objective found.", file=sys.stderr)
            return 1
        account_id = str(found["account_id"])
        campaign_id = str(found["campaign_id"])
        campaign_name = found.get("name", "") or ""
        print(
            f"Discovered campaign: account_id={account_id} campaign_id={campaign_id} "
            f"objective={found['objective']} buying_type={found.get('buying_type')} "
            f"name={campaign_name!r}"
        )

    # --- show what rows we read from the sheet -----------------------------
    rows = sheet_client.read_check_rows(args.sheet_url)
    print(f"\nRead {len(rows)} check row(s) from the sheet:")
    for r in rows:
        print(f"  row {r.row_index}: {r.check_id} = {r.builder_input!r}")
    if not rows:
        print(
            "  (no rows with a check_id found — confirm column A has check_ids "
            "and a header row reads 'Check_ID' / 'Builder Input')",
            file=sys.stderr,
        )

    if args.dry_run:
        # Run checks but don't write back — useful first pass.
        from app.adapters.bigquery import BigQueryMetaClient as _MC

        meta = _MC(config=BigQueryMetaConfig(project=PROJECT))
        resolver = BigQueryAccountResolver(config=ResolverConfig(project=PROJECT))
        client_id = resolver.resolve_client_id(account_id)
        evidence = {
            "campaign": meta.get_campaign(client_id, campaign_id),
            "ad_sets": meta.get_ad_sets(client_id, campaign_id),
            "ads": meta.get_ads(client_id, campaign_id),
            "client_id": client_id,
            "campaign_id": campaign_id,
        }
        from app.core.pipeline import execute_checks

        results = execute_checks(rows, run_check, evidence=evidence)
        print("\n[dry-run] verdicts (NOT written to the sheet):")
        for r in results:
            print(f"  row {r.row_index} {r.check_id}: {r.verdict} — {r.action or '(ok)'}")
        return 0

    # --- full orchestration: read -> check -> WRITE back to the sheet ------
    service = SocialQAOrchestrationService(
        run_store=InMemoryRunStore(),
        resolver=BigQueryAccountResolver(config=ResolverConfig(project=PROJECT)),
        meta_client=BigQueryMetaClient(config=BigQueryMetaConfig(project=PROJECT)),
        sheet_client=sheet_client,
        check_runner=run_check,
    )
    request = OrchestrationRequest(
        request_id="sheet-run-1",
        account_id=account_id,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        sheet_url=args.sheet_url,
        channel_id=args.channel,
    )

    print("\nRunning full orchestration (will WRITE verdicts to the sheet)...")
    result = service.run(request)

    print("\n=== result ===")
    print(f"status:  {result.status}")
    print(f"summary: {result.summary_counts}")
    print(f"resolved client_id: {result.resolved_client_id}")
    print(f"message: {result.message}")
    if result.error_code:
        print(f"error_code: {result.error_code}")
    if result.status == "completed":
        print("\n✅ Verdicts written back to the sheet — open it to see the filled-in columns.")

    # --- optional Slack post ----------------------------------------------
    if args.channel:
        token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
        if not token:
            print(
                "\n(--channel given but SLACK_BOT_TOKEN not set; skipping Slack post)",
                file=sys.stderr,
            )
        else:
            try:
                SlackClient(config=SlackConfig(bot_token=token)).post_thread_message(
                    channel_id=args.channel, text=result.message,
                )
                print(f"✅ Posted summary to Slack channel {args.channel}.")
            except SlackPostError as exc:
                print(f"Slack post failed: {exc.code} — {exc.message}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
