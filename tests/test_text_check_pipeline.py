"""Unit tests for the Gemini text-check pipeline.

Mocks Gemini at the GeminiClient.run_text_checks boundary. The registry of
text-check definitions starts empty in prod — these tests inject definitions
via monkeypatch so they don't depend on Brandon's not-yet-finalized list.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.checks import text_checks as text_checks_module
from app.core.pipeline import execute_checks, execute_text_checks
from app.models import CheckRow


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def two_text_checks(monkeypatch) -> None:
    """Inject two test text-check definitions for the duration of the test."""
    defs = {
        "creative_spelling": text_checks_module.TextCheckDefinition(
            check_id="creative_spelling",
            instruction="Does this ad text contain a spelling error?",
            ad_field="creative.body",
        ),
        "creative_promo_language": text_checks_module.TextCheckDefinition(
            check_id="creative_promo_language",
            instruction="Does this ad text use disallowed promotional language?",
            ad_field="creative.title",
        ),
    }
    monkeypatch.setattr(text_checks_module, "TEXT_CHECK_DEFINITIONS", defs)


class _FakeGemini:
    """Records the batch it was given; returns the prescripted response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.last_batch: list[dict[str, Any]] = []
        self.call_count = 0

    def run_text_checks(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        self.last_batch = list(batch)
        self.call_count += 1
        return self.response


def _row(check_id: str, row_index: int = 10, builder_input: str = "") -> CheckRow:
    return CheckRow(row_index=row_index, check_id=check_id, builder_input=builder_input)


def _ad(ad_id: str, *, name: str = "", body: str = "", title: str = "") -> dict:
    return {
        "id": ad_id,
        "name": name,
        "creative": {"body": body, "title": title},
    }


# --- extract_ad_text / helpers --------------------------------------------


def test_extract_ad_text_flat_field() -> None:
    ad = {"body": "Hello"}
    assert text_checks_module.extract_ad_text(ad, "body") == "Hello"


def test_extract_ad_text_nested_field() -> None:
    ad = {"creative": {"body": "Hello"}}
    assert text_checks_module.extract_ad_text(ad, "creative.body") == "Hello"


def test_extract_ad_text_missing_field_returns_empty() -> None:
    assert text_checks_module.extract_ad_text({}, "creative.body") == ""
    assert text_checks_module.extract_ad_text({"creative": None}, "creative.body") == ""
    assert text_checks_module.extract_ad_text({"creative": "x"}, "creative.body") == ""


def test_extract_ad_text_strips_whitespace() -> None:
    assert text_checks_module.extract_ad_text({"body": "  Hello  "}, "body") == "Hello"


def test_extract_ad_text_non_dict_input() -> None:
    assert text_checks_module.extract_ad_text(None, "body") == ""
    assert text_checks_module.extract_ad_text("nope", "body") == ""


def test_ad_label_prefers_name() -> None:
    assert text_checks_module.ad_label({"id": 1, "name": "Hero Ad"}) == "Hero Ad"
    assert text_checks_module.ad_label({"id": 1}) == "ad 1"
    assert text_checks_module.ad_label({}) == "an ad"
    assert text_checks_module.ad_label(None) == "an ad"


# --- is_text_check / TEXT_CHECK_DEFINITIONS starts empty --------------------


def test_text_check_definitions_starts_empty() -> None:
    """Until Brandon hands over the spec, the registry must be empty so we
    don't accidentally route deterministic check_ids to Gemini."""
    assert text_checks_module.TEXT_CHECK_DEFINITIONS == {}


def test_is_text_check_false_for_unknown_ids() -> None:
    assert text_checks_module.is_text_check("not_a_real_check") is False
    assert text_checks_module.is_text_check("") is False


def test_is_text_check_true_for_injected(two_text_checks) -> None:
    assert text_checks_module.is_text_check("creative_spelling") is True
    assert text_checks_module.is_text_check("creative_promo_language") is True


# --- execute_checks skips text-check rows ----------------------------------


def test_execute_checks_skips_text_rows(two_text_checks) -> None:
    """Text-check rows must not fall through to the deterministic registry."""

    def boom_runner(row, *, evidence=None):
        raise AssertionError(f"Runner should not be called for text check {row.check_id}")

    rows = [_row("creative_spelling", row_index=5)]
    results = execute_checks(rows, boom_runner, evidence={})
    assert results == []


# --- execute_text_checks: empty / disabled paths ----------------------------


def test_no_gemini_client_returns_empty(two_text_checks) -> None:
    rows = [_row("creative_spelling")]
    assert execute_text_checks(rows, [_ad("a1", body="hi")], None) == []


def test_no_text_rows_returns_empty(two_text_checks) -> None:
    gem = _FakeGemini({"check_results": {}})
    rows = [_row("campaign_objective")]  # deterministic, not a text check
    assert execute_text_checks(rows, [_ad("a1", body="hi")], gem) == []
    assert gem.call_count == 0  # no call when nothing to ask


def test_no_ads_with_text_returns_review(two_text_checks) -> None:
    gem = _FakeGemini({"check_results": {}})
    rows = [_row("creative_spelling")]
    results = execute_text_checks(rows, [_ad("a1")], gem)  # body is empty
    assert len(results) == 1
    assert results[0].verdict == "Review"
    assert "no ad text available" in results[0].action.lower()
    # No batch items were sent — nothing to ask Gemini.
    assert gem.last_batch == []


def test_empty_ads_list_returns_review(two_text_checks) -> None:
    gem = _FakeGemini({"check_results": {}})
    rows = [_row("creative_spelling")]
    results = execute_text_checks(rows, [], gem)
    assert len(results) == 1
    assert results[0].verdict == "Review"


# --- execute_text_checks: aggregation rules --------------------------------


def test_all_ads_pass_row_passes(two_text_checks) -> None:
    gem = _FakeGemini(
        {
            "check_results": {
                "r10_aa1": {"verdict": "Pass", "action": "", "confidence": 0.95},
                "r10_aa2": {"verdict": "Pass", "action": "", "confidence": 0.92},
            }
        }
    )
    rows = [_row("creative_spelling")]
    ads = [_ad("a1", body="Good copy"), _ad("a2", body="Also good")]
    results = execute_text_checks(rows, ads, gem)

    assert len(results) == 1
    assert results[0].verdict == "Pass"
    # Single batched call regardless of ad count.
    assert gem.call_count == 1
    assert len(gem.last_batch) == 2


def test_one_ad_fix_makes_row_fix_with_ad_label(two_text_checks) -> None:
    gem = _FakeGemini(
        {
            "check_results": {
                "r10_aa1": {"verdict": "Pass", "action": "", "confidence": 0.95},
                "r10_aa2": {
                    "verdict": "Fix",
                    "action": "'Recieve' should be 'Receive'.",
                    "confidence": 0.93,
                },
            }
        }
    )
    rows = [_row("creative_spelling")]
    ads = [
        _ad("a1", name="Hero", body="All good here."),
        _ad("a2", name="Variant B", body="Recieve 20% off."),
    ]
    results = execute_text_checks(rows, ads, gem)

    assert results[0].verdict == "Fix"
    assert "Variant B" in results[0].action
    assert "Recieve" in results[0].action


def test_review_when_any_ad_review_and_none_fix(two_text_checks) -> None:
    gem = _FakeGemini(
        {
            "check_results": {
                "r10_aa1": {"verdict": "Pass", "action": "", "confidence": 0.95},
                "r10_aa2": {
                    "verdict": "Review",
                    "action": "Wasn't sure about the punctuation.",
                    "confidence": 0.4,
                },
            }
        }
    )
    rows = [_row("creative_spelling")]
    ads = [_ad("a1", name="A", body="Solid"), _ad("a2", name="B", body="Maybe?")]
    results = execute_text_checks(rows, ads, gem)

    assert results[0].verdict == "Review"
    assert "B" in results[0].action


def test_missing_per_ad_result_is_review_not_pass(two_text_checks) -> None:
    """Gemini failing to return a verdict for an ad must NOT silently Pass."""
    gem = _FakeGemini({"check_results": {}})  # totally empty response
    rows = [_row("creative_spelling")]
    ads = [_ad("a1", body="some text")]
    results = execute_text_checks(rows, ads, gem)
    assert results[0].verdict == "Review"


def test_fix_short_circuits_remaining_ads(two_text_checks) -> None:
    """Once any ad Fixes, subsequent ad verdicts don't matter for this row.

    Important because Gemini might still return Pass for the ads we look at
    after the Fix — the row verdict should remain Fix.
    """
    gem = _FakeGemini(
        {
            "check_results": {
                "r10_aa1": {"verdict": "Fix", "action": "Typo", "confidence": 0.95},
                "r10_aa2": {"verdict": "Pass", "action": "", "confidence": 0.95},
                "r10_aa3": {"verdict": "Pass", "action": "", "confidence": 0.95},
            }
        }
    )
    rows = [_row("creative_spelling")]
    ads = [
        _ad("a1", body="Typo"),
        _ad("a2", body="OK"),
        _ad("a3", body="OK"),
    ]
    results = execute_text_checks(rows, ads, gem)
    assert results[0].verdict == "Fix"


# --- batching: one Gemini call per job, multiple text checks ---------------


def test_multiple_text_checks_share_single_batch(two_text_checks) -> None:
    """Two text-check rows × two ads = 4 batch items, ONE Gemini call."""
    gem = _FakeGemini(
        {
            "check_results": {
                "r10_aa1": {"verdict": "Pass", "action": "", "confidence": 0.9},
                "r10_aa2": {"verdict": "Pass", "action": "", "confidence": 0.9},
                "r11_aa1": {"verdict": "Pass", "action": "", "confidence": 0.9},
                "r11_aa2": {"verdict": "Pass", "action": "", "confidence": 0.9},
            }
        }
    )
    rows = [
        _row("creative_spelling", row_index=10),
        _row("creative_promo_language", row_index=11),
    ]
    ads = [
        _ad("a1", body="A body", title="A title"),
        _ad("a2", body="B body", title="B title"),
    ]
    results = execute_text_checks(rows, ads, gem)

    assert len(results) == 2
    assert all(r.verdict == "Pass" for r in results)
    assert gem.call_count == 1
    assert len(gem.last_batch) == 4


def test_batch_items_use_per_check_ad_field(two_text_checks) -> None:
    """spelling reads ad.creative.body; promo reads ad.creative.title."""
    gem = _FakeGemini({"check_results": {}})
    rows = [
        _row("creative_spelling", row_index=10),
        _row("creative_promo_language", row_index=11),
    ]
    ads = [_ad("a1", body="from-body", title="from-title")]
    execute_text_checks(rows, ads, gem)

    # Each batch item carries the instruction and the text from the
    # correct ad field.
    texts_by_key = {item["check_id"]: item["text"] for item in gem.last_batch}
    assert texts_by_key["r10_aa1"] == "from-body"
    assert texts_by_key["r11_aa1"] == "from-title"


def test_builder_input_and_notes_propagate_to_result(two_text_checks) -> None:
    gem = _FakeGemini(
        {
            "check_results": {
                "r10_aa1": {"verdict": "Pass", "action": "", "confidence": 0.95}
            }
        }
    )
    row = CheckRow(
        row_index=10,
        check_id="creative_spelling",
        builder_input="no typos",
        builder_notes="reviewer A",
    )
    results = execute_text_checks([row], [_ad("a1", body="good")], gem)
    assert results[0].builder_input == "no typos"
    assert results[0].builder_notes == "reviewer A"
