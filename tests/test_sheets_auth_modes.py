"""Auth-mode dispatch in GoogleSheetsClient._client.

Exercises the three modes without hitting Google. Mocks the `gspread` module
factory functions (`service_account_from_dict`, `service_account`, `authorize`)
and `google.auth.default` via sys.modules so the import inside `_client`
returns our fakes.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from app.adapters.sheets.client import GoogleSheetsClient, GoogleSheetsConfig


@pytest.fixture
def fake_gspread(monkeypatch):
    """Replace `gspread` in sys.modules with a MagicMock for the duration."""
    fake = MagicMock()
    fake.service_account_from_dict = MagicMock(name="sa_from_dict")
    fake.service_account = MagicMock(name="sa_from_file")
    fake.authorize = MagicMock(name="authorize")
    monkeypatch.setitem(sys.modules, "gspread", fake)
    return fake


@pytest.fixture
def fake_google_auth(monkeypatch):
    """Patch google.auth.default in place. The Request import in _adc_client
    works against the real module; we only need to intercept the credential
    fetch so no network/file I/O happens.
    """
    import google.auth

    fake_default = MagicMock(return_value=(MagicMock(spec=["expired"]), None))
    # Force `expired` to be falsy so the refresh path is skipped.
    fake_default.return_value[0].expired = False
    monkeypatch.setattr(google.auth, "default", fake_default)
    return google.auth


# --- service_account mode --------------------------------------------------


def test_service_account_mode_uses_json(fake_gspread) -> None:
    client = GoogleSheetsClient(
        config=GoogleSheetsConfig(
            auth_mode="service_account",
            service_account_json='{"type": "service_account"}',
        )
    )
    client._client()
    fake_gspread.service_account_from_dict.assert_called_once_with(
        {"type": "service_account"}
    )
    fake_gspread.service_account.assert_not_called()


def test_service_account_mode_uses_file_when_json_blank(fake_gspread) -> None:
    client = GoogleSheetsClient(
        config=GoogleSheetsConfig(
            auth_mode="service_account",
            service_account_file="/tmp/sa.json",
        )
    )
    client._client()
    fake_gspread.service_account.assert_called_once_with(filename="/tmp/sa.json")
    fake_gspread.service_account_from_dict.assert_not_called()


def test_service_account_mode_without_creds_hard_fails(fake_gspread) -> None:
    client = GoogleSheetsClient(config=GoogleSheetsConfig(auth_mode="service_account"))
    with pytest.raises(RuntimeError, match="service_account_file or service_account_json"):
        client._client()


# --- adc mode --------------------------------------------------------------


def test_adc_mode_calls_google_auth_default(fake_gspread, fake_google_auth) -> None:
    client = GoogleSheetsClient(config=GoogleSheetsConfig(auth_mode="adc"))
    client._client()
    fake_google_auth.default.assert_called_once()
    fake_gspread.authorize.assert_called_once()
    # ADC mode must NOT touch the service-account factories.
    fake_gspread.service_account_from_dict.assert_not_called()
    fake_gspread.service_account.assert_not_called()


# --- auto mode -------------------------------------------------------------


def test_auto_mode_prefers_service_account_when_configured(fake_gspread, fake_google_auth) -> None:
    client = GoogleSheetsClient(
        config=GoogleSheetsConfig(
            auth_mode="auto",
            service_account_json='{"type": "service_account"}',
        )
    )
    client._client()
    fake_gspread.service_account_from_dict.assert_called_once()
    # SA succeeded — ADC must NOT have been invoked.
    fake_google_auth.default.assert_not_called()


def test_auto_mode_falls_back_to_adc_when_sa_not_configured(fake_gspread, fake_google_auth) -> None:
    """No JSON, no file — auto silently falls through to ADC instead of failing."""
    client = GoogleSheetsClient(config=GoogleSheetsConfig(auth_mode="auto"))
    client._client()
    fake_gspread.service_account_from_dict.assert_not_called()
    fake_gspread.service_account.assert_not_called()
    fake_google_auth.default.assert_called_once()


def test_auto_mode_falls_back_to_adc_when_sa_factory_raises(fake_gspread, fake_google_auth) -> None:
    """Bad SA JSON shouldn't kill the worker — fall back to ADC."""
    fake_gspread.service_account_from_dict.side_effect = ValueError("bad json")
    client = GoogleSheetsClient(
        config=GoogleSheetsConfig(
            auth_mode="auto",
            service_account_json='{"type": "service_account"}',
        )
    )
    client._client()
    fake_google_auth.default.assert_called_once()


# --- invalid mode ----------------------------------------------------------


def test_invalid_auth_mode_raises_clear_error() -> None:
    client = GoogleSheetsClient(config=GoogleSheetsConfig(auth_mode="totally-made-up"))
    with pytest.raises(RuntimeError, match="Invalid qa_sheets_auth_mode"):
        client._client()
