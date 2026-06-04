# Peacock template coverage — Lever 1 (data-driven) vs Lever 2 (Meta API)

**Status:** 2026-06-04. Definitive per-check coverage map after a full inventory
of the Peacock project `nbc-287716` (~80 datasets). Grounds the work to reduce
Review verdicts by wiring every check the data can support.

## Data reality
Peacock's *entire* BigQuery footprint is **reporting + trafficking/creative**
metadata. The Meta ad-set *configuration/settings* (budget, bid, demographic &
geo targeting, optimization goal, attribution window, conversion event, spend
caps) are **not synced anywhere** — verified against the curated table, the raw
Meta staging tables (`prod_peacock_social_data.src_meta`/`stg_meta`), and the
dataset inventory. Those fields live only in the Meta Marketing API.

Two sources we read:
- **perf** — `prod_peacock_final_data.creative_and_audience_data` (per creative).
- **trafficking** — `AirTable_v2.wp_live_trafficking` (build spec), joined per
  creative by `DistributionID` ↔ `Distribution_` (version-number fallback).

## Coverage map
| Check | Source | Technique | Status |
|---|---|---|---|
| campaign_objective, campaign_buying_type | perf (Peacock vocab) | peacock branch | ✅ shipped |
| ad_status | perf `Creative_Status` | direct | ✅ shipped |
| ad_call_to_action | perf `CTABundle` | direct | ✅ shipped |
| ad_copy/headline/description_spelling | perf `FinalCopy` (Gemini) | — | ✅ shipped |
| ad_creative_dimensions | trafficking `Frame_Size` | peacock check | ✅ shipped (Phase B) |
| **adset_audiences** | perf `AudienceName` | adapter-shape `targeting.custom_audiences` | 🟢 Lever 1 |
| **adset_placements** (template "Placements" row) | perf `AirTable_Placement` | new check | 🟢 Lever 1 |
| **adset_start_date / adset_end_date** | trafficking `Media_Flight_Date`/`Media_End_Date` | adapter-shape adset `start_time`/`end_time`; peacock = Pass-on-match-else-Review | 🟢 Lever 1 (Kerri semantics) |
| **ad_flight_window** (new QC) | trafficking `Live_After_End_Date_Warning` | new check | 🟢 Lever 1 |
| show / genre / offer (new bonus) | trafficking `Show_Name…`/`Genre`/`Offer` | new checks | 🟢 Lever 1 (gated on Kerri rows) |
| campaign_budget, campaign_bid_strategy | — | — | 🔴 Lever 2 (Meta API) |
| adset_conversion_event, adset_spend_minimum/maximum, adset_age_min/max, adset_genders, adset_countries, adset_audience_exclusions, adset_optimization_goal, adset_attribution_setting | — | — | 🔴 Lever 2 (Meta API) |
| adset_name_conventions, ad_name_conventions | — | — | ✋ manual by design |

## Cardinal rule (non-negotiable)
Every wired check keeps the Peacock-Olympics guard: blank / ambiguous / unsynced
→ **Review, never a false Pass or Fix**. Reducing Reviews means converting them
to Pass/Fix *only where the data confidently supports it* — never lowering the
bar. Where the data genuinely can't answer (the 🔴 rows), Review is correct.

## Impact
Lever 1 flips the data-backed rows (audiences, + dates when they match) and adds
new high-signal Peacock checks (placements, QC flight-window, show/genre/offer).
The ~11 ad-set *settings* rows stay Review until **Lever 2** — a direct Meta
Marketing API connector for the Peacock ad account (separate decision: does
Wpromote already have Meta API access to `act_<peacock>`?).
