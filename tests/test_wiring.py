"""Wiring tests: confirm the dependency graph is assembled correctly.

These mock the heavy client constructors so we exercise the wiring LOGIC (which
client gets built, how config is threaded, Peacock routing, peacock_client_ids)
without standing up real BigQuery / Sheets / Firestore / Slack. This was the
biggest coverage blind spot — a config-threading bug here is invisible to the
per-check unit tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.gemini import StubGeminiClient
from app.adapters.storage import InMemoryRunStore
from app.api import wiring
from app.config import Settings


def _settings(**kw) -> Settings:
    base = dict(qa_run_store_backend="memory", gemini_api_key="", slack_bot_token="")
    base.update(kw)
    return Settings(**base)


# --- gemini: stub vs real -------------------------------------------------


def test_gemini_stub_without_key() -> None:
    assert isinstance(wiring.build_gemini_client(_settings(gemini_api_key="")), StubGeminiClient)


def test_gemini_real_with_key(monkeypatch) -> None:
    fake = MagicMock()
    monkeypatch.setattr(wiring, "GeminiClient", fake)
    wiring.build_gemini_client(_settings(gemini_api_key="abc123"))
    fake.assert_called_once()
    assert fake.call_args.kwargs["config"].api_key == "abc123"


# --- slack: None without token -------------------------------------------


def test_slack_none_without_token() -> None:
    assert wiring.build_slack_client(_settings(slack_bot_token="")) is None


def test_slack_built_with_token(monkeypatch) -> None:
    fake = MagicMock()
    monkeypatch.setattr(wiring, "SlackClient", fake)
    assert wiring.build_slack_client(_settings(slack_bot_token="xoxb-1")) is not None
    fake.assert_called_once()


# --- run store ------------------------------------------------------------


def test_run_store_memory_backend() -> None:
    assert isinstance(wiring.build_run_store(_settings(qa_run_store_backend="memory")), InMemoryRunStore)


# --- meta client routing (the Peacock-critical path) ----------------------


def _mock_meta_constructors(monkeypatch):
    bq, peacock, router = MagicMock(name="bq"), MagicMock(name="peacock"), MagicMock(name="router")
    monkeypatch.setattr(wiring, "BigQueryMetaClient", bq)
    monkeypatch.setattr(wiring, "PeacockMetaClient", peacock)
    monkeypatch.setattr(wiring, "RoutingMetaClient", router)
    return bq, peacock, router


def test_meta_client_registers_peacock_override_and_threads_trafficking_config(monkeypatch) -> None:
    _bq, peacock, router = _mock_meta_constructors(monkeypatch)
    s = _settings(
        qa_peacock_client_id="C22848672",
        qa_peacock_trafficking_dataset="AirTable_v2",
        qa_peacock_trafficking_table="wp_live_trafficking",
    )
    wiring.build_meta_client(s)

    # Peacock override registered under the configured client_id
    overrides = router.call_args.kwargs["overrides"]
    assert "C22848672" in overrides
    # The trafficking dataset/table are threaded into the PeacockMetaConfig
    cfg = peacock.call_args.kwargs["config"]
    assert cfg.trafficking_dataset == "AirTable_v2"
    assert cfg.trafficking_table == "wp_live_trafficking"


def test_meta_client_no_peacock_override_when_id_blank(monkeypatch) -> None:
    _bq, peacock, router = _mock_meta_constructors(monkeypatch)
    wiring.build_meta_client(_settings(qa_peacock_client_id=""))
    assert router.call_args.kwargs["overrides"] == {}
    peacock.assert_not_called()


# --- orchestration: peacock_client_ids threading -------------------------


def _mock_orchestration_deps(monkeypatch):
    for name in ("BigQueryMetaClient", "PeacockMetaClient", "RoutingMetaClient",
                 "BigQueryAccountResolver", "GoogleSheetsClient"):
        monkeypatch.setattr(wiring, name, MagicMock(name=name))
    svc = MagicMock(name="service")
    monkeypatch.setattr(wiring, "SocialQAOrchestrationService", svc)
    return svc


def test_orchestration_threads_peacock_client_ids(monkeypatch) -> None:
    svc = _mock_orchestration_deps(monkeypatch)
    wiring.build_orchestration_service(_settings(qa_peacock_client_id="C22848672"))
    assert svc.call_args.kwargs["peacock_client_ids"] == frozenset({"C22848672"})


def test_orchestration_empty_peacock_ids_when_blank(monkeypatch) -> None:
    svc = _mock_orchestration_deps(monkeypatch)
    wiring.build_orchestration_service(_settings(qa_peacock_client_id=""))
    assert svc.call_args.kwargs["peacock_client_ids"] == frozenset()
