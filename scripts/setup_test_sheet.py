"""Create a throwaway QA test sheet in YOUR Google Drive, populated with the
header + check rows the worker expects. Run once; it prints the sheet URL.

Requires ADC with Sheets + Drive scopes (standard ADC login only grants cloud
scope, which isn't enough for Sheets):

    gcloud auth application-default login --scopes=https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform

    source .venv/bin/activate
    python scripts/setup_test_sheet.py

Safety: this creates ONE new spreadsheet in your own Google Drive and writes a
few cells into it. It touches nothing else — no GCP project, no production
sheets, no repo state. The sheet is owned by your account, so the local worker
(same ADC credentials) can read and write it.
"""

from __future__ import annotations

import google.auth
import gspread

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_TITLE = "Paid Social QA — Local Test Sheet"

# Header row the bot detects by column name, then two check rows. Builder Input
# is the "expected" value you're testing against the real campaign. Pass or Fix
# and Action are left blank — the bot writes verdicts into them.
_ROWS = [
    ["Check_ID", "Builder Input", "Builder Notes", "Pass or Fix", "Action"],
    ["campaign_objective", "Sales", "", "", ""],
    ["campaign_buying_type", "Auction", "", "", ""],
]


def main() -> None:
    credentials, _ = google.auth.default(scopes=_SCOPES)
    gc = gspread.authorize(credentials)

    sheet = gc.create(_TITLE)
    worksheet = sheet.sheet1
    # batch_update matches the write pattern the worker's sheet adapter uses.
    worksheet.batch_update(
        [{"range": f"A1:E{len(_ROWS)}", "values": _ROWS}]
    )

    print("Created test sheet:")
    print(f"  Title: {_TITLE}")
    print(f"  URL:   {sheet.url}")
    print("\nUse that URL as the sheet_url when we run the local QA.")


if __name__ == "__main__":
    main()
