"""Unit tests for the ad-set targeting helper."""

from __future__ import annotations

from app.checks._targeting import read_targeting


def test_nested_targeting_returns_fields() -> None:
    adset = {
        "id": 1,
        "targeting": {
            "age_min": 18,
            "age_max": 65,
            "genders": [1, 2],
            "countries": ["US"],
        },
    }
    t = read_targeting(adset)
    assert t["age_min"] == 18
    assert t["age_max"] == 65
    assert t["genders"] == [1, 2]
    assert t["countries"] == ["US"]


def test_nested_strips_none_values() -> None:
    adset = {"targeting": {"age_min": 18, "age_max": None, "genders": None}}
    t = read_targeting(adset)
    assert t == {"age_min": 18}


def test_flat_targeting_fields_picked_up() -> None:
    """When targeting isn't nested, harvest known fields from the adset row."""
    adset = {
        "id": 1,
        "name": "Adset A",
        "age_min": 25,
        "age_max": 45,
        "countries": ["US", "CA"],
        "effective_status": "ACTIVE",  # not a targeting field, should be ignored
    }
    t = read_targeting(adset)
    assert t["age_min"] == 25
    assert t["age_max"] == 45
    assert t["countries"] == ["US", "CA"]
    assert "effective_status" not in t
    assert "name" not in t


def test_flat_skips_none_values() -> None:
    adset = {"age_min": 18, "age_max": None}
    assert read_targeting(adset) == {"age_min": 18}


def test_missing_targeting_returns_empty() -> None:
    adset = {"id": 1, "name": "Adset"}
    assert read_targeting(adset) == {}


def test_nested_empty_dict_returns_empty() -> None:
    adset = {"targeting": {}}
    assert read_targeting(adset) == {}


def test_non_dict_input_returns_empty() -> None:
    assert read_targeting(None) == {}
    assert read_targeting("not a dict") == {}
    assert read_targeting([1, 2, 3]) == {}


def test_non_dict_targeting_falls_through_to_flat() -> None:
    """If `targeting` is present but not a dict, don't crash — fall through to flat extraction."""
    adset = {"targeting": "not a dict", "age_min": 18}
    assert read_targeting(adset) == {"age_min": 18}
