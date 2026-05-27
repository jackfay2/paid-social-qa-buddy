"""Live data-layer smoke check against real BigQuery.

NOT a unit test. Run manually with ADC:
    gcloud auth application-default login   # once
    source .venv/bin/activate
    python scripts/live_check.py

It exercises the path that's only ever been run against mocks so far:
  resolver (account_id -> client_id) -> meta client (evidence fetch) ->
  campaign_objective / campaign_buying_type checks against real Meta values.

Usage:
    python scripts/live_check.py
        Auto-finds a client that has Meta data, then a campaign with an objective.

    python scripts/live_check.py --dataset C00085144
        Discover from a specific client dataset.

    python scripts/live_check.py --account-id 1234567890 --campaign-id 9876543210
        Run against specific IDs (skips discovery).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Make `app` importable when run as `python scripts/live_check.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import bigquery  # noqa: E402

from app.adapters.bigquery import (  # noqa: E402
    BigQueryAccountResolver,
    BigQueryMetaClient,
    BigQueryMetaConfig,
    ResolverConfig,
)
from app.checks.registry import run_check  # noqa: E402
from app.models import CheckRow  # noqa: E402

PROJECT = "polaris-data-317717"


def _dump(label: str, value: Any) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(value, indent=2, default=str))


def _campaigns_with_objective(client: bigquery.Client, dataset: str) -> list[dict[str, Any]]:
    """Return up to 5 campaigns (with objective) from a client dataset, or []."""
    query = f"""
        SELECT account_id, id, campaign_id, name, objective, buying_type
        FROM `{PROJECT}.{dataset}.facebook_ads__campaigns`
        WHERE objective IS NOT NULL
        LIMIT 5
    """
    try:
        return [dict(r) for r in client.query(query).result()]
    except Exception as exc:  # dataset may lack the table
        print(f"  ({dataset}: {exc})")
        return []


def auto_find_campaign(client: bigquery.Client) -> tuple[str, list[dict[str, Any]]]:
    """Find a client with Meta spend, then a campaign that HAS ad sets.

    Ranks clients by Facebook activity (summary perf table), then for each finds
    the campaign with the most ad sets — so the re-run validates get_ad_sets /
    get_ads returning real rows, not the empty lists an old paused campaign gives.
    """
    candidates_query = f"""
        SELECT client_id, COUNT(*) AS perf_rows
        FROM `{PROJECT}.summary.facebook_ads__account_performance`
        WHERE client_id IS NOT NULL
        GROUP BY client_id
        ORDER BY perf_rows DESC
        LIMIT 25
    """
    candidates = [str(r["client_id"]).strip() for r in client.query(candidates_query).result()]
    candidates = [c for c in candidates if c]
    print(f"Top Meta-active clients (by performance rows): {candidates[:10]}")

    for client_id in candidates:
        # Find the campaign with the most ad sets in this client.
        adsets_query = f"""
            SELECT campaign_id, COUNT(*) AS adset_count
            FROM `{PROJECT}.{client_id}.facebook_ads__adsets`
            WHERE campaign_id IS NOT NULL
            GROUP BY campaign_id
            ORDER BY adset_count DESC
            LIMIT 1
        """
        try:
            adset_rows = list(client.query(adsets_query).result())
        except Exception as exc:  # dataset may lack the table
            print(f"  ({client_id}: {exc})")
            continue
        if not adset_rows:
            continue

        target_campaign_id = int(adset_rows[0]["campaign_id"])  # int from BQ, safe to inline
        adset_count = adset_rows[0]["adset_count"]
        campaign_query = f"""
            SELECT account_id, id, campaign_id, name, objective, buying_type, effective_status
            FROM `{PROJECT}.{client_id}.facebook_ads__campaigns`
            WHERE campaign_id = {target_campaign_id}
            LIMIT 1
        """
        rows = [dict(r) for r in client.query(campaign_query).result()]
        if rows:
            print(f"\nFound campaign with {adset_count} ad sets in {client_id}:")
            for r in rows:
                print(
                    f"  account_id={r['account_id']} campaign_id={r['campaign_id']} "
                    f"status={r['effective_status']} objective={r['objective']} "
                    f"buying_type={r['buying_type']} name={r['name']!r}"
                )
            return client_id, rows
    return "", []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--account-id")
    parser.add_argument("--campaign-id")
    args = parser.parse_args()

    bq = bigquery.Client(project=PROJECT)

    account_id = args.account_id
    campaign_id = args.campaign_id

    if not (account_id and campaign_id):
        if args.dataset:
            rows = _campaigns_with_objective(bq, args.dataset)
            if rows:
                print(f"Campaigns in {args.dataset}:")
                for r in rows:
                    print(f"  account_id={r['account_id']} id={r['id']} "
                          f"campaign_id={r['campaign_id']} objective={r['objective']}")
        else:
            _, rows = auto_find_campaign(bq)

        if not rows:
            print("\nNo Meta campaigns with an objective found. Try `--dataset C<id>`.")
            return

        discovered = rows[0]
        account_id = str(discovered["account_id"])
        campaign_id = str(discovered["campaign_id"])
        if discovered["id"] != discovered["campaign_id"]:
            print(
                "\n*** NOTE: id != campaign_id here. get_campaign() filters on `id` "
                "but get_ad_sets/get_ads filter on `campaign_id`. If the campaign "
                "comes back empty below, that's the bug. ***"
            )

    print(f"\nUsing account_id={account_id} campaign_id={campaign_id}")

    # 1. Resolver: account_id -> client_id (should round-trip to the discovered dataset).
    resolver = BigQueryAccountResolver(config=ResolverConfig(project=PROJECT))
    client_id = resolver.resolve_client_id(account_id)
    print(f"\nResolved client_id: {client_id}")
    if not client_id:
        print(
            "Resolver returned None — account has no rows in "
            "summary.facebook_ads__account_performance."
        )
        return

    # 2. Meta client: fetch the evidence triplet.
    meta = BigQueryMetaClient(config=BigQueryMetaConfig(project=PROJECT))
    campaign = meta.get_campaign(client_id, campaign_id)
    ad_sets = meta.get_ad_sets(client_id, campaign_id)
    ads = meta.get_ads(client_id, campaign_id)

    _dump("campaign", campaign)
    if not campaign:
        print(
            "\n*** get_campaign returned empty — likely the id-vs-campaign_id issue. "
            "Fix: get_campaign should filter on campaign_id, not id. ***"
        )
    print(f"\nad_sets: {len(ad_sets)} rows")
    if ad_sets:
        _dump("ad_sets[0]", ad_sets[0])
    print(f"\nads: {len(ads)} rows")
    if ads:
        _dump("ads[0]", ads[0])

    evidence = {
        "client_id": client_id,
        "campaign_id": campaign_id,
        "campaign": campaign,
        "ad_sets": ad_sets,
        "ads": ads,
    }

    # 3. Run the two campaign checks against the real evidence.
    actual_objective = campaign.get("objective")
    actual_buying_type = campaign.get("buying_type")
    print("\n=== check runs (against real evidence) ===")
    cases = [
        ("campaign_objective", str(actual_objective)),
        ("campaign_objective", "Traffic"),
        ("campaign_objective", "Sales"),
        ("campaign_buying_type", str(actual_buying_type)),
    ]
    for check_id, builder_input in cases:
        row = CheckRow(row_index=2, check_id=check_id, builder_input=builder_input)
        result = run_check(row, evidence=evidence)
        print(f"  {check_id} (builder_input={builder_input!r}) -> {result.verdict}: {result.action}")


if __name__ == "__main__":
    main()
