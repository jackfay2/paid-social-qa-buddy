"""Domain models. Mirrors the Search repo's shapes for compatibility."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CheckRow:
    """One row of the QA sheet that has a check_id in column A."""
    row_index: int
    check_id: str
    builder_input: str = ""
    builder_notes: str = ""
    campaign_setting: str = ""


@dataclass
class CheckResult:
    """Per-row verdict written back to the QA sheet."""
    row_index: int
    check_id: str
    verdict: str  # Pass | Fix | Review | N/A | Error
    action: str = ""
    builder_input: str = ""
    builder_notes: str = ""


@dataclass
class RunSummary:
    pass_count: int = 0
    fix_count: int = 0
    review_count: int = 0
    na_count: int = 0
    error_count: int = 0
