"""Screen an AM's QA sheet for the pilot.

Answers the three questions that decide whether the bot can help an AM, in one
command:
  1. Can the bot even read the sheet? (i.e. is it shared with the service account)
  2. Which Meta account/campaign(s) is it for? (pulled from the in-sheet links)
  3. Is that client synced into our BigQuery? (the hard gate)

AMs use their own QA-doc formats, not our template, so this reads the campaign
link out of the sheet rather than expecting our layout.

Usage:
    source .venv/bin/activate
    python scripts/screen_am_sheet.py '<google-sheet-url-or-key>'
"""
from __future__ import annotations

import re
import sys

import gspread
import google.auth
from google.auth import impersonated_credentials
from google.cloud import bigquery

from app.adapters.bigquery import BigQueryAccountResolver, ResolverConfig

SA = "ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com"
PROJECT = "polaris-data-317717"


def sheet_key(arg: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", arg)
    return m.group(1) if m else arg


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/screen_am_sheet.py '<sheet-url-or-key>'")
        return 2
    key = sheet_key(sys.argv[1])

    src, _ = google.auth.default()
    creds = impersonated_credentials.Credentials(
        source_credentials=src,
        target_principal=SA,
        target_scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)

    try:
        sh = gc.open_by_key(key)
    except Exception as exc:  # noqa: BLE001
        print(f"CANNOT READ THE SHEET ({type(exc).__name__}).")
        print(">>> Almost certainly not shared with the bot. Ask the AM to share it as Editor with:")
        print(f"    {SA}")
        return 1

    print(f"sheet: {sh.title!r}")
    tabs = sh.worksheets()

    # AM sheets embed the campaign as a rich-text hyperlink (not a formula), so
    # pull hyperlinks from the top of every tab in one metadata call.
    ranges = [f"'{w.title}'!A1:J25" for w in tabs]
    accounts: dict[str, set[str]] = {}
    try:
        meta = sh.fetch_sheet_metadata(params={
            "includeGridData": True,
            "ranges": ranges,
            "fields": "sheets.data.rowData.values(hyperlink,textFormatRuns.format.link.uri)",
        })
        for s in meta.get("sheets", []):
            for d in s.get("data", []):
                for row in d.get("rowData", []):
                    for cell in row.get("values", []):
                        link = cell.get("hyperlink") or next(
                            (r.get("format", {}).get("link", {}).get("uri")
                             for r in cell.get("textFormatRuns", []) if r.get("format", {}).get("link")),
                            None,
                        )
                        if link and "act=" in link and "facebook" in link.lower():
                            m1 = re.search(r"act=(\d+)", link)
                            m2 = re.search(r"selected_campaign_ids[^\d]*(\d+)", link)
                            if m1:
                                accounts.setdefault(m1.group(1), set())
                                if m2:
                                    accounts[m1.group(1)].add(m2.group(1))
    except Exception as exc:  # noqa: BLE001
        print(f"(could not read hyperlinks: {str(exc)[:90]})")

    if not accounts:
        print("\nNo Meta campaign links found in the sheet.")
        print("(It may be our own template, a non-Meta sheet, or the campaign link is missing.)")
        return 0

    resolver = BigQueryAccountResolver(config=ResolverConfig(project=PROJECT))
    bq = bigquery.Client(project=PROJECT)
    print(f"\nMeta account(s) referenced: {list(accounts)}")
    any_synced = False
    for acct, camps in accounts.items():
        try:
            cid = resolver.resolve_client_id(acct)
        except Exception:  # noqa: BLE001
            cid = None
        n = list(bq.query(
            f"SELECT COUNT(*) n FROM `{PROJECT}.summary.facebook_ads__account_performance` "
            f"WHERE CAST(account_id AS STRING)='{acct}'"
        ).result())[0]["n"]
        synced = bool(cid and n)
        any_synced = any_synced or synced
        verdict = (f"SYNCED as client {cid} -> the bot CAN QA it"
                   if synced else "NOT synced -> the bot CANNOT QA it yet (needs the new marts)")
        camp_list = sorted(camps)
        print(f"\n  account {acct}: {verdict}")
        print(f"    perf rows: {n}   campaigns in sheet: {camp_list[:5]}{' ...' if len(camp_list) > 5 else ''}")

    print("\nVERDICT:", "eligible for the pilot now" if any_synced else "blocked until the marts (client not in our data)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
