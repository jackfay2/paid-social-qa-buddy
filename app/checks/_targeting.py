"""Defensive reader for ad-set targeting fields.

Per-client BigQuery schemas vary on how targeting is stored:
- Some clients have it as a nested RECORD on `facebook_ads__adsets`
  (`adset["targeting"]` is a dict of age/gender/location/audiences/etc.).
- Some have a subset as flat columns directly on the adset row.
- Others have it in a separate `facebook_ads__adset_targetings` table — in that
  case orchestration must fetch and merge it into the adset row as
  `adset["targeting"]` before calling check functions. This helper does NOT
  issue queries; it just reads what's present.

`read_targeting` returns the present targeting fields as a flat dict, regardless
of source shape. Callers use `.get()` with their own defaults. Absent fields
stay absent so checks can return Review-on-missing cleanly.
"""

from __future__ import annotations

from typing import Any

# Targeting field names that appear (nested or flat) across client schemas.
# Used to harvest flat fields when the nested RECORD isn't present.
_TARGETING_FIELDS = (
    "age_min",
    "age_max",
    "genders",
    "countries",
    "location_types",
    "excluded_custom_audiences",
    "custom_audiences",
    "optimization",
    "publisher_platforms",
    "brand_safety_content_filter_levels",
)


def read_targeting(adset: Any) -> dict[str, Any]:
    """Return an ad set's targeting fields as a flat dict.

    Reads the nested `adset["targeting"]` RECORD when present (the common shape);
    otherwise falls back to picking targeting-shaped flat columns off the adset
    row. Returns {} when nothing usable is found, so callers can branch to
    Review cleanly.
    """
    if not isinstance(adset, dict):
        return {}

    nested = adset.get("targeting")
    if isinstance(nested, dict):
        return {k: v for k, v in nested.items() if v is not None}

    # Fallback: harvest known targeting fields off the adset row directly.
    return {
        key: adset[key]
        for key in _TARGETING_FIELDS
        if key in adset and adset[key] is not None
    }
