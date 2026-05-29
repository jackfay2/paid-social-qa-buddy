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


# The three spelling checks the brief scopes to Gemini (creative copy /
# headline / description). Field paths come from the QA template's column B.
# Instructions are deliberately NARROW — spelling only, never grammar /
# punctuation / capitalization / word-choice / translation (hard rule #3) — and
# bias to Review on any uncertainty (Peacock-Olympics rule). Brandon can refine
# the wording; the scope is fixed by the brief.
#
# Capitalization, pricing/promo language, and fair-housing compliance are also
# in Gemini scope per the brief but map to template rows not yet in the
# confirmed set — add them here when those check_ids land.
_SPELLING_INSTRUCTION = (
    "You are checking ad creative text for SPELLING errors only. Does the "
    "following {part} contain a clear, unambiguous spelling mistake (a "
    "misspelled English word)? Respond Fix ONLY if there is a definite spelling "
    "error. Respond Pass if the spelling is correct. Respond Review if you are "
    "not fully confident, or if the text relies on brand names, intentional "
    "stylization, abbreviations, or non-English words you cannot verify. Do NOT "
    "flag grammar, punctuation, capitalization, or word choice — spelling only."
)

TEXT_CHECK_DEFINITIONS: dict[str, TextCheckDefinition] = {
    "ad_copy_spelling": TextCheckDefinition(
        check_id="ad_copy_spelling",
        instruction=_SPELLING_INSTRUCTION.format(part="ad copy (body) text"),
        ad_field="creative.object_story_spec.link_data.message",
    ),
    "ad_headline_spelling": TextCheckDefinition(
        check_id="ad_headline_spelling",
        instruction=_SPELLING_INSTRUCTION.format(part="ad headline"),
        ad_field="creative.object_story_spec.link_data.name",
    ),
    "ad_description_spelling": TextCheckDefinition(
        check_id="ad_description_spelling",
        instruction=_SPELLING_INSTRUCTION.format(part="ad description"),
        ad_field="creative.object_story_spec.link_data.description",
    ),
}


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
