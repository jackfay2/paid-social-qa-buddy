# Peacock Mode — Phase B spec (the trafficking table)

**Status:** Phase B **v1 SHIPPED** 2026-06-03 (code + tests; not yet deployed).
Grounded in live inspection of `nbc-287716.AirTable_v2.wp_live_trafficking`
(197 cols, the Airtable trafficking mirror). **UNBLOCKED** — join confirmed
(§6.1): perf `DistributionID` ↔ trafficking `Distribution_` (verified 5/5, then
**1076/1076 ads** merged on live campaign `120215246378710260`), with
`VersionNumber` ↔ `Version_` as the fallback for un-reused historical creatives.

### What v1 ships
- **Trafficking merge** (`PeacockMetaClient`): one extra query per campaign joins
  the build-spec onto each ad as `ad["trafficking"]` (frame_size, asset_type,
  flight dates, QC flag, trafficking_status, offer/show/genre). Best-effort —
  any failure (or SA missing dataset access) logs + degrades to perf-only Phase A.
- **`ad_creative_dimensions` is now deterministic in Peacock mode** (was manual
  Review): compares the builder's expected dimensions against the trafficked
  `Frame_Size`, by aspect ratio (accepts pixels `1080x1920` or ratios `9:16`).
  Cardinal-rule safe: confident Fix only when every ad's size was seen; any
  ambiguity (extra sizes, unsynced ads, unparseable tokens) → Review with the
  actual trafficked sizes always echoed. Verified live (16:9→Fix, 9:16→Review).
- Config: `qa_peacock_trafficking_dataset` / `_table` (blank table disables the
  merge). Worker SA reads `AirTable_v2` via the existing **project-level**
  dataViewer grant on `nbc-287716` — no new access needed.
- 16 new tests (merge, version fallback, graceful degrade, caching, dimensions
  Pass/Fix/Review paths, pipeline routing). Full suite 420 green.

### Deferred to v2 (data merged + ready, checks not wired)
Flight-date and QC-flag checks (`adset_start/end_date`, a new flight-window
check) wait on Kerri's semantics (§6.5–6.6) and the ad-vs-ad-set level question
— flight dates are per-creative in trafficking, the template rows are ad-set
level. The fields are already in `ad["trafficking"]`, so wiring them is small
once Kerri confirms.

> **Maintenance caveat (Pamela, 2026-06-03):** she is *no longer maintaining the
> performance data* (`creative_and_audience_data`). As of today both tables are
> current (newest Meta row = 2026-06-03), so this is a **forward-looking risk**,
> not a present blocker — but the perf table is also our *bridge* (Meta
> `campaign_id` → `DistributionID` → trafficking). If it ever stops ingesting new
> campaigns, new-campaign QA loses that bridge. Mitigation: monitor perf freshness
> empirically (one `MAX(Date)` query); if it goes stale, pivot to a direct
> `campaign_id` → trafficking lookup and make trafficking the primary source. No
> further Pamela question needed for the build.

## 1. Why the trafficking table matters
The performance table (`creative_and_audience_data`) is *delivered* data — it
only exists after a campaign runs. The trafficking table is the **intended build
spec** (what *should* be built/live), so it:
- enables **build-time QA** (before the campaign has performance data — the gap that left Kerri's brand-new campaign un-findable), and
- is the **source of truth for "what was supposed to be"**, which is exactly what QA compares against.

## 2. Field types (gotchas)
Scalars: `Objective`, `CTA_Bundle`, `Final_Copy`, `Show_Name_For_File_Name`,
`Genre`, `Peacock_Genre`, `Frame_Size`, `Asset_Type`, `Creative_Type`, `Length`,
`Status`, `Trafficking_Status`, `Live_After_End_Date_Warning`, `Creative_ID`,
`File_Name`, `Code_Final`, `Campaign_Name_ORDERED_`.
**Arrays** (`ARRAY<STRING>`, Airtable multi-value — take first non-empty / membership): `Buy_Type`, `Destination_URL`, `Offer`, `Platform` (filter Meta via `'Meta' IN UNNEST(Platform)`).
**DATE:** `Media_Flight_Date`, `Media_End_Date`. **BOOL:** `Confirmed_Paused_Creative`, `Ready_For_Delivery`.
`Live_on_Platform` is a **JSON blob** (e.g. `{"name":"Not Live",…}`) — parse `.name`.

## 3. Checks Phase B UNLOCKS (verified the fields exist + carry real values)
| Check | Trafficking field | Today (Phase A) | Notes |
|---|---|---|---|
| **`ad_creative_dimensions`** | `Frame_Size` (`1080x1920`, `1080x1350`) + `Asset_Type` | ✋ MANUAL Review | **Big win** — automate the 1×1 / 9×16 check (1080x1920=9:16, 1080x1080=1:1). |
| **`adset_start_date` / `adset_end_date`** | `Media_Flight_Date` / `Media_End_Date` (real DATEs) | 🔭 "not available" | flight-window dates — confirm with Kerri these map to the template's start/end. |
| **status / live** | `Trafficking_Status`, `Confirmed_Paused_Creative` | partial | ✅ resolved empirically: `Trafficking_Status="Live"` on **all** rows; `Live_on_Platform.name="Not Live"` on **99%** (22556/22815) — so `Live_on_Platform` is **not** a reliable Fail driver. Trust `Trafficking_Status` + `Confirmed_Paused_Creative`; Review (never silent Fail) on ambiguity. |
| **objective / buy-type / CTA / copy / landing** | `Objective`, `Buy_Type[]`, `CTA_Bundle`, `Final_Copy`, `Destination_URL[]` | ✅ (perf) | trafficking is the **build-time** source of these; same Peacock vocab. |

## 4. NEW Peacock-specific checks (the "bonus" Kerri was interested in)
- **Pre-computed QC flags → surface directly.** `Live_After_End_Date_Warning` already holds human-readable QC (`"🚦 All Clear: Live within Flight Window 🚦"` vs `"‼️ Caution: Approaching End Date ‼️"`). Map: "All Clear" → Pass, "Caution/Warning" → Review/Fix. Near-free, high-signal. (Likely siblings: `Flight_Date_Vs_Trafficking_Date`, `Properties_*_vs_Flight_Date`, `Due_Date_Warnings_WORKDAY`.)
- **Offer / Show / Genre tagging.** `Offer[]` (sparse in samples — confirm it's populated for offer-driven campaigns), `Show_Name_For_File_Name`, `Genre`/`Peacock_Genre`. Builder asserts the expected offer/show; bot confirms it matches the trafficked value.

## 5. Build approach
- Extend `PeacockMetaClient` to also read `wp_live_trafficking` (filter `'Meta' IN UNNEST(Platform)`), dedup, and **merge into the same `evidence`** keyed by the confirmed join: `CAST(perf.DistributionID AS STRING) = CAST(traf.Distribution_ AS STRING)`, falling back to `VersionNumber`/`Version_` when `DistributionID` is blank. Prefer trafficking for *intended-spec* fields (the QA target) and for the Phase-B-only fields (dimensions, dates, QC flags).
- Array fields → first non-empty; `Live_on_Platform` → JSON-parse `.name`; DATE fields → ISO compare against builder input.
- New checks slot into the existing registry + the same template rows (creative dimensions, dates) — no template change. The QC-flag + Offer/Show checks are new `check_id`s if Kerri wants them.
- Keeps the Phase-A safety: any field still absent → Review.

## 6. Open items

### Resolved — nothing further needed from Pamela
1. ✅ **Join key (Pamela, 2026-06-03):** the **distribution ID** field — `perf.DistributionID` ↔ `traf.Distribution_` (INT64; CAST both to STRING). Fallback for un-reused historical creatives whose distribution ID is blank: the **version number** — `perf.VersionNumber` ↔ `traf.Version_`. Verified 5/5 against live campaign `120215246378710260` (returns Objective, Frame_Size, flight dates, QC flag, Show).
2. ✅ **Authoritative "live" signal (resolved empirically):** `Trafficking_Status="Live"` on all rows; `Live_on_Platform.name="Not Live"` on 99% → `Live_on_Platform` is unreliable as a generic signal. Status check trusts `Trafficking_Status` + `Confirmed_Paused_Creative`; ambiguity → Review, never silent Fail.
3. ✅ **Offer population (resolved empirically):** populated on only **2%** (459/22815) of Meta rows — it's a sparse tag for offer-driven campaigns. Design: Offer check is NA unless the builder asserts an offer, then compare. No reliability question for Pamela.
4. ✅ **Perf-table freshness (resolved empirically):** newest Meta row = 2026-06-03 (current today). Pamela's "no longer maintaining" is a forward risk to monitor (one `MAX(Date)` query), not a present blocker — see the maintenance caveat at the top.

### Open — for Kerri, non-blocking (fold into a normal check-in, NOT a separate ask)
5. **Dates:** do `Media_Flight_Date`/`Media_End_Date` map to the template's Start/End Date rows? (Until confirmed, surface as Review with the trafficked dates shown.)
6. **Which bonus checks does Kerri actually want** (QC-flag surfacing, Offer/Show/Genre)? Build the high-signal ones (dimensions, dates, QC flag) regardless; gate the tagging checks on her answer.
