# Standard-client validation + coverage findings (2026-06-04)

Ran the full check set against **5 real standard clients** (BigQuery `polaris-data-317717`,
billing `prj-prd-ai-ppc-qa-pkph`), feeding each check the *actual* extracted value
and bucketing the result. Method: `Error` = bug; `Review-despite-data-present` =
value-map/field-path gap; `Pass`/`Fix` = check engaged correctly.

## Headline: the engine is prod-ready (correctness)
Across **5 clients × 15 checks (75 runs): ZERO errors, ZERO value-map
misinterpretations.** Every real client value that was present canonicalized
correctly → Pass: objective, buying_type, status (5/5 each), and
bid_strategy/optimization_goal/attribution wherever the field existed. The
`Fix` results (adset_status, ad_status) are correct multi-entity behavior
(one entity's value fed; others genuinely differ). The checks, synonym maps, and
defensive Review-on-missing logic all behave correctly on real data.

## The real story: coverage is gated by the per-client Airbyte SCHEMA
The ad-set *settings* columns are present for some clients and **entirely absent
for others** — confirmed via INFORMATION_SCHEMA (missing column, not null data):

| Client | ad sets | optimization_goal | attribution_spec | settings columns present? |
|---|---|---|---|---|
| C51305634 | 19 | 19/19 ✓ | 19/19 ✓ | optimization_goal, attribution_spec, bid_strategy, spend caps, targeting |
| C91138922 | 21 | 0/21 | 0/21 | **only `targeting`** |
| C89175635 | 21 | 0/21 | 0/21 | **only `targeting`** |
| C10100976 | 14 | 0/14 | 0/14 | **only `targeting`** |
| C62303887 | **0** | — | — | **no ad sets synced at all** (459 ads exist) |

So: `targeting` (age / gender / countries / audiences) is synced **everywhere** →
those checks work broadly. But `optimization_goal`, `attribution_spec`,
`bid_strategy`, `daily_min_spend_target`, `daily_spend_cap` exist for **only ~1 of
5 clients**. For the rest, those checks correctly return **Review ("not available")**
— Review-by-design, not a bug.

## Implication — the standard path's "Lever"
How green the bot is on standard clients is **gated by the Airbyte field sync, not
the bot.** The single highest-value lever is **Riley + Nikki's field additions** —
getting `optimization_goal`, `attribution_spec`, `bid_strategy`, and the spend-cap
columns synced across *all* client datasets (and fixing clients like C62303887
whose ad sets aren't synced). Crucially, **this is achievable internally** (Airbyte
config), unlike Peacock's blocked Meta API. It's the standard-path analog of
Peacock's Lever 2 — but doable.

## Secondary finding — bid_strategy lives at two levels
`campaign_bid_strategy` reads `campaign.bid_strategy`, but for at least one client
(C51305634) bid_strategy is on the **ad set**, not the campaign (1/5 campaigns had
it at campaign level). **Enhancement:** have the check read campaign-level, then
fall back to ad-set-level, so it's deterministic regardless of where the sync puts
it. Low-risk, cardinal-safe.

## Validation caveat (honest)
The harness fed actuals via the same field access the checks use, so it strongly
validates the **value-maps + robustness (0 errors)** and confirms field-paths for
fields that were present — but it can't catch a hypothetical wrong-column-name path
where both sides miss identically. Net: high confidence on correctness; coverage
is the gating variable, and it's a data-sync problem, not a code problem.

## Recommended next steps (standard MVP)
1. **Drive the Airbyte field coverage** (Riley + Nikki): land optimization_goal /
   attribution_spec / bid_strategy / spend caps across all client datasets; fix
   missing-ad-set clients. Biggest lever on Pass rate.
2. **Lock Carrie's check_id list + template** — the canonical check set.
3. **bid_strategy two-level fallback** (small code enhancement).
4. **Prod cutover** with Maya (listener `qa_app=social` routing + worker promotion).
