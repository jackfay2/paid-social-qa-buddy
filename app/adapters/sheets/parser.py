"""Pure parsing logic for QA sheets. No gspread dependency, so it's trivially testable.

Header detection is alias-based (mirrors the Search repo): scan the first N rows
for a header row containing the required columns, matched by normalized header
text rather than fixed positions. This is robust to the Social template's exact
column layout still being in flux — as long as the headers read "Check_ID",
"Builder Input", "Pass or Fix", "Action", the positions can move.

Row index convention: CheckRow.row_index is the 1-based spreadsheet row number,
so the writer can target the same cell the value was read from (idempotency).
"""

from __future__ import annotations

from app.models import CheckRow

_HEADER_SCAN_LIMIT = 40

# Columns needed to build a CheckRow from the sheet.
_INPUT_REQUIRED_ALIASES = {
    "check_id": ["check id", "checkid"],
    "builder_input": ["builder input"],
}
_INPUT_OPTIONAL_ALIASES = {
    "builder_notes": ["builder notes"],
    "campaign_setting": ["campaign settings", "campaign setting", "settings"],
}

# Columns the bot writes verdicts into.
_OUTPUT_REQUIRED_ALIASES = {
    "pass_or_fix": ["pass or fix", "pass/fix"],
    "action": ["action", "actions"],
}
# qa_initial is optional: the current Social template has no "QA Initial" column
# (it has "Post QA Acknowledgement", which is a human field, deliberately NOT
# matched here). If Kerri adds a QA-Initial column later, the bot will start
# filling it automatically.
_OUTPUT_OPTIONAL_ALIASES = {
    "qa_initial": ["qa initial", "qa buddy initial", "qa buddy initials"],
}


class SheetTemplateError(Exception):
    """Raised when the sheet doesn't contain the expected header columns."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def normalize_header(value: str) -> str:
    """Lowercase, collapse whitespace, treat underscores as spaces."""
    return " ".join((value or "").strip().lower().replace("_", " ").split())


def find_column_index(headers: list[str], aliases: list[str]) -> int | None:
    normalized = [normalize_header(v) for v in headers]
    for alias in aliases:
        target = normalize_header(alias)
        for idx, header in enumerate(normalized):
            if header == target:
                return idx
    return None


def column_to_a1(col_index_zero_based: int) -> str:
    """Convert a 0-based column index to an A1 column letter (0 -> A, 26 -> AA)."""
    value = col_index_zero_based + 1
    letters: list[str] = []
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _detect_header_map(
    rows: list[list[str]],
    required_aliases: dict[str, list[str]],
    optional_aliases: dict[str, list[str]],
) -> tuple[int, dict[str, int]]:
    scan_limit = min(len(rows), _HEADER_SCAN_LIMIT)
    for row_idx in range(scan_limit):
        row = rows[row_idx]
        resolved: dict[str, int] = {}
        missing: list[str] = []
        for key, aliases in required_aliases.items():
            col = find_column_index(row, aliases)
            if col is None:
                missing.append(key)
            else:
                resolved[key] = col
        if missing:
            continue
        for key, aliases in optional_aliases.items():
            col = find_column_index(row, aliases)
            if col is not None:
                resolved[key] = col
        return row_idx, resolved

    raise SheetTemplateError(
        "sheet_template_invalid",
        f"Could not find required columns {sorted(required_aliases)} "
        f"in the first {scan_limit} rows.",
    )


def parse_check_rows(table_rows: list[list[str]]) -> list[CheckRow]:
    """Parse sheet values into CheckRow objects.

    Only rows with a non-empty check_id (column A in the template) become
    CheckRows. Section headers, instructions, and blank rows are skipped.
    """
    if not table_rows:
        raise SheetTemplateError("sheet_template_invalid", "Sheet is empty.")

    header_idx, headers = _detect_header_map(
        table_rows, _INPUT_REQUIRED_ALIASES, _INPUT_OPTIONAL_ALIASES
    )

    rows: list[CheckRow] = []
    # start=header_idx + 2 makes row_index the 1-based spreadsheet row number.
    for index, item in enumerate(table_rows[header_idx + 1:], start=header_idx + 2):
        check_col = headers["check_id"]
        check_id = item[check_col].strip() if check_col < len(item) else ""
        if not check_id:
            continue

        def _cell(key: str) -> str:
            col = headers.get(key)
            if col is None or col >= len(item):
                return ""
            return item[col].strip()

        rows.append(
            CheckRow(
                row_index=index,
                check_id=check_id,
                builder_input=_cell("builder_input"),
                builder_notes=_cell("builder_notes"),
                campaign_setting=_cell("campaign_setting"),
            )
        )

    return rows


def detect_output_header_map(table_rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Find the header row and the output columns the bot writes to.

    Returns (header_row_index, {column_key: column_index}). Required keys:
    pass_or_fix, action. Optional: qa_initial.
    """
    if not table_rows:
        raise SheetTemplateError("sheet_template_invalid", "Sheet is empty.")
    return _detect_header_map(
        table_rows, _OUTPUT_REQUIRED_ALIASES, _OUTPUT_OPTIONAL_ALIASES
    )
