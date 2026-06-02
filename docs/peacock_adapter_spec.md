# Peacock special-case adapter — design spec

**Status:** draft for review with Kerri + Pamela Nelson (Peacock data SME).
**Author:** Jack (with Claude), 2026-06-02. Grounded in live data inspection of `nbc-287716`.

## 1. Why Peacock needs special handling

Peacock (`NBCU - Peacock`, Wpromote client **`C22848672`**) is in Polaris like everyone else, but its **ad data does not flow to the standard `polaris-data-317717.C<client>.facebook_ads__*` Airbyte sync** the bot reads for every other client:

- `polaris-data-317717.C22848672.facebook_ads__*` **exists but is frozen at 2023-03-01** — no current campaigns (newest campaign created 2023; all 17-digit IDs; zero of the current 18-digit `120…` Meta IDs). Useless for live QA.
- Peacock's **live** Meta data lives in a **standalone GCP project `nbc-287716`**, table `prod_peacock_final_data.creative_and_audience_data` — fresh through 2026-06-02, with **30k+ Meta rows since March** (and TikTok/Snap/Reddit/Pinterest/DV360/etc. in the same table, `Platform`-tagged).
- Moving Peacock into the standard sync is **not on the table** (per Jack). So we build a **client-specific adapter** that reads Peacock's data in place.

This is a clean fit for the existing architecture (adapter behind a protocol + per-client routing; precedent: the Search repo's client-specific `greystar_search_checks.py`).

## 2. Data source (verified 2026-06-02)

- **Project / dataset / table:** `nbc-287716.prod_peacock_final_data.creative_and_audience_data` (158 columns, partitioned by month on `Date`).
- **Grain:** one row per `Date × Creative × placement` — a *performance/trafficking* table, not a settings table. Example: campaign `120215246378710260` = 2,277 rows over 33 days, 5 ad sets, 131 creatives.
- **Hierarchy:** `Campaign_ID` → `Ad_Set_ID` → `Creative_ID` (the "Creative" is effectively the ad).
- **Filter:** always `Platform = 'Meta'` for this bot (the table is multi-platform).
- **Auth:** the worker SA `ppc-qa-buddy@…` needs **`bigquery.dataViewer` on `nbc-287716`** (Jack's local ADC has access today; the prod SA must be granted it — `pse-daily-health-check@…` already has it as a model). Jobs bill to our worker project (jobUser there), read `nbc-287716` via dataViewer — same `billing_project` split the standard client already uses.

## 3. Architecture

- New adapter **`app/adapters/peacock/client.py` → `PeacockMetaClient`**, implementing the **same `MetaDataClient` protocol** as `BigQueryMetaClient` (`get_campaign`, `get_ad_sets`, `get_ads`). It queries the unified table, **dedups the daily rows to distinct entities**, and shapes them into the **same `evidence` dicts** the existing checks already consume.
- **Routing:** in worker wiring, if `client_id == "C22848672"` → use `PeacockMetaClient` (config: project `nbc-287716`, dataset `prod_peacock_final_data`, table `creative_and_audience_data`); else the standard `BigQueryMetaClient`. One branch. (`C22848672` passes the existing `^C\d{8}$` validation, so account→client resolution is unchanged.)
- **Checks are reused as-is.** Thanks to the 2026-06-02 audit fixes, every check returns **Review** ("not available") when its field is absent — so the unsupported Meta-settings checks **safely self-disable** on Peacock with no wrong verdicts and no code changes. Only the *supported* checks need any Peacock-specific calibration (vocabulary maps).

### Dedup rule (critical)
Settings (objective, copy, URL, status) are **stable across the daily rows** for an entity; metrics (Spend, Impressions…) are not — and we don't use metrics for QA. So: `SELECT DISTINCT` the entity + its setting columns (take the latest `Date` per entity if a setting ever changes). **Never sum/aggregate** — this is a QA-of-settings job, not reporting.

## 4. Field mapping → `evidence` (grounded in real values)

| evidence path | Peacock column | Sample real value | Notes |
|---|---|---|---|
| `campaign.objective` | `Objective` | `"Acquisition"` | **Peacock vocabulary**, not Meta enums — needs a Peacock synonym map (Acquisition/Awareness/Engagement/Retention; matches the ACQ/AWA/ENGT/RET campaign-name prefixes). |
| `campaign.buying_type` | `Buy_Type` | `"Biddable"` | map `Biddable→AUCTION` (confirm with Kerri). |
| `campaign.name` | `Campaign` | `Peacock_ACQ_Conversions_…` | |
| `ad_sets[].name` | `Ad_Set_Name` | `Peacock_FBIG_AllPlacements_…` | |
| `ad_sets[].id` | `Ad_Set_ID` | | |
| `ads[].id` | `Creative_ID` | | "Creative" == ad here. |
| `ads[].name` | `Creative` | `2501aoalwson_video_1080x1350_15_trailer_` | filename, not copy. |
| `ads[].effective_status` | `Creative_Status` | `"Live"`/`"Paused"` | map Live→ACTIVE, Paused→PAUSED. |
| `ads[].creative.body` (copy) | **`FinalCopy`** | (78% populated) | **Use `FinalCopy`, NOT `AdHeadline`/`AdDescription` (both 0% populated — video creatives).** |
| `ads[].creative.link_url` | `URL` | `https://www.peacocktv.com/stream-tv/…` (93%) | landing page + UTM source. |
| `ads[].creative.call_to_action_type` | `CTABundle` | `"Sign Up"` | map to enum (confirm vocabulary). |

## 5. Check coverage (honest)

**✅ Supported now (real verdicts):**
- `ad_copy_spelling` — Gemini on **`FinalCopy`** (78% of creatives; the rest → Review "no copy text", never a false Pass).
- `ad_destination_url` (landing page) — `URL` (93%).
- `ad_utm_parameters` — parse `URL` query string.
- `campaign_objective` — `Objective` (needs the Peacock vocab map).
- `ad_status` — `Creative_Status`.

**🔶 Supported with calibration (vocabulary mapping):**
- `campaign_buying_type` (`Buy_Type`), `ad_call_to_action` (`CTABundle`), and possibly start/end dates from `FlightStart`/`FlightEnd`/`CampaignLaunchDate` — **semantics differ** from Meta ad-set `start_time`; confirm with Kerri whether the template's date check should map to flight dates.

**❌ Not supported — no column in Peacock's data (→ Review "not available in Peacock data source"):**
bid strategy, campaign budget, targeting (age / gender / country), optimization goal, attribution setting, conversion event / pixel, spend min/max, audiences / exclusions, Facebook Page, Instagram account, separate headline & description spelling, display URL, site links, Advantage+. (These are campaign *settings* the reporting table simply doesn't carry.)

**Net:** Peacock QA would be **strong on creative/copy/landing-URL/objective/status** — the highest-human-error, Peacock-Olympics-class area — but would **not** cover the Meta campaign-settings checks. That's a data limitation, not a bot limitation, and every unsupported check degrades safely to Review.

## 6. Bonus — Peacock-specific checks the standard template can't do

The unified table carries rich trafficking metadata: `Offer_Name`, `Offer_Price`, `Offer_Duration`, `Sub_Offer_Name`, `Show`, `Genre`, `Network`, `Format`, `Specs`, `FrameSize`, `CampaignLaunchDate`. These enable **Peacock-specific QA** the generic Meta template doesn't have — e.g. *"does the offer/price shown in the creative match the trafficked `Offer_Name`/`Offer_Price`?"*, *"is the `Show`/`Genre`/`Format` tagged correctly?"* These may be **more valuable to Kerri's actual workflow** than the generic checks. Propose as a fast-follow; scope with Kerri.

## 7. Open questions (for Kerri / Pamela)

1. Is **`FinalCopy`** the canonical ad-copy field for Meta, or is the real copy elsewhere (object-story-spec equivalent)? (AdHeadline/AdDescription are empty.)
2. Should the QA template's **objective** check expect Meta vocabulary or Peacock's (`Acquisition`/…)? — drives whether we map Peacock→Meta or QA in Peacock's own terms.
3. Do the **date** checks map to flight dates (`FlightStart`/`FlightEnd`) or true Meta ad-set start/end? (Different concepts.)
4. Which checks does Kerri **actually want** for Peacock? (She may not need the unsupported Meta-settings ones at all.)
5. Are the **Offer/Show/Format** metadata checks (§6) worth building?
6. Confirm **prod SA `bigquery.dataViewer` on `nbc-287716`** grant.

## 8. Build plan

- **Phase 0 (prereq):** grant the prod SA `dataViewer` on `nbc-287716`.
- **Phase 1:** `PeacockMetaClient` (query + dedup + evidence shaping) + routing for `C22848672`. Run the ✅ checks (copy spelling via `FinalCopy`, landing URL, UTMs, objective, status). Verify against a campaign Kerri has manually QA'd.
- **Phase 2:** vocabulary calibration maps (objective, buy_type, CTA, status — from a full `DISTINCT` scan + Kerri) and the 🔶 date check.
- **Phase 3:** Peacock-specific Offer/Show/Format checks (§6), if Kerri wants them.

## 9. Notes / caveats

- **Daily-stale:** like the standard sync, this table is daily — and Kerri's *exact* demo campaign `120249542911530260` wasn't in it yet at spec time (too new / id not yet landed), though peers with the same `120…0260` format are. Confirm freshness expectations.
- **Cost:** the table is large + multi-platform; always filter `Platform='Meta'` + `Campaign_ID` (+ a `Date` window) so queries scan narrowly. Cache per run like the standard adapter.
- **No metrics in QA:** we read settings columns only; never the Spend/Impression/conversion columns.
