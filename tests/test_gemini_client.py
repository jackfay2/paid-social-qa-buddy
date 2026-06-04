"""Unit tests for GeminiClient with a mocked httpx client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx

from app.adapters.gemini.client import (
    GeminiClient,
    GeminiConfig,
    StubGeminiClient,
)


def _gemini_response(verdicts: dict) -> MagicMock:
    """Build a mock Gemini API response whose model text is the given JSON."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(verdicts)}]}}
        ]
    }
    return response


def _client(response: MagicMock | None = None, **config_overrides) -> GeminiClient:
    http = MagicMock(spec=httpx.Client)
    if response is not None:
        http.post.return_value = response
    config = GeminiConfig(api_key="test-key", **config_overrides)
    return GeminiClient(config=config, http_client=http)


_BATCH = [
    {"check_id": "ad_copy_spelling", "text": "Shop now for amazing deals", "instruction": "spelling?"},
    {"check_id": "headline_spelling", "text": "Big Sale", "instruction": "spelling?"},
]


# --- empty / unconfigured --------------------------------------------------


def test_empty_batch_returns_empty() -> None:
    result = _client(_gemini_response({})).run_text_checks([])
    assert result == {"check_results": {}}


def test_no_api_key_reviews_all() -> None:
    http = MagicMock(spec=httpx.Client)
    client = GeminiClient(config=GeminiConfig(api_key=""), http_client=http)
    result = client.run_text_checks(_BATCH)
    assert all(r["verdict"] == "Review" for r in result["check_results"].values())
    http.post.assert_not_called()


# --- success path ----------------------------------------------------------


def test_parses_verdicts_above_threshold() -> None:
    response = _gemini_response(
        {
            "ad_copy_spelling": {"verdict": "Pass", "confidence": 0.95, "reason": "clean"},
            "headline_spelling": {"verdict": "Fix", "confidence": 0.9, "reason": "typo"},
        }
    )
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert result["ad_copy_spelling"]["verdict"] == "Pass"
    assert result["headline_spelling"]["verdict"] == "Fix"
    assert result["headline_spelling"]["action"] == "typo"


def test_generation_config_pins_temperature_zero() -> None:
    """Spelling must be reproducible: pin temperature=0 (greedy decoding) so the
    same copy yields the same verdict run-to-run. At the default (~1.0) the same
    ad flipped Pass<->Review across runs (measured + fixed 2026-06-04)."""
    client = _client(_gemini_response({"ad_copy_spelling": {"verdict": "Pass", "confidence": 0.95, "reason": "clean"}}))
    client.run_text_checks(_BATCH)
    config = client._http.post.call_args.kwargs["json"]["generationConfig"]
    assert config["temperature"] == 0
    assert config["topP"] == 1


def test_low_confidence_becomes_review() -> None:
    response = _gemini_response(
        {
            "ad_copy_spelling": {"verdict": "Pass", "confidence": 0.5, "reason": "maybe"},
            "headline_spelling": {"verdict": "Fix", "confidence": 0.95, "reason": "typo"},
        }
    )
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    # 0.5 < 0.8 threshold -> Review
    assert result["ad_copy_spelling"]["verdict"] == "Review"
    assert result["headline_spelling"]["verdict"] == "Fix"


def test_invalid_verdict_becomes_review() -> None:
    response = _gemini_response(
        {"ad_copy_spelling": {"verdict": "Maybe", "confidence": 0.99, "reason": "x"},
         "headline_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""}}
    )
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert result["ad_copy_spelling"]["verdict"] == "Review"


def test_missing_check_in_response_becomes_review() -> None:
    # Model only returned one of the two checks.
    response = _gemini_response(
        {"ad_copy_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""}}
    )
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert result["headline_spelling"]["verdict"] == "Review"


# --- failure paths all become Review --------------------------------------


def test_non_200_reviews_all() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 429
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert all(r["verdict"] == "Review" for r in result.values())


def test_request_error_reviews_all() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.side_effect = httpx.ReadTimeout("timeout")
    client = GeminiClient(config=GeminiConfig(api_key="k"), http_client=http)
    result = client.run_text_checks(_BATCH)["check_results"]
    assert all(r["verdict"] == "Review" for r in result.values())


def test_malformed_json_reviews_all() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]
    }
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert all(r["verdict"] == "Review" for r in result.values())


def test_unexpected_response_shape_reviews_all() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {"unexpected": "shape"}
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert all(r["verdict"] == "Review" for r in result.values())


def test_handles_markdown_fenced_json() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    fenced = "```json\n" + json.dumps(
        {"ad_copy_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""},
         "headline_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""}}
    ) + "\n```"
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": fenced}]}}]
    }
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert result["ad_copy_spelling"]["verdict"] == "Pass"


def test_handles_json_array_of_single_key_objects() -> None:
    """gemini-2.5-flash often returns [{"id": {...}}, {"id2": {...}}] instead of
    a flat object — must merge, not fail to Review (the live-data bug)."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    arr = json.dumps([
        {"ad_copy_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": "ok"}},
        {"headline_spelling": {"verdict": "Fix", "confidence": 0.97, "reason": "typo"}},
    ])
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": arr}]}}]}
    result = _client(response).run_text_checks(_BATCH)["check_results"]
    assert result["ad_copy_spelling"]["verdict"] == "Pass"
    assert result["headline_spelling"]["verdict"] == "Fix"


# --- stub ------------------------------------------------------------------


def test_disables_thinking_for_2_5_models() -> None:
    """gemini-2.5-* runs 'thinking' by default (~3x latency). The request must
    set thinkingConfig.thinkingBudget=0 so a batched call doesn't time out into
    an all-Review fail-safe."""
    response = _gemini_response(
        {"ad_copy_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""},
         "headline_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""}}
    )
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = response
    GeminiClient(
        config=GeminiConfig(api_key="k", model="gemini-2.5-flash"),
        http_client=http,
    ).run_text_checks(_BATCH)
    sent = http.post.call_args.kwargs["json"]["generationConfig"]
    assert sent["thinkingConfig"] == {"thinkingBudget": 0}
    assert sent["responseMimeType"] == "application/json"


def test_no_thinking_config_for_non_2_5_models() -> None:
    """thinkingConfig is a 2.5-only field — must NOT be sent to other models
    (would 400)."""
    response = _gemini_response(
        {"ad_copy_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""},
         "headline_spelling": {"verdict": "Pass", "confidence": 0.99, "reason": ""}}
    )
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = response
    GeminiClient(
        config=GeminiConfig(api_key="k", model="gemini-2.0-flash"),
        http_client=http,
    ).run_text_checks(_BATCH)
    sent = http.post.call_args.kwargs["json"]["generationConfig"]
    assert "thinkingConfig" not in sent


def test_stub_reviews_all() -> None:
    result = StubGeminiClient().run_text_checks(_BATCH)["check_results"]
    assert all(r["verdict"] == "Review" for r in result.values())
    assert len(result) == 2
