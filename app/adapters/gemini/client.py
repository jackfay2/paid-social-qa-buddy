"""Gemini adapter for batched text-quality checks.

Gemini's role is deliberately narrow (hard rule): yes/no-style judgments on ad
text — spelling, capitalization, promo-language, fair-housing phrasing. NOT
translation, nuanced typo detection, brand-voice, or anything generative. Maya
found Gemini unreliable outside this lane on the Search side.

One call per job (cost + latency): all text-check items are batched into a
single prompt. The model returns structured JSON keyed by check_id. Anything
uncertain — low confidence, timeout, non-200, malformed response — becomes
Review, never an auto-Pass (the Peacock-Olympics rule).

Uses the Gemini REST API via httpx, so no extra SDK dependency and it's fully
mockable. Conforms to the GeminiClient Protocol in app.core.contracts.

Input batch item shape (built by orchestration when text checks are wired):
    {"check_id": str, "text": str, "instruction": str}
Return shape (merged back by check_id):
    {"check_results": {check_id: {"verdict": str, "action": str, "confidence": float}}}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

_logger = logging.getLogger("paid_social_qa_buddy.gemini")

_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-2.5-flash"
# A batched call on gemini-2.5-flash can take 30-40s; 60s leaves headroom so a
# slow batch never times out into an all-Review fail-safe. (Worker stop is 720s.)
_DEFAULT_TIMEOUT_SECONDS = 60
_DEFAULT_CONFIDENCE_THRESHOLD = 0.8

_ALLOWED_VERDICTS = {"Pass", "Fix", "Review"}


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str = _DEFAULT_MODEL
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD


def _review_all(batch: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    """Build a result mapping every check in the batch to Review.

    Used for every failure path: no key, transport error, bad status, malformed
    response. Never auto-Pass on uncertainty.
    """
    return {
        "check_results": {
            item["check_id"]: {
                "verdict": "Review",
                "action": reason,
                "confidence": 0.0,
            }
            for item in batch
            if item.get("check_id")
        }
    }


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class GeminiClient:
    """Concrete GeminiClient backed by the Gemini REST API."""

    def __init__(
        self,
        config: GeminiConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)

    def run_text_checks(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        if not batch:
            return {"check_results": {}}
        if not self.config.api_key:
            return _review_all(batch, "Gemini not configured; verify text manually.")

        prompt = self._build_prompt(batch)
        # Force raw JSON output so we don't have to strip markdown fences.
        generation_config: dict[str, Any] = {"responseMimeType": "application/json"}
        # gemini-2.5-* models run "thinking" by default, which roughly triples
        # latency (a 46-item batch took ~37s vs ~6s) and burns extra tokens for
        # no quality gain on these narrow yes/no spelling judgments. Disable it.
        # Only 2.5 models accept thinkingConfig, so gate on the model name to
        # avoid a 400 on other models.
        if "2.5" in self.config.model:
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}
        try:
            response = self._http.post(
                _API_URL.format(model=self.config.model),
                params={"key": self.config.api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": generation_config,
                },
            )
        except httpx.RequestError as exc:
            _logger.warning("gemini_request_error", extra={"error": str(exc)})
            return _review_all(batch, "Gemini request failed; verify text manually.")

        if response.status_code != 200:
            _logger.warning("gemini_http_error", extra={"status": response.status_code})
            return _review_all(
                batch, f"Gemini returned HTTP {response.status_code}; verify text manually."
            )

        try:
            model_output = self._extract_json(response.json())
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            _logger.warning("gemini_parse_error", extra={"error": str(exc)})
            return _review_all(batch, "Gemini response malformed; verify text manually.")

        # Per-check: enforce verdict whitelist + confidence threshold; default Review.
        results: dict[str, dict[str, Any]] = {}
        for item in batch:
            check_id = item.get("check_id")
            if not check_id:
                continue
            gem = model_output.get(check_id)
            if not isinstance(gem, dict):
                results[check_id] = {
                    "verdict": "Review",
                    "action": "No Gemini result for this check; verify manually.",
                    "confidence": 0.0,
                }
                continue

            verdict = str(gem.get("verdict", "Review")).title()
            confidence = _as_float(gem.get("confidence"))
            action = str(gem.get("reason", "")).strip()

            if verdict not in _ALLOWED_VERDICTS or confidence < self.config.confidence_threshold:
                results[check_id] = {
                    "verdict": "Review",
                    "action": action or "Low confidence; verify manually.",
                    "confidence": confidence,
                }
            else:
                results[check_id] = {
                    "verdict": verdict,
                    "action": action,
                    "confidence": confidence,
                }

        return {"check_results": results}

    def _build_prompt(self, batch: list[dict[str, Any]]) -> str:
        lines = [
            "You are a strict ad-copy QA assistant. For each item, judge ONLY the",
            "stated question about the given text. Answer Pass, Fix, or Review.",
            "Use Review whenever you are not confident. Do not translate, rewrite,",
            "or make brand-voice judgments — only answer the specific question.",
            "",
            'Respond ONLY with JSON of the form:',
            '{"<check_id>": {"verdict": "Pass|Fix|Review", "confidence": 0.0-1.0, "reason": "short"}}',
            "",
            "Items:",
        ]
        for item in batch:
            lines.append(
                json.dumps(
                    {
                        "check_id": item.get("check_id"),
                        "question": item.get("instruction", ""),
                        "text": item.get("text", ""),
                    }
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _extract_json(payload: dict[str, Any]) -> dict[str, Any]:
        """Pull the model's text out of the Gemini response and parse it as JSON."""
        text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Defensive: strip a ```json ... ``` fence if the model adds one anyway.
        if text.startswith("```"):
            inner = text.split("```")[1]
            if inner.lstrip().lower().startswith("json"):
                inner = inner.lstrip()[4:]
            text = inner.strip()
        result = json.loads(text)
        # gemini-2.5-flash often returns a JSON ARRAY of single-key objects
        # ([{"<check_id>": {...}}, ...]) instead of one flat object — merge it.
        if isinstance(result, list):
            merged: dict[str, Any] = {}
            for item in result:
                if isinstance(item, dict):
                    merged.update(item)
            result = merged
        if not isinstance(result, dict):
            raise ValueError("Gemini JSON was not an object or list of objects")
        return result


class StubGeminiClient:
    """No-op Gemini for local/test runs without an API key: everything Review."""

    def run_text_checks(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        return _review_all(batch, "Gemini stub; text checks not run.")
