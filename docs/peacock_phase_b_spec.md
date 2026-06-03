# Peacock Mode — Phase B spec (the trafficking table)

**Status:** draft, 2026-06-03. Grounded in live inspection of
`nbc-287716.AirTable_v2.wp_live_trafficking` (197 cols, the Airtable trafficking
mirror). Phase A (perf table + vocab + trigger) is shipped; Phase B adds this
**build-time** source. **Blocked on one thing:** the canonical join key (asked
Pamela — see §6).

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
| **status / live** | `Trafficking_Status`, `Confirmed_Paused_Creative`, `Live_on_Platform.name` | partial | ⚠️ saw `Trafficking_Status="Live"` while `Live_on_Platform.name="Not Live"` — confirm which is authoritative (§6). |
| **objective / buy-type / CTA / copy / landing** | `Objective`, `Buy_Type[]`, `CTA_Bundle`, `Final_Copy`, `Destination_URL[]` | ✅ (perf) | trafficking is the **build-time** source of these; same Peacock vocab. |

## 4. NEW Peacock-specific checks (the "bonus" Kerri was interested in)
- **Pre-computed QC flags → surface directly.** `Live_After_End_Date_Warning` already holds human-readable QC (`"🚦 All Clear: Live within Flight Window 🚦"` vs `"‼️ Caution: Approaching End Date ‼️"`). Map: "All Clear" → Pass, "Caution/Warning" → Review/Fix. Near-free, high-signal. (Likely siblings: `Flight_Date_Vs_Trafficking_Date`, `Properties_*_vs_Flight_Date`, `Due_Date_Warnings_WORKDAY`.)
- **Offer / Show / Genre tagging.** `Offer[]` (sparse in samples — confirm it's populated for offer-driven campaigns), `Show_Name_For_File_Name`, `Genre`/`Peacock_Genre`. Builder asserts the expected offer/show; bot confirms it matches the trafficked value.

## 5. Build approach
- Extend `PeacockMetaClient` to also read `wp_live_trafficking` (filter `'Meta' IN UNNEST(Platform)`), dedup, and **merge into the same `evidence`** keyed by the join (§6). Prefer trafficking for *intended-spec* fields (the QA target) and for the Phase-B-only fields (dimensions, dates, QC flags).
- Array fields → first non-empty; `Live_on_Platform` → JSON-parse `.name`; DATE fields → ISO compare against builder input.
- New checks slot into the existing registry + the same template rows (creative dimensions, dates) — no template change. The QC-flag + Offer/Show checks are new `check_id`s if Kerri wants them.
- Keeps the Phase-A safety: any field still absent → Review.

## 6. Open items (for Pamela / Kerri)
1. **Join key (sent to Pamela):** trafficking `Creative_ID` is an Airtable ID, not the Meta creative id — join by `File_Name`, a code (`Code_Final`/`Claravine_Code`), or campaign name? *(Needed to merge trafficking ↔ performance per creative.)*
2. **Authoritative "live" signal:** `Trafficking_Status` vs `Live_on_Platform.name` disagreed in samples ("Live" vs "Not Live") — which should the status check trust?
3. **Dates:** do `Media_Flight_Date`/`Media_End_Date` map to the template's Start/End Date rows?
4. **Offer:** is `Offer` reliably populated for offer-driven campaigns (it was empty in general samples)?
5. **Which bonus checks does Kerri actually want** (QC-flag surfacing, Offer/Show/Genre)?
