# Lever 2 — Meta Marketing API connector for Peacock (scope + decision doc)

**Status:** proposal, 2026-06-04. For Brad (architecture) + the Peacock/NBCU
Business-Manager owners. **This is a decision doc, not a build.** The gating
question is auth/access (§5), not engineering.

## 1. The gap (why this exists)
Peacock's QA bot fills every template row its data can support — but Peacock's
*entire* BigQuery footprint is reporting + trafficking/creative data. The Meta
ad-set **configuration/settings** are not synced anywhere in it. Confirmed
2026-06-04 against four sources: the curated table, the trafficking table, the
raw Meta staging tables (`prod_peacock_social_data.src_meta`/`stg_meta`), and the
audience-mapping table. Those fields live **only in the Meta Marketing API.**

Today those ~10 rows correctly return **Review** ("can't verify"). A clean
Peacock run is `Pass 9 | Review 15 | N/A 3 | Error 0`; ~10 of the Reviews are
these settings.

## 2. What it unlocks
Reading campaign + ad-set + ad settings from the Meta API makes these
deterministic (Pass/Fix) for Peacock — including the **Peacock-Olympics class**
(conversion event / optimization goal), the exact incident the bot exists for:

| Check | Meta API field |
|---|---|
| campaign_budget | `campaign.daily_budget` / `lifetime_budget` |
| campaign_bid_strategy | `campaign.bid_strategy` |
| adset_conversion_event | `adset.promoted_object.custom_event_type` |
| adset_optimization_goal | `adset.optimization_goal` |
| adset_attribution_setting | `adset.attribution_spec` |
| adset_spend_minimum / maximum | `adset.daily_min_spend_target` / `daily_spend_cap` |
| adset_age_min / age_max | `adset.targeting.age_min` / `age_max` |
| adset_genders | `adset.targeting.genders` |
| adset_countries | `adset.targeting.geo_locations.countries` |
| adset_audience_exclusions | `adset.targeting.excluded_custom_audiences` |
| (also confirms) adset_audiences, start/end dates, status | `targeting.custom_audiences`, `start_time`/`end_time`, `effective_status` |

## 3. The key point: the checks ALREADY speak Meta-API shape
The bot's deterministic checks were written against Meta's field shapes (the
standard non-Peacock path reads Airbyte-synced Meta data that mirrors the API).
So a Meta API adapter that returns evidence in the same shape lights these up
with **little or no change to check logic** — e.g. `check_adset_conversion_event`
already reads `promoted_object.custom_event_type`; `check_campaign_budget` already
reads `daily_budget` in minor units; `check_adset_attribution_setting` already
parses `attribution_spec`. **Lever 2 is ~90% an adapter + auth task, not a
checks rewrite.** (Lone mapping nit: Meta returns countries under
`targeting.geo_locations.countries`; the adapter maps it to `targeting.countries`.)

## 4. Data source
- **API:** Meta Marketing API (Graph API), pinned to a specific version.
- **Reads only** (`ads_read`) — the bot never writes to Meta.
- **Endpoints (bounded, a few calls per QA run):**
  - `GET /<campaign_id>?fields=objective,buying_type,daily_budget,lifetime_budget,bid_strategy,status,effective_status`
  - `GET /<campaign_id>/adsets?fields=name,optimization_goal,bid_strategy,attribution_spec,promoted_object{custom_event_type,pixel_id},daily_min_spend_target,daily_spend_cap,start_time,end_time,status,targeting{age_min,age_max,genders,geo_locations,custom_audiences,excluded_custom_audiences,publisher_platforms}`
  - `GET /<adset_id>/ads?fields=name,status,creative` (if ad-level settings needed)
- Account: `act_172945950683253` (and the other Peacock accounts that resolve to `C22848672`).
- **Freshness bonus:** the API is real-time (current settings), vs BigQuery's
  daily Airbyte lag — so this also fixes fresh-launch QA (the gap that left
  Kerri's brand-new campaign un-findable).

## 5. ⭐ The gating question (for the team — this decides feasibility)
To read Peacock's ad account via the API, Wpromote needs:
1. **A Meta App** (Meta for Developers) with the Marketing API + **`ads_read`**
   at Advanced Access (may require Meta App Review).
2. **A System User** in Wpromote's Meta Business Manager with a long-lived token.
3. **Read access to Peacock's ad account** (`act_172945950683253`) assigned to
   that system user. Wpromote manages Peacock's ads, so agency/partner access
   very likely already exists in Business Manager — **confirm it extends to an
   API token with `ads_read`.**

**If all three exist → Lever 2 is just: provide the token (→ Secret Manager) +
build the adapter.** If not → a Business-Manager admin (possibly on NBCU's side,
since they own the account) provisions the app/system-user/permissions first.

> **Questions to answer:** (a) Does Wpromote have a Meta App with `ads_read`
> Advanced Access? (b) Is there a System User token that can read
> `act_172945950683253`? (c) If not, who owns provisioning it — Wpromote BM or
> NBCU BM?

## 6. Architecture fit (clean, mirrors what's there)
- New adapter `app/adapters/meta_api/client.py` implementing the existing
  `MetaDataClient` protocol (`app/core/contracts.py`) — same interface as
  `BigQueryMetaClient` / `PeacockMetaClient`.
- For Peacock, a **composite** client merges sources: BigQuery/trafficking for
  creative + dimensions + flight + audiences + placements (already built), Meta
  API for the settings. Slots into the existing `RoutingMetaClient` (Peacock →
  composite). Non-Peacock clients are untouched.
- **12-Factor:** token in **Secret Manager** (never a key file), pinned API
  version + account map in env config. Backing-service interface, same as the
  Sheets/BQ/Slack adapters.
- Cardinal rule unchanged: ambiguous/missing → Review, never a false Pass.

## 7. Effort estimate (engineering, once access exists)
- Meta API client (auth, field fetch, pagination, error/rate-limit handling, the
  geo_locations mapping): ~2–3 days.
- Composite/routing wiring + evidence merge + config: ~1–2 days.
- Tests (mocked API responses, per-check live verification): ~1–2 days.
- **~1–1.5 weeks eng**, plus auth-provisioning lead time (hours if access exists;
  longer if App Review is needed — that's the schedule risk, not the code).

## 8. Risks / considerations
- **App Review for `ads_read`** is the main schedule unknown — confirm current
  access status early.
- **Token rotation** — system-user tokens are long-lived but rotate; store in
  Secret Manager, fail `/readyz` on a bad token (same pattern as the Sheets SA).
- **Rate limits** — trivial for QA volume (a few campaigns/day, bounded reads).
- **Scope** — start read-only, Peacock-only; generalizes to other clients later
  (the API also gives fresher settings than BigQuery for everyone).

## 9. Decision needed
Answer §5 (a/b/c). If access exists, this is a ~1–1.5 week build that converts
~10 Peacock Reviews into Pass/Fix and adds real-time freshness. If it doesn't,
the first step is a Business-Manager provisioning task, not code.
