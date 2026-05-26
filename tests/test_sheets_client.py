"""Unit tests for GoogleSheetsClient with a mocked gspread client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.sheets.client import (
    GoogleSheetsClient,
    GoogleSheetsConfig,
    extract_sheet_id,
)
from app.adapters.sheets.parser import SheetTemplateError
from app.models import CheckResult

_URL = "https://docs.google.com/spreadsheets/d/1AbCdEf_-123/edit#gid=0"

_HEADER = [
    "Check_ID",
    "facebook_ads__campaigns",
    "",
    "Campaign Level",
    "Builder Input",
    "Builder Notes",
    "Pass or Fix",
    "Action",
]


def _make_worksheet(values: list[list[str]], title: str = "Meta") -> MagicMock:
    ws = MagicMock()
    ws.title = title
    ws.get_all_values.return_value = values
    return ws


def _make_gspread(worksheet: MagicMock) -> MagicMock:
    spreadsheet = MagicMock()
    spreadsheet.sheet1 = worksheet
    client = MagicMock()
    client.open_by_url.return_value = spreadsheet
    return client


def _make_client(worksheet: MagicMock) -> GoogleSheetsClient:
    return GoogleSheetsClient(
        config=GoogleSheetsConfig(),
        gspread_client=_make_gspread(worksheet),
    )


# --- extract_sheet_id ------------------------------------------------------


def test_extract_sheet_id_valid() -> None:
    assert extract_sheet_id(_URL) == "1AbCdEf_-123"


def test_extract_sheet_id_invalid_raises() -> None:
    with pytest.raises(ValueError):
        extract_sheet_id("https://example.com/not-a-sheet")


# --- check_access ----------------------------------------------------------


def test_check_access_success() -> None:
    client = _make_client(_make_worksheet([_HEADER]))
    result = client.check_access(_URL)
    assert result.ok is True


def test_check_access_invalid_url_returns_parse_error() -> None:
    client = _make_client(_make_worksheet([_HEADER]))
    result = client.check_access("https://example.com/nope")
    assert result.ok is False
    assert result.error_code == "sheet_parse_error"


def test_check_access_permission_denied_is_specific() -> None:
    """The #1 user error — sheet not shared with the service account."""
    gspread_client = MagicMock()
    gspread_client.open_by_url.side_effect = Exception(
        "APIError: 403 PERMISSION_DENIED: The caller does not have permission"
    )
    client = GoogleSheetsClient(
        config=GoogleSheetsConfig(), gspread_client=gspread_client
    )

    result = client.check_access(_URL)
    assert result.ok is False
    assert result.error_code == "sheet_permission_denied"
    assert "service account" in result.reason.lower()


def test_check_access_not_found() -> None:
    gspread_client = MagicMock()
    gspread_client.open_by_url.side_effect = Exception("404 not found")
    client = GoogleSheetsClient(
        config=GoogleSheetsConfig(), gspread_client=gspread_client
    )

    result = client.check_access(_URL)
    assert result.error_code == "sheet_not_found"


# --- read_check_rows -------------------------------------------------------


def test_read_check_rows_returns_parsed_rows() -> None:
    values = [
        _HEADER,
        ["bid_strategy", "", "", "Bid Strategy", "Lowest cost", "", "", ""],
        ["objective", "", "", "Campaign Objective", "OUTCOME_TRAFFIC", "", "", ""],
    ]
    client = _make_client(_make_worksheet(values))
    rows = client.read_check_rows(_URL)

    assert [r.check_id for r in rows] == ["bid_strategy", "objective"]


def test_read_check_rows_raises_on_permission_error() -> None:
    gspread_client = MagicMock()
    gspread_client.open_by_url.side_effect = Exception("403 forbidden")
    client = GoogleSheetsClient(
        config=GoogleSheetsConfig(), gspread_client=gspread_client
    )

    with pytest.raises(SheetTemplateError) as excinfo:
        client.read_check_rows(_URL)
    assert excinfo.value.error_code == "sheet_permission_denied"


# --- write_results ---------------------------------------------------------


def test_write_results_batches_verdict_and_action() -> None:
    worksheet = _make_worksheet([_HEADER])
    client = _make_client(worksheet)

    results = [
        CheckResult(row_index=2, check_id="bid_strategy", verdict="Pass", action=""),
        CheckResult(
            row_index=3,
            check_id="objective",
            verdict="Fix",
            action='Expected "OUTCOME_TRAFFIC", got "OUTCOME_AWARENESS"',
        ),
    ]
    client.write_results(_URL, results, qa_initial="QA-BOT")

    # One batched call, never per-row.
    worksheet.batch_update.assert_called_once()
    updates = worksheet.batch_update.call_args.args[0]

    # 2 results x 2 columns (pass_or_fix=G, action=H), no qa_initial column here.
    ranges = {u["range"] for u in updates}
    assert "G2" in ranges  # bid_strategy verdict
    assert "H2" in ranges  # bid_strategy action
    assert "G3" in ranges  # objective verdict
    assert "H3" in ranges  # objective action


def test_write_results_includes_qa_initial_when_column_present() -> None:
    header_with_initial = _HEADER + ["QA Initial"]  # column I (index 8)
    worksheet = _make_worksheet([header_with_initial])
    client = _make_client(worksheet)

    results = [
        CheckResult(row_index=2, check_id="bid_strategy", verdict="Pass", action=""),
    ]
    client.write_results(_URL, results, qa_initial="QA-BOT")

    updates = worksheet.batch_update.call_args.args[0]
    qa_initial_update = next(u for u in updates if u["range"] == "I2")
    assert qa_initial_update["values"] == [["QA-BOT"]]


def test_write_results_noop_on_empty_results() -> None:
    worksheet = _make_worksheet([_HEADER])
    client = _make_client(worksheet)

    client.write_results(_URL, [], qa_initial="QA-BOT")

    worksheet.batch_update.assert_not_called()
