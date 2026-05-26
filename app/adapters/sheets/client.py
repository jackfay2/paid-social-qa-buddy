"""gspread-backed SheetClient. Reads builder expected-values, writes verdicts.

Auth: ADC by default (uses the worker's service account), or a service-account
JSON file/string. The #1 user error is forgetting to share the sheet with the
service account; check_access surfaces that as a specific, actionable error so
the Slack message can tell the builder exactly what to fix.

Conforms to the SheetClient Protocol in app.core.contracts.

Leaner than the Search adapter: no verdict-cell coloring, action-wrap
formatting, or write readback. Core only: access check, read, batch_update write.
Writes are always batched (hard rule: never per-row writes).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.adapters.sheets.parser import (
    SheetTemplateError,
    column_to_a1,
    detect_output_header_map,
    parse_check_rows,
)
from app.core.contracts import SheetAccessResult
from app.models import CheckResult, CheckRow

_logger = logging.getLogger("paid_social_qa_buddy.sheets")

_SHEET_URL_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def extract_sheet_id(sheet_url: str) -> str:
    match = _SHEET_URL_PATTERN.search(sheet_url or "")
    if not match:
        raise ValueError("Invalid Google Sheets URL format")
    return match.group(1)


@dataclass
class GoogleSheetsConfig:
    """Sheet client settings.

    worksheet_name: tab to read/write. Blank -> first sheet.
    auth_mode: "adc" (default) or "service_account".
    service_account_file / service_account_json: used when auth_mode is
        service_account.
    """
    worksheet_name: str = ""
    auth_mode: str = "adc"
    service_account_file: str = ""
    service_account_json: str = ""


class GoogleSheetsClient:
    """SheetClient backed by gspread."""

    def __init__(
        self,
        config: GoogleSheetsConfig | None = None,
        gspread_client: object | None = None,
    ) -> None:
        self.config = config or GoogleSheetsConfig()
        # Inject a ready gspread client in tests; built lazily otherwise.
        self._injected_client = gspread_client

    # --- auth / open -------------------------------------------------------

    def _client(self):
        if self._injected_client is not None:
            return self._injected_client

        import gspread

        mode = (self.config.auth_mode or "adc").strip().lower()
        if mode == "service_account":
            if self.config.service_account_json.strip():
                payload = json.loads(self.config.service_account_json)
                return gspread.service_account_from_dict(payload)
            if self.config.service_account_file.strip():
                return gspread.service_account(filename=self.config.service_account_file)
            raise RuntimeError(
                "auth_mode=service_account requires service_account_file or "
                "service_account_json."
            )

        # Default: ADC.
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(scopes=_SHEETS_SCOPES)
        if getattr(credentials, "expired", False) and getattr(
            credentials, "refresh_token", None
        ):
            credentials.refresh(Request())
        return gspread.authorize(credentials)

    def _open(self, source: str):
        return self._client().open_by_url(source)

    def _select_worksheet(self, spreadsheet):
        if self.config.worksheet_name:
            return spreadsheet.worksheet(self.config.worksheet_name)
        return spreadsheet.sheet1

    # --- SheetClient Protocol ---------------------------------------------

    def check_access(self, source: str) -> SheetAccessResult:
        try:
            extract_sheet_id(source)
            spreadsheet = self._open(source)
            worksheet = self._select_worksheet(spreadsheet)
            _ = worksheet.title
            return SheetAccessResult(ok=True, reason="sheet_accessible")
        except Exception as exc:  # noqa: BLE001 — map every failure to a result
            return self._map_access_error(exc)

    def read_check_rows(self, source: str) -> list[CheckRow]:
        access = self.check_access(source)
        if not access.ok:
            raise SheetTemplateError(
                access.error_code or "sheet_inaccessible", access.reason
            )
        spreadsheet = self._open(source)
        worksheet = self._select_worksheet(spreadsheet)
        table_rows = worksheet.get_all_values()
        return parse_check_rows(table_rows)

    def write_results(
        self,
        source: str,
        results: Sequence[CheckResult],
        qa_initial: str,
        batch: bool = True,
    ) -> None:
        if not results:
            return

        spreadsheet = self._open(source)
        worksheet = self._select_worksheet(spreadsheet)
        table_rows = worksheet.get_all_values()
        _, header_map = detect_output_header_map(table_rows)

        updates: list[dict] = []
        for result in results:
            row = int(result.row_index)
            if row <= 0:
                continue
            updates.append(
                {
                    "range": f"{column_to_a1(header_map['pass_or_fix'])}{row}",
                    "values": [[result.verdict]],
                }
            )
            updates.append(
                {
                    "range": f"{column_to_a1(header_map['action'])}{row}",
                    "values": [[result.action]],
                }
            )
            qa_initial_col = header_map.get("qa_initial")
            if qa_initial_col is not None:
                updates.append(
                    {
                        "range": f"{column_to_a1(qa_initial_col)}{row}",
                        "values": [[qa_initial]],
                    }
                )

        if not updates:
            return

        _logger.info(
            "sheet_write",
            extra={"result_count": len(results), "update_count": len(updates)},
        )
        # Always batched — hard rule: never per-row writes in a loop.
        worksheet.batch_update(updates)

    # --- error mapping -----------------------------------------------------

    @staticmethod
    def _map_access_error(exc: Exception) -> SheetAccessResult:
        message = str(exc).lower()
        if isinstance(exc, ValueError):
            return SheetAccessResult(
                ok=False,
                reason="Could not parse the sheet URL. Use a full Google Sheets URL.",
                error_code="sheet_parse_error",
            )
        if "permission" in message or "forbidden" in message or "403" in message:
            return SheetAccessResult(
                ok=False,
                reason=(
                    "The QA sheet isn't shared with the bot's service account. "
                    "Share it (Editor) with the service account email and re-run."
                ),
                error_code="sheet_permission_denied",
            )
        if "not found" in message or "404" in message:
            return SheetAccessResult(
                ok=False,
                reason="Google Sheet not found. Check the URL.",
                error_code="sheet_not_found",
            )
        return SheetAccessResult(
            ok=False,
            reason="Sheet inaccessible due to an upstream error.",
            error_code="sheet_inaccessible",
        )
