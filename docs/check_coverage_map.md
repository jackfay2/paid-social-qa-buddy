# QA check coverage map (canonical)

**Kerri signed off on the check_id list 2026-06-04.** The bot handles all **33**
check_ids; both templates (standard + Peacock) reconcile with **zero unrecognized
check_ids**. This is the source of truth for what each check verifies and its
status. Verified against live data across 5 standard clients + the Peacock account.

**Legend:** ✅ deterministic now · ⚠️ data-gated (Review until Airbyte syncs the
field; *auto-upgrades* to Pass/Fix when it lands, no code change) · ✋ manual by
design · 〰️ conditional · 🦚 Peacock-mode behavior

## Campaign level
| check_id | verifies | source | status |
|---|---|---|---|
| campaign_objective | objective matches expected | `objective` | ✅ (🦚 Peacock vocab: Acquisition etc.) |
| campaign_buying_type | auction / reservation | `buying_type` | ✅ (🦚 Peacock: Biddable) |
| campaign_status | active / paused | `effective_status` | ✅ |
| campaign_start_date | launch date | `start_time` | ✅ |
| campaign_bid_strategy | bid strategy | `bid_strategy` (campaign or ad set) | ⚠️ synced ~2/5 clients |
| campaign_budget | daily/lifetime budget (in dollars) | `daily_budget`/`lifetime_budget` | ⚠️ ~2/5; often CBO-off (set per ad set) |

## Ad set level
| check_id | verifies | source | status |
|---|---|---|---|
| adset_status | active / paused | `effective_status` | ✅ |
| adset_start_date | flight start | `start_time` | ✅ standard · 🦚 Peacock: trafficking flight, Pass-on-match-else-Review |
| adset_end_date | flight end | `end_time` | 〰️ often legitimately null (ongoing) → Review; ✅ when set |
| adset_age_min / adset_age_max | age targeting | `targeting.age_min/max` | ✅ (targeting synced everywhere) |
| adset_genders | gender targeting | `targeting.genders` | ✅ (empty = All, Meta default) |
| adset_countries | geo targeting | `targeting.countries` | ✅ |
| adset_audiences | interests/custom audiences present | `targeting.custom_audiences` | ✅ where synced · 🦚 Peacock: AudienceName · ALWAYS_RUN |
| adset_audience_exclusions | exclusions present | `targeting.excluded_custom_audiences` | ✅ where synced · ALWAYS_RUN |
| adset_placements | placements | `AirTable_Placement` (Peacock) | 🦚 Peacock ✅ · standard ⚠️ (not in data) |
| **adset_conversion_event** | the optimization/conversion event ⭐ | `promoted_object.custom_event_type` | ⚠️ **not synced for any client yet** — the Peacock-Olympics check; enriched Review |
| adset_optimization_goal | optimization goal | `optimization_goal` | ⚠️ ~2/5; enriched Review |
| adset_attribution_setting | attribution window | `attribution_spec` | ⚠️ ~2/5; enriched Review |
| adset_spend_minimum / maximum | spend floor / cap present | `daily_min_spend_target`/`daily_spend_cap` | ⚠️ ~2/5 · ALWAYS_RUN |
| adset_name_conventions | name contains expected components | the ad set `name` | ✅ builder enters expected name/components; bot confirms each name contains them (boundary-aware) — Kerri approved 2026-06-04 |

## Ad level
| check_id | verifies | source | status |
|---|---|---|---|
| ad_status | active / paused | `effective_status` | ✅ |
| ad_count | expected number of ads | count of ads | ✅ |
| ad_destination_url | destination URL / domain | `link_url` | ✅ (domain-mode if a bare domain is entered) |
| ad_call_to_action | CTA button | `call_to_action_type` / `CTABundle` | ✅ |
| ad_creative_dimensions | 1x1 / 9x16 present | `Frame_Size` (Peacock) | 🦚 Peacock ✅ · standard ✋ manual (dims unreliable in BQ); enriched with ad count |
| ad_flight_window | pre-computed flight QC | `Live_After_End_Date_Warning` (Peacock) | 🦚 Peacock surface · standard Review · ALWAYS_RUN |
| ad_name_conventions | name contains expected components | the ad `name` | ✅ same as adset_name_conventions (builder-entered components) |

## Text checks (Gemini)
| check_id | verifies | source | status |
|---|---|---|---|
| ad_copy_spelling | body spelling | creative body / `FinalCopy` | ✅ |
| ad_headline_spelling | headline spelling | creative title / headline | ✅ |
| ad_description_spelling | description spelling | creative description | 〰️ often no description field → Review |

## Other
| check_id | verifies | source | status |
|---|---|---|---|
| download_changes | changes downloaded before building | n/a | ✋ manual (brief-mandated) |

## Summary
- **~20 deterministic today** (objective, buying type, statuses, dates, age/gender/geo, audiences, ad count, URL, CTA, copy/headline spelling, **naming conventions** [builder-entered components]).
- **7 data-gated** (bid strategy, budget, conversion event, optimization goal, attribution, spend min/max) — deterministic *today* for well-synced clients (e.g. C51305634), Review elsewhere; all auto-upgrade when Airbyte syncs the columns (Riley/Nikki). `promoted_object` (conversion event) is the one missing for *every* client — top priority when they have bandwidth.
- **2 manual by design** (creative dimensions [standard only; Peacock automates via Frame_Size], download_changes). Naming conventions moved to automated 2026-06-04 (Kerri approved).
- **2 conditional** (end date, description spelling).
- **2 Peacock-mode additions** (placements, flight-window QC).

Every Review/manual row is **enriched** with the context the bot already knows, so
a human reviewer gets a head start rather than a bare instruction.
