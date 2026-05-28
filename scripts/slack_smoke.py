"""Live worker → real Slack post, into the test Slack workspace.

This is the achievable "test in the test Slack workspace" milestone before GCP
provisioning lands. It runs the SAME orchestration the deployed worker will run:

    resolve account_id -> client_id   (live BigQuery)
    fetch Meta evidence               (live BigQuery)
    run the deterministic checks       (real registry)
    -> post the QA summary to a real Slack channel in the test workspace

What's REAL here: BigQuery, the checks, the verdicts, and the Slack post.
What's stubbed (because those legs need GCP provisioning that isn't done yet):
the Slack @-mention trigger and the Cloud Tasks queue. So this proves the
worker->Slack leg end-to-end without the listener or the queue.

The Google Sheet is kept in-memory (live Sheets auth is still gated on the SA
JSON landing in Secret Manager). Verdicts are computed from REAL BigQuery data
and both written to the in-memory sheet AND posted to Slack.

SECURITY: the bot token is read from the SLACK_BOT_TOKEN env var only — never a
CLI arg (args show up in shell history and `ps`). Put it in .env (gitignored)
or export it for the session.

Usage:
    source .venv/bin/activate
    export SLACK_BOT_TOKEN=xoxb-...            # test-workspace bot token
    python scripts/slack_smoke.py --channel C0123ABCD            # auto-find campaign
    python scripts/slack_smoke.py --channel C0123ABCD \
        --account-id 123 --campaign-id 456                       # specific campaign
    python scripts/slack_smoke.py --channel C0123ABCD --thread-ts 1716...  # reply in thread
    python scripts/slack_smoke.py --channel C0123ABCD --dry-run  # build msg, DON'T post
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import bigquery  # noqa: E402

from app.adapters.bigquery import (  # noqa: E402
    BigQueryAccountResolver,
    BigQueryMetaClient,
    BigQueryMetaConfig,
    ResolverConfig,
)
from app.adapters.slack import SlackClient, SlackConfig, SlackPostError  # noqa: E402
from app.adapters.storage import InMemoryRunStore  # noqa: E402
from app.checks.registry import run_check  # noqa: E402
from app.core.orchestration import (  # noqa: E402
    OrchestrationRequest,
    SocialQAOrchestrationService,
)

# Reuse the in-memory sheet + campaign discovery from the existing local runner
# so the two scripts stay consistent.
from scripts.local_qa_run import (  # noqa: E402
    _CHECK_ROWS,
    InMemorySheetClient,
    discover_campaign,
)

PROJECT = "polaris-data-317717"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel",
        required=True,
        help="Slack channel ID in the test workspace (e.g. C0123ABCD). The bot "
        "must be a member of this channel.",
    )
    parser.add_argument(
        "--thread-ts",
        default="",
        help="Optional thread ts to reply into. Omit to post top-level.",
    )
    parser.add_argument("--account-id")
    parser.add_argument("--campaign-id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full QA and print the message, but DON'T post to Slack.",
    )
    args = parser.parse_args()

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not bot_token and not args.dry_run:
        print(
            "ERROR: SLACK_BOT_TOKEN is not set. Export the test-workspace bot "
            "token (xoxb-...) or use --dry-run.",
            file=sys.stderr,
        )
        return 2

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
            f"Discovered: account_id={account_id} campaign_id={campaign_id} "
            f"objective={found['objective']} name={campaign_name!r}"
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
        request_id="slack-smoke-1",
        account_id=account_id,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        sheet_url="memory://test-sheet",
        channel_id=args.channel,
        thread_ts=args.thread_ts,
    )

    print("\nRunning full orchestration against live BigQuery...")
    result = service.run(request)

    print("\n=== orchestration result ===")
    print(f"status:  {result.status}")
    print(f"summary: {result.summary_counts}")
    print(f"resolved client_id: {result.resolved_client_id}")

    print("\n=== message to post ===")
    print(result.message)

    if args.dry_run:
        print("\n[dry-run] Not posting to Slack.")
        return 0

    slack = SlackClient(config=SlackConfig(bot_token=bot_token))
    try:
        slack.post_thread_message(
            channel_id=args.channel,
            thread_ts=args.thread_ts,
            text=result.message,
        )
    except SlackPostError as exc:
        print(
            f"\nERROR posting to Slack: {exc.code} — {exc.message}\n"
            f"  transient={SlackClient.is_transient(exc)}\n"
            "  Common causes: bot not invited to the channel (channel_not_found "
            "/ not_in_channel), wrong token (invalid_auth), or missing chat:write "
            "scope (missing_scope).",
            file=sys.stderr,
        )
        return 1

    where = f"thread {args.thread_ts}" if args.thread_ts else "channel top-level"
    print(f"\n✅ Posted QA summary to {args.channel} ({where}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
