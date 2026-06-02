# BigQuery Field Coverage — what's automated today vs. Review-by-design

**Source:** live schema of `polaris-data-317717.C61854560.facebook_ads__*`, dug
2026-06-02 (INFORMATION_SCHEMA + sampled `targeting` struct). This is the
baseline for one client; **per-client schema varies** (we `SELECT *` for that
reason), so re-confirm for each client — especially **Peacock** when we have its
dataset. The checks degrade safely: an absent field → **Review** ("not available
in BigQuery; verify manually"), never a wrong Pass/Fix.

## Three buckets

### ✅ Deterministic today — field is synced, check is built & verified
| Check | BQ field |
|---|---|
| `campaign_objective` | `campaigns.objective` |
| `campaign_buying_type` | `campaigns.buying_type` |
| `campaign_bid_strategy` | `campaigns.bid_strategy` |
| `adset_start_date` | `adsets.start_time` |
| `adset_end_date` | `adsets.end_time` |
| `adset_spend_minimum` | `adsets.daily_min_spend_target` |
| `adset_spend_maximum` | `adsets.daily_spend_cap` |
| `adset_age_min` / `adset_age_max` | `adset_targetings.age_min` / `age_max` |
| `adset_genders` | `targeting.genders` |
| `adset_countries` | `targeting.countries` (+ `location_types`) |
| `adset_audience_exclusions` | `targeting.excluded_custom_audiences` |
| `adset_optimization_goal` | `adsets.optimization_goal` |
| `adset_attribution_setting` | `adsets.attribution_spec` |
| `ad_status` | `ads.effective_status` |
| `ad_call_to_action` | `ad_creatives.call_to_action_type` |
| `ad_destination_url` | `ad_creatives.link_url` / `object_url` |
| `ad_copy_spelling` (Gemini) | `ad_creatives.body` |
| `ad_headline_spelling` (Gemini) | `ad_creatives.title` |

### ✋ Manual by design (Kerri, 2026-06-02) — Review with instructions, never auto
| Check | Why |
|---|---|
| `adset_name_conventions` | naming varies by client; no single rule |
| `ad_name_conventions` | same |
| `ad_creative_dimensions` | not derivable from BQ fields (template marks MANUAL) |
| `download_changes` | process step, not a data field |

### 🔭 Review-by-design TODAY — field NOT synced to BQ (this is the Riley + Nikki list)
These checks exist (or are trivial to add) but **return Review until the column
lands**. They will flip to deterministic the moment the field is synced — no
code change needed beyond wiring.

| Check | Missing BQ field | Lands in table |
|---|---|---|
| `adset_conversion_event` 🚨 | `promoted_object.custom_event_type` | `facebook_ads__adsets` |
| `adset_conversion_location` | `promoted_object.{pixel_id, application_id, offline_conversion_data_set_id}` | `facebook_ads__adsets` |
| `adset_audiences` (interests/custom) | `targeting.custom_audiences` | `targeting` struct / `adset_targetings` |
| `adset_placements` | `targeting.publisher_platforms` + `facebook_positions` + `instagram_positions` | `targeting` struct |
| `ad_facebook_page` | `actor_id` | `facebook_ads__ads` |
| `ad_instagram_account` | `instagram_user_id` | `facebook_ads__ads` |
| `ad_description_spelling` (Gemini) | ad description text (`object_story_spec…description`) | `facebook_ads__ad_creatives` |
| `ad_display_url` | `caption` | `facebook_ads__ad_creatives` |
| `ad_utm_parameters` | `url_tags` | `facebook_ads__ad_creatives` |
| `ad_site_links` | `site_links_spec` | `facebook_ads__ad_creatives` |
| `ad_advantage_creative` | `degrees_of_freedom_spec` (note: `asset_feed_spec` IS synced — partial possible) | `facebook_ads__ad_creatives` |

> 🚨 `adset_conversion_event` is the **Peacock-Olympics** check. It's the most
> important one to make deterministic — push `promoted_object` to the top of the
> Riley/Nikki request.

## Buildable now, but needs one product answer
- **`campaign_budget`** — `campaigns.daily_budget` / `lifetime_budget` are synced
  (BQ stores **minor units**, e.g. `200000` = $2,000.00). Blocked only on: *what
  format do builders type the budget in?* (dollars? `$2,000`? cents?) — a
  Kerri/Carrie/template decision, not a data gap.

## The ask for Riley + Nikki (one line)
> Please sync these Meta Marketing API fields into the `facebook_ads__*` tables:
> `promoted_object` (custom_event_type + pixel/app/offline ids), `targeting.custom_audiences`,
> `targeting.publisher_platforms` + positions, `ads.actor_id`, `ads.instagram_user_id`,
> and on `ad_creatives`: `url_tags`, `caption`, `site_links_spec`, `degrees_of_freedom_spec`,
> and the creative description text. Each unlocks one QA check from manual → automated.
