"""Unit tests for the pure sheet-parsing logic (no gspread)."""

from __future__ import annotations

import pytest

from app.adapters.sheets.parser import (
    SheetTemplateError,
    column_to_a1,
    detect_output_header_map,
    find_column_index,
    normalize_header,
    parse_check_rows,
)

# A header row shaped like Kerri's Meta template (field name in col B, human
# label in col D, builder/output columns to the right).
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


# --- helpers ---------------------------------------------------------------


def test_normalize_header_lowercases_and_collapses() -> None:
    assert normalize_header("  Check_ID ") == "check id"
    assert normalize_header("Pass or  Fix") == "pass or fix"


def test_find_column_index_matches_alias() -> None:
    assert find_column_index(_HEADER, ["check id", "checkid"]) == 0
    assert find_column_index(_HEADER, ["pass or fix"]) == 6
    assert find_column_index(_HEADER, ["nonexistent"]) is None


def test_column_to_a1() -> None:
    assert column_to_a1(0) == "A"
    assert column_to_a1(6) == "G"
    assert column_to_a1(25) == "Z"
    assert column_to_a1(26) == "AA"


# --- parse_check_rows ------------------------------------------------------


def test_parse_check_rows_basic() -> None:
    table = [
        _HEADER,
        ["bid_strategy", "bid_strategy", "", "Bid Strategy", "Lowest cost", "note", "", ""],
        ["objective", "objective", "", "Campaign Objective", "OUTCOME_TRAFFIC", "", "", ""],
    ]
    rows = parse_check_rows(table)

    assert len(rows) == 2
    assert rows[0].check_id == "bid_strategy"
    assert rows[0].builder_input == "Lowest cost"
    assert rows[0].builder_notes == "note"
    # Header at row 1, first data row at row 2.
    assert rows[0].row_index == 2
    assert rows[1].check_id == "objective"
    assert rows[1].row_index == 3


def test_parse_check_rows_skips_repeated_section_headers() -> None:
    """The multi-section Meta template restarts each section with its own
    "Check_ID" header row. Those must NOT parse as a check_id="Check_ID" row
    (which would become 'Error: Unrecognized' and write onto the header row)."""
    table = [
        _HEADER,
        ["campaign_objective", "objective", "", "Campaign Objective", "Sales", "", "", ""],
        # ad-set section restarts with another header row
        ["Check_ID", "facebook_ads__adsets", "", "Ad Set Level", "Builder Input", "Builder Notes", "Pass or Fix", "Action"],
        ["adset_status", "effective_status", "", "Ad Set Status", "Active", "", "", ""],
        # ad section restarts again (CheckID variant)
        ["CheckID", "facebook_ads__ads", "", "Ad Level", "Builder Input", "Builder Notes", "Pass or Fix", "Action"],
        ["ad_status", "effective_status", "", "Ad Status", "Active", "", "", ""],
    ]
    rows = parse_check_rows(table)
    assert [r.check_id for r in rows] == ["campaign_objective", "adset_status", "ad_status"]


def test_parse_check_rows_skips_rows_without_check_id() -> None:
    table = [
        _HEADER,
        ["bid_strategy", "", "", "Bid Strategy", "Lowest cost", "", "", ""],
        ["", "objective", "", "Campaign Objective", "should be skipped", "", "", ""],
        ["budget", "", "", "Budget", "$100/day", "", "", ""],
    ]
    rows = parse_check_rows(table)

    assert [r.check_id for r in rows] == ["bid_strategy", "budget"]
    # The skipped row (index 3) is not present; budget keeps its true row index.
    assert rows[1].row_index == 4


def test_parse_check_rows_handles_header_not_in_first_row() -> None:
    table = [
        ["Meta", "", "", "", "", "", "", ""],
        ["Some preamble", "", "", "", "", "", "", ""],
        _HEADER,
        ["bid_strategy", "", "", "Bid Strategy", "Lowest cost", "", "", ""],
    ]
    rows = parse_check_rows(table)

    assert len(rows) == 1
    # Header is row 3, data row is row 4.
    assert rows[0].row_index == 4


def test_parse_check_rows_handles_short_rows() -> None:
    """A data row with fewer cells than the header shouldn't IndexError."""
    table = [
        _HEADER,
        ["bid_strategy"],  # only the check_id cell present
    ]
    rows = parse_check_rows(table)

    assert len(rows) == 1
    assert rows[0].check_id == "bid_strategy"
    assert rows[0].builder_input == ""


def test_parse_check_rows_raises_when_no_header() -> None:
    table = [
        ["Random", "data", "with", "no", "recognizable", "headers"],
        ["more", "random", "data", "", "", ""],
    ]
    with pytest.raises(SheetTemplateError) as excinfo:
        parse_check_rows(table)
    assert excinfo.value.error_code == "sheet_template_invalid"


def test_parse_check_rows_raises_on_empty_sheet() -> None:
    with pytest.raises(SheetTemplateError):
        parse_check_rows([])


# --- detect_output_header_map ----------------------------------------------


def test_detect_output_header_map_finds_required_columns() -> None:
    _, header_map = detect_output_header_map([_HEADER])
    assert header_map["pass_or_fix"] == 6
    assert header_map["action"] == 7
    # No QA Initial column in this template, so it's absent (not an error).
    assert "qa_initial" not in header_map


def test_detect_output_header_map_finds_optional_qa_initial() -> None:
    header_with_initial = _HEADER + ["QA Initial"]
    _, header_map = detect_output_header_map([header_with_initial])
    assert header_map["qa_initial"] == 8


def test_read_and_write_resolve_same_header_despite_banner_row() -> None:
    """Audit #2: a banner row that matches the OUTPUT columns but not the INPUT
    columns must NOT split read vs write onto different header rows (which would
    write verdicts to the wrong cells). Both paths must anchor to the one true
    header row that has ALL four columns."""
    # A "Pass or Fix / Action" legend banner ABOVE the real header.
    banner = ["Legend", "", "", "", "", "", "Pass or Fix", "Action"]
    table = [
        banner,
        _HEADER,
        ["bid_strategy", "", "", "Bid Strategy", "Lowest cost", "", "", ""],
    ]
    read_rows = parse_check_rows(table)
    write_header_idx, write_map = detect_output_header_map(table)

    # Real header is row index 1; the banner (idx 0) must be ignored by both.
    assert write_header_idx == 1
    assert write_map["pass_or_fix"] == 6 and write_map["action"] == 7
    # The data row's 1-based index (2 rows above it: banner + header) is 3,
    # and the writer's header sits at idx 1 — consistent, no divergence.
    assert read_rows[0].row_index == 3


def test_detect_output_header_map_raises_when_missing_required() -> None:
    bad_header = ["Check_ID", "Builder Input", "Builder Notes"]  # no Pass or Fix / Action
    with pytest.raises(SheetTemplateError):
        detect_output_header_map([bad_header])
