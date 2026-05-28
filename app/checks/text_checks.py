"""Text-quality check definitions for Gemini batched evaluation.

Text checks are deliberately narrow (Peacock-Olympics rule): yes/no judgments
on ad copy — spelling, capitalization, promo-language, fair-housing phrasing.
NOT translation, nuanced typos beyond spellcheck, brand voice, or anything
generative. Gemini's only role here is "answer this specific question about
this specific text, Pass/Fix/Review."

Each definition declares:
  - check_id     : the QA-sheet column-A identifier
  - instruction  : the strict yes/no question Gemini answers per ad
  - ad_field     : where in the ad evidence record the text lives. Dot-paths
                   supported (e.g. ``creative.body``) so nested BQ shapes work.

Per-ad evaluation; aggregation rule (matches ad-set semantics): if any ad
returns Fix, the row is Fix and points at that ad. If any returns Review and
none Fix, the row is Review. Otherwise Pass. If NO ad has populated text for
the field, the row is Review with "no ad text available."

Definitions start EMPTY by design — Brandon owns the specific instructions
and ad_field paths. Adding a new text check is just an entry here; the
pipeline wiring picks it up automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextCheckDefinition:
    """Configuration for a single text-quality check evaluated by Gemini."""

    check_id: str
    instruction: str
    ad_field: str  # Dot-path into the ad record, e.g. "body" or "creative.body".


# Empty until Brandon hands over the canonical text-check set. Example shape
# (kept commented so the registry stays empty for shape-stability tests):
#
# "creative_spelling": TextCheckDefinition(
#     check_id="creative_spelling",
#     instruction=(
#         "Does this ad text contain any spelling errors? Answer Pass if it is "
#         "perfectly spelled, Fix if there is a clear spelling error, Review if "
#         "you are not confident."
#     ),
#     ad_field="creative.body",
# ),
TEXT_CHECK_DEFINITIONS: dict[str, TextCheckDefinition] = {}


def is_text_check(check_id: str) -> bool:
    """True when the given check_id is evaluated via Gemini, not the deterministic registry."""
    return check_id in TEXT_CHECK_DEFINITIONS


def extract_ad_text(ad: Any, field: str) -> str:
    """Read a (possibly nested) text field off an ad record.

    Returns "" when the field is missing, blank, or the traversal hits a
    non-dict midway. Callers treat "" as "no text available for this ad."
    """
    if not isinstance(ad, dict) or not field:
        return ""
    current: Any = ad
    for part in field.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
        if current is None:
            return ""
    text = str(current).strip()
    return text


def ad_label(ad: Any) -> str:
    """Best-effort human label for an ad. Mirrors the ad-set check helper."""
    if not isinstance(ad, dict):
        return "an ad"
    name = ad.get("name") or ad.get("ad_name")
    if name:
        return str(name)
    ad_id = ad.get("id") or ad.get("ad_id")
    return f"ad {ad_id}" if ad_id else "an ad"
