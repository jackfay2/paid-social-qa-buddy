# Meta QA Template → Check_ID Mapping

Maps every row of the Paid Social (Meta) QA sheet template to a proposed
`check_id` and its build status. This is both the **contract** to lock with
Brandon/Kerri (what each check is named + what it verifies) and the **spec**
for our remaining build.

Source template (draft, 2026-05-28):
`https://docs.google.com/spreadsheets/d/12CMnQyqwgmKwGaujE5Hu64sswvlDfFT5Cs-JEJNtF4Y`

## How the bot reads the sheet (confirmed parity with Maya's Search bot)

- The bot keys each check off **column A (`Check_ID`)**. Column B (`objective`,
  `targeting.age_max`, …) is a **reference annotation** for which BigQuery field
  the check reads — NOT the key.
- Rows with an empty `Check_ID` are skipped. The draft template has column A
  blank today, which is why a raw run reads 0 rows. **Populating column A with
  the IDs below is the template-completion task.**
- The builder fills **Builder Input**; the bot writes **Pass or Fix** + **Action**.
- We own the `check_id` strings (Brandon OK'd `lowercase_underscore`; same style
  Maya uses).

## Status legend

- ✅ **BUILT** — function exists in `app/checks/registry.py` today.
- 🔨 **BUILD** — deterministic check we still need to write.
- 🤖 **GEMINI** — text check → `TEXT_CHECK_DEFINITIONS` (needs Brandon's instruction text).
- ✋ **MANUAL** — return `Review` with instructions (`ALWAYS_REVIEW_CHECK_ACTIONS`); never auto-attempt.
- ⏭️ **SKIP** — template marks "skip / hide for now".

## ⚠️ Two check styles — needs a Brandon/Kerri decision

The template mixes two input styles, and they need different check logic:

1. **Value-match** (builder types the expected value; bot compares to live data):
   objective, bid strategy, age, gender, dates. Our built checks all do this.
2. **Yes/No** (builder asserts "yes, I set this correctly"): name conventions,
   location, interests, spend min/max, exclusions. For these, *what does the bot
   verify?* Options: (a) presence — the field is set/non-empty; (b) ignore the
   Yes/No and value-match anyway; (c) treat as MANUAL/acknowledgement. **This is
   the single biggest open question for the check list.** Rows below marked
   "(Yes/No)" inherit this ambiguity.

---

## Campaign level (`facebook_ads__campaigns`)

| Template row | Col B (field) | Proposed check_id | Status | Notes |
|---|---|---|---|---|
| Campaign Objective | objective | `campaign_objective` | ✅ BUILT | legacy↔ODAX map calibrated |
| Buying Type | buying_type | `campaign_buying_type` | ✅ BUILT | |
| Budget | daily_budget | `campaign_budget` | 🔨 BUILD | BQ value is in minor units (e.g. `200000` = $2000.00). Need unit handling + builder format. |
| Bid Strategy | bid_strategy | `campaign_bid_strategy` | ✅ BUILT | |

## Ad Set level (`facebook_ads__adsets`)

| Template row | Col B (field) | Proposed check_id | Status | Notes |
|---|---|---|---|---|
| Name - Aligned with Conventions (Yes/No) | name | `adset_name_conventions` | ✋ MANUAL | Auto-verifiable only if naming convention is encoded as a rule; default MANUAL. |
| Ad Sets that Ads Should Be Live In (Yes/No) | name? | `adset_ads_placement` | 🔨 BUILD | Structural (which ad sets contain live ads vs builder expectation). Review-heavy for MVP. |
| Conversion Event Location | promoted_object.pixel_id / application_id / offline_conversion_data_set_id | `adset_conversion_location` | 🔨 BUILD | Which of pixel / app / offline dataset is set. |
| **Event Name** | promoted_object.custom_event_type | `adset_conversion_event` | ✅ BUILT (2026-05-29) | **🚨 Peacock-Olympics check.** Strict standard-event match; near-match like "purchase event" vs "purchase" → Review (never Pass). Confident standard-vs-standard mismatch → Fix. Not green-confirmed but foundational. |
| Start Date (If Applicable) | start_time | `adset_start_date` | ✅ BUILT | |
| End Date (If Applicable) | end_time | `adset_end_date` | ✅ BUILT | |
| Spend Minimum (Yes/No) | daily_min_spend_budget | `adset_spend_minimum` | 🔨 BUILD | Yes/No presence check. |
| Spend Maximum (Yes/No) | daily_spend_cap | `adset_spend_maximum` | 🔨 BUILD | Yes/No presence check. |
| Audience Targeting - Age Min | targeting.age_max ⚠️ | `adset_age_min` | ✅ BUILT | **Col B annotation is swapped** — reads `age_min`, per Brandon. |
| Audience Targeting - Age Max | targeting.age_min ⚠️ | `adset_age_max` | ✅ BUILT | Swapped annotation — reads `age_max`. |
| Audience Targeting - Gender | targeting.genders | `adset_genders` | ✅ BUILT | |
| Audience Targeting - Location (Yes/No) | targeting.countries + location_types | `adset_countries` | ✅ BUILT | Built as value-match; template uses Yes/No → reconcile (see check-styles note). |
| Interests and/or Custom Audiences (Yes/No) | targeting.custom_audiences | `adset_audiences` | 🔨 BUILD | Yes/No presence. |
| Audience Exclusions (Yes/No) | targeting.excluded_custom_audiences | `adset_audience_exclusions` | 🔨 BUILD | Yes/No presence. |
| Placements | targeting.publisher_platforms + *_positions | `adset_placements` | 🔨 BUILD | Compare platform/position set. |
| Optimization for Ad Delivery | optimization_goal | `adset_optimization_goal` | ✅ BUILT (2026-05-29) | String-enum value-match; conservative synonyms, Review on unmapped. BQ shape confirmed (e.g. CLICKS, OFFSITE_CONVERSIONS). |
| Attribution Delivery Setting | attribution_setting (BQ: `attribution_spec`) | `adset_attribution_setting` | ✅ BUILT (2026-05-29) | Parses Meta's `attribution_spec` list ({event_type, window_days}); compares as a set of (channel, days). Dropdown values from validation tab. Empty spec → Review. |

## Ad level (`facebook_ads__ads`)

| Template row | Col B (field) | Proposed check_id | Status | Notes |
|---|---|---|---|---|
| Name - Aligned with Conventions (Yes/No) | name | `ad_name_conventions` | ✋ MANUAL | Same as ad-set naming; MANUAL unless convention encoded. |
| Ad Status (Live or Paused?) | effective_status | `ad_status` | ✅ BUILT | |
| Facebook Page Selection | actor_id | `ad_facebook_page` | 🔨 BUILD | |
| Instagram Account Selection | instagram_user_id | `ad_instagram_account` | 🔨 BUILD | |
| Correct Creative - 1x1 and 9x16 | MANUAL | `ad_creative_dimensions` | ✋ MANUAL | Template explicitly marks MANUAL. |
| Ad Copy - No Typos | creative.object_story_spec.link_data.message | `ad_copy_spelling` | 🤖 GEMINI | spellcheck only (no translation/nuance). |
| Headline - No Typos | creative.object_story_spec.link_data.name | `ad_headline_spelling` | 🤖 GEMINI | |
| Description - No Typos | creative.object_story_spec.link_data.description | `ad_description_spelling` | 🤖 GEMINI | |
| Call To Action | creative.call_to_action_type | `ad_call_to_action` | 🔨 BUILD | Needs an active ad with populated creative to validate. |
| Site Links | site_links_spec | `ad_site_links` | 🔨 BUILD | |
| Info Labels | (skip / hide for now) | — | ⏭️ SKIP | Template defers. |
| Advantage+ Creative Enhancements | degrees_of_freedom_spec.creative_features_spec | `ad_advantage_creative` | 🔨 BUILD | |
| Landing Page (Website URL) | AdCreative.object_story_spec.link_data.link | `ad_destination_url` | ✅ BUILT | |
| Display URL | AdCreative.object_story_spec.link_data.caption | `ad_display_url` | 🔨 BUILD | Distinct from landing page (the shown URL). |
| Tracking - Domain/Pixel Selection | promoted_object.pixel_id / application_id / offline_conversion_data_set_id | `ad_tracking_pixel` | 🔨 BUILD | Overlaps `adset_conversion_location` — confirm whether tracked at ad or ad-set level. |
| Tracking - UTM Parameters | AdCreative.url_tags | `ad_utm_parameters` | 🔨 BUILD | Builder provides params for QA → compare `url_tags`. |

---

## Tally

- ✅ **BUILT and mapped: 11** — campaign objective/buying_type/bid_strategy; adset start/end/age_min/age_max/genders/countries; ad status/destination_url.
- 🔨 **BUILD (deterministic): ~15** — campaign_budget; adset conversion_location, **conversion_event (Peacock)**, spend_min, spend_max, audiences, exclusions, placements, optimization_goal, attribution; ad facebook_page, instagram_account, call_to_action, site_links, advantage_creative, display_url, tracking_pixel, utm_parameters.
- 🤖 **GEMINI: 3** — ad copy/headline/description spelling. Wiring is in place; needs Brandon's instruction text in `TEXT_CHECK_DEFINITIONS`.
- ✋ **MANUAL: 2–3** — creative 1x1/9x16; possibly the two naming-convention rows.
- ⏭️ **SKIP: 1** — info labels.

## Built checks with NO template row (decide: keep or drop)

These exist in our registry but the current template has no matching row:
`campaign_status`, `campaign_start_date`, `adset_status`, `ad_count`. Keep as
useful extras, or trim to match the template — Brandon/Kerri's call.

## Open questions for Brandon / Kerri

1. **Yes/No vs value-match** (above) — what does a Yes/No check actually verify?
2. **Green-highlighted rows** = already confirmed (per Jack). Overlay that status
   onto this table — can be auto-detected by reading the sheet's cell fill colors
   once it's shared with the SA.
3. **Naming conventions** — is there an encodable rule, or stays MANUAL?
4. **Pixel/tracking** tracked at ad level, ad-set level, or both? (`adset_conversion_location` vs `ad_tracking_pixel` overlap.)
5. **Budget** units + builder input format.
6. Confirm the **swapped age annotation** in column B is a template typo, not intended.

---

## Confirmed (green-highlighted) rows — the MVP check set

Column B is green-filled on 17 rows (detected by reading cell colors). Per Jack,
green = already confirmed. 10 of the 17 are already built:

| Confirmed row | check_id | Built? |
|---|---|---|
| Campaign Objective | campaign_objective | ✅ |
| Buying Type | campaign_buying_type | ✅ |
| Budget | campaign_budget | 🔨 |
| Bid Strategy | campaign_bid_strategy | ✅ |
| Name - Aligned with Conventions (ad set) | adset_name_conventions | ✋/🔨 |
| Start Date | adset_start_date | ✅ |
| End Date | adset_end_date | ✅ |
| Spend Minimum | adset_spend_minimum | 🔨 (Yes/No) |
| Spend Maximum | adset_spend_maximum | 🔨 (Yes/No) |
| Age Min | adset_age_min | ✅ |
| Age Max | adset_age_max | ✅ |
| Gender | adset_genders | ✅ |
| Location | adset_countries | ✅ |
| Name - Aligned with Conventions (ad) | ad_name_conventions | ✋/🔨 |
| Ad Status | ad_status | ✅ |
| Instagram Account Selection | ad_instagram_account | 🔨 |
| Call To Action | ad_call_to_action | ✅ (2026-05-29) |

**Priority build list (confirmed, not yet built): 6** — campaign_budget,
adset_spend_minimum, adset_spend_maximum, ad_instagram_account, + the two
naming-convention rows. Note 4 of the 6 are Yes/No-style → blocked on the
check-styles decision. (`ad_call_to_action` built 2026-05-29, value-matching the
18 dropdown CTA labels.)

**Gemini spelling checks now defined (2026-05-29):** `ad_copy_spelling`,
`ad_headline_spelling`, `ad_description_spelling` are live in
`TEXT_CHECK_DEFINITIONS` (narrow spelling-only instructions, Review on
uncertainty) and route through the batched text-check path. Brandon can refine
the instruction wording; scope is fixed by the brief.

## Canonical builder-input values (MASTER DATA VALIDATION tab)

The template's second tab is the dropdown source of truth for Builder Input.
Snapshotted to `data/meta_master_data_validation_export.csv`. These drive the
check value-maps:

- **Campaign Objective**: Sales, Awareness, Traffic, Engagement, Leads, App Promotion
- **Budget Strategy**: Campaign budget, Ad set budget (CBO vs ABO — future `campaign_budget_strategy` check)
- **Bid Strategy**: Highest volume or value, Cost per result goal, ROAS goal, Bid cap
- **Buying Type**: Auction, Reservation
- **Age Min / Max**: 18–65
- **Gender**: Men, Women, All
- **Attribution Spec**: 1-day click; 7-day click; 1-day click, 1-day view; 7-day click, 1-day view
- **Call To Action**: Learn More, Shop Now, Sign Up, Contact Us, Download, Book Now, Get Quote, Get Offer, Call Now, Send Message, Send WhatsApp Message, Order Now, Subscribe, Apply Now, Watch More, Use App, Buy Tickets, Get Directions

**Calibrated against these (2026-05-29):** `campaign_buying_type` now maps
"Reservation" → `RESERVED`; `campaign_bid_strategy` now maps "Highest volume or
value" → `LOWEST_COST_WITHOUT_CAP` and confirms "Cost per result goal" → `COST_CAP`.
Both were false-Review bugs against the exact dropdown labels before.

## Template handling model (mirrors Maya's Search bot)

- The Meta sheet is the **source-of-truth template** (analog of Maya's
  "Finalized Campaign Template" / `data/new_search_export.csv`). Snapshotted to
  `data/meta_qa_template_export.csv`. **We never write into it.**
- `Check_ID` (col A) is **pre-populated as part of finalizing the template** —
  not written at runtime. It's blank today because the template isn't finalized.
- Runtime: a builder copies the template, fills Builder Input, shares the COPY
  with the SA; the bot reads it and writes **only verdicts** (Pass or Fix /
  Action / QA initial) back into that copy. The master template is read-only.
- Our testing uses offline fixtures / our own copies — never the master template.
