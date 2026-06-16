"""Go-live check for the new Meta data marts (e.g. prj-npd-plrs-tst-marts-onfd).

Run this the moment we have read access. It probes the new project against what
the bot's adapter actually needs and prints a verdict: is the repoint a pure
config swap (just point BigQueryMetaConfig.project / ResolverConfig.project at
the new project) or does it need code?

Runs AS the bot's service account, so it tests the bot's access, not just yours.

Usage:
    source .venv/bin/activate
    python scripts/verify_new_marts.py
    python scripts/verify_new_marts.py --project prj-prd-...-marts-... \
        --account 955066737173306 --campaign 120245902610470176

What the adapter expects (from app/adapters/bigquery): one dataset per client
named C<digits>; each has facebook_ads__campaigns / __adsets / __adset_targetings
/ __ads / __ad_creatives; account_id -> client_id resolves via
<project>.summary.facebook_ads__account_performance.
"""
from __future__ import annotations

import argparse
import sys

import google.auth
from google.auth import impersonated_credentials
from google.cloud import bigquery

SA = "ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com"
DEFAULT_PROJECT = "prj-npd-plrs-tst-marts-onfd"
DEFAULT_BILLING = "prj-prd-ai-ppc-qa-pkph"
EXPECTED_TABLES = [
    "facebook_ads__campaigns",
    "facebook_ads__adsets",
    "facebook_ads__adset_targetings",
    "facebook_ads__ads",
    "facebook_ads__ad_creatives",
]
# the columns whose absence/nullness currently forces checks to manual Review
GATED = [
    "promoted_object",
    "optimization_goal",
    "attribution_spec",
    "daily_min_spend_target",
    "daily_spend_cap",
    "bid_strategy",
]


def bq_client(billing):
    src, _ = google.auth.default()
    creds = impersonated_credentials.Credentials(
        source_credentials=src,
        target_principal=SA,
        target_scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=billing, credentials=creds)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--billing", default=DEFAULT_BILLING)
    ap.add_argument("--account", default="955066737173306")
    ap.add_argument("--campaign", default="120245902610470176")
    a = ap.parse_args()
    P = a.project
    gaps: list[str] = []

    # pick a billing project the SA can run jobs in
    bq = None
    for billing in (a.billing, P, "polaris-data-317717"):
        try:
            c = bq_client(billing)
            list(c.query("SELECT 1").result())
            bq = c
            print(f"[billing] running jobs as the SA in {billing}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[billing] {billing} unusable: {str(exc)[:90]}")
    if bq is None:
        print("No usable billing project for the SA; abort.")
        return 2

    # 1. access + per-client dataset shape. A project we lack access to returns
    # an EMPTY dataset list (not an error), so treat "nothing visible" as
    # not-granted-yet rather than a structural finding.
    try:
        ds = [d.dataset_id for d in bq.list_datasets(project=P)]
    except Exception as exc:  # noqa: BLE001
        ds = []
        print(f"\n[access] list_datasets error: {type(exc).__name__}: {str(exc)[:120]}")
    if not ds:
        print("\n>>> No datasets visible in this project. Access almost certainly isn't granted yet.")
        print(">>> Re-run the moment access lands (after go-live) and this will give the real repoint verdict.")
        return 1
    cds = [d for d in ds if d.startswith("C") and d[1:].isdigit()]
    print(f"\n[access] OK. {len(ds)} datasets; {len(cds)} look like C<client_id> (e.g. {cds[:5]})")
    if not cds:
        gaps.append("datasets visible but none named C<client_id> (different layout than the adapter expects)")
    sample = cds[0] if cds else ds[0]

    # 2. expected tables in a sample client dataset
    if sample:
        try:
            tbls = {t.table_id for t in bq.list_tables(f"{P}.{sample}")}
            missing = [t for t in EXPECTED_TABLES if t not in tbls]
            print(f"\n[tables@{sample}] present: {sorted(t for t in EXPECTED_TABLES if t in tbls)}")
            if missing:
                print(f"   MISSING: {missing}")
                gaps.append(f"missing expected tables in {sample}: {missing}")
        except Exception as exc:  # noqa: BLE001
            print(f"[tables] error: {str(exc)[:110]}")

    # 3. resolver source (account_id -> client_id)
    try:
        n = list(bq.query(f"SELECT COUNT(*) n FROM `{P}.summary.facebook_ads__account_performance`").result())[0]["n"]
        print(f"\n[resolver] summary.facebook_ads__account_performance present, {n} rows")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[resolver] NOT found: {str(exc)[:110]}")
        gaps.append("no summary.facebook_ads__account_performance (resolver needs a new source/dataset)")

    # 4. the gated fields: present? populated?
    if sample:
        try:
            cols = {f.name for f in bq.get_table(f"{P}.{sample}.facebook_ads__adsets").schema}
            present = [g for g in GATED if g in cols]
            absent = [g for g in GATED if g not in cols]
            print(f"\n[gated fields@{sample}.adsets] present: {present}")
            if absent:
                print(f"   still absent: {absent}")
            if present:
                sel = ", ".join(f"COUNTIF({g} IS NOT NULL) AS {g}" for g in present)
                row = dict(list(bq.query(f"SELECT {sel}, COUNT(*) AS total FROM `{P}.{sample}.facebook_ads__adsets`").result())[0])
                print(f"   non-null counts: {row}")
        except Exception as exc:  # noqa: BLE001
            print(f"[gated fields] error: {str(exc)[:110]}")

    # 5. Kendra Scott reachable? (the pilot client missing from polaris)
    print(f"\n[Kendra Scott] account {a.account} / campaign {a.campaign}")
    try:
        r = list(bq.query(
            f"SELECT client_id FROM `{P}.summary.facebook_ads__account_performance` "
            f"WHERE CAST(account_id AS STRING)='{a.account}' LIMIT 1"
        ).result())
        if r:
            ks = r[0]["client_id"]
            print(f"   resolves to client_id {ks}")
            for tbl in ("facebook_ads__campaigns", "facebook_ads__adsets", "facebook_ads__ads"):
                n = list(bq.query(f"SELECT COUNT(*) n FROM `{P}.{ks}.{tbl}` WHERE CAST(campaign_id AS STRING)='{a.campaign}'").result())[0]["n"]
                print(f"   {tbl}: {n} rows for the Yellow Rose campaign")
        else:
            print("   not resolvable here (account absent from this project's resolver source)")
            gaps.append("Kendra Scott still not resolvable in the new marts")
    except Exception as exc:  # noqa: BLE001
        print(f"   check error: {str(exc)[:110]}")

    # verdict
    print("\n==== REPOINT VERDICT ====")
    if not gaps:
        print(f"CONFIG SWAP. Structure matches what the adapter reads. Point it at the new project:")
        print(f"   BQ_META_PROJECT={P}  (set BigQueryMetaConfig.project + ResolverConfig.project)")
        print("   No adapter code change indicated. Then re-run the standard validation.")
    else:
        print("NEEDS WORK before repoint:")
        for g in gaps:
            print(f"   - {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
