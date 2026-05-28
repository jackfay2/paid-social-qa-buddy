# Paid Social QA Buddy Bot — Project Memory

Auto-loaded by Claude Code when working in the `paid-social-qa-buddy` repo. Captures durable project decisions so they don't have to be rederived each session.

## Project status

Maya Gundepudi (Search QA Buddy owner) is handing off the Paid Social extension to Jack Fay. Original MVP target was **June 5, 2026**; that date has passed and the realistic target slipped (single-developer project, dependency chain). Phase 1 is Meta only. Meta data comes from BigQuery (Airbyte-synced daily from the Meta Marketing API), not directly from Meta. Phases 2+ add TikTok, Snap, Reddit, Pinterest, LinkedIn using the same architecture with new connectors and registries.

**Build status: vertical slice built, data layer validated against live BigQuery.** Repo at `github.com/jackfay2/paid-social-qa-buddy` (local: `/Users/jack.fay/paid-social-qa-buddy`). Built + unit-tested (218 tests, CI green on push): five backing-service adapters, account resolver, Gemini text-check adapter, pipeline, orchestration, worker endpoint, Cloud Tasks OIDC auth, 5 campaign-level deterministic checks, 7 ad-set-level deterministic checks (status / start_date / end_date / age_min / age_max / genders / countries — targeting reads handle nested-vs-flat schema variance). The **full worker has been run end-to-end against real BigQuery** (resolver + fetch + orchestration + checks → correct verdicts, 2026-05-27). The literal Google Sheets and Slack calls remain mocked/simulated (Sheets OAuth is blocked on this workspace — Maya's prod pattern is SA-JSON-from-Secret-Manager, see Open decisions). See Implementation status below.

## People

- **Jack Fay** — implementer (user of this Claude Code instance)
- **Maya Gundepudi** — Search QA Buddy owner, handing off Social; owns the existing repo
- **Brad Ash** — head engineer at Wpromote; GCP/architecture authority. Mandated 12-Factor as the design foundation
- **Kerri Lewis** — Paid Social team leader; owns the QA sheet template and final `check_id` list. (The original handoff called her "Carrie" — same person, spelled Kerri.)
- **Brandon** — interim POC for template/check questions while Kerri is out
- **Riley, Nikki** — BigQuery field additions to land missing Meta fields in the `facebook_ads__*` tables
- **Jason Burma, Anthony Murillo** — security / tech leadership
- **Sami Stoltenberg** — Paid Social team leader / stakeholder approval (with Kerri)
- **ai-team@wpromote.com** — group with access to all GCP/Firestore tooling (Brad granted)

## Architecture (locked)

- **Shared listener, split workers per platform.** Slack physics: one Slack app `@qa-buddy` → one Events API URL → one listener service. Listener reads `platform` field from the parsed message and enqueues to the platform-specific Cloud Tasks queue.
- **Existing repo (Maya's, hosts the listener):** listener + Search worker stay there. Maya makes the `platform` routing change in her repo (eventually).
- **Test setup (decided 2026-05-28):** for validation, Maya and Jack are copying her test listener into a new test service with the Social changes added, running in a separate test Slack workspace. Lets the two listeners run side-by-side in test without touching her Search test flow. Once green, the Social changes get merged back into Maya's main listener for production (per Brad's "share the Slack app" rule). So the prod listener stays unified; the test path is a parallel branch.
- **Social repo (Jack's, built):** `github.com/jackfay2/paid-social-qa-buddy`. Social worker + BigQuery adapter (Meta data) + account resolver + Polaris adapter (directory lookup) + check registry + orchestration + worker endpoint. Deploys its own Cloud Run worker. Receives tasks from `qa-buddy-runs-social` Cloud Tasks queue.
- **Slack model:** `@-mention` with `key: value` lines (one field per line). Not slash commands. `@qa-buddy` is the shared mention.
- **One shared Slack app** per Brad ("managing too many Slack apps is an overhead nightmare").
- **12-Factor app principles** are mandated by Brad as the foundation. Treat as a hard constraint, not a style guide.

## Data architecture (correct mental model)

The original handoff doc treated Polaris as the Meta data source. It is not. Three distinct systems:

- **Polaris** — Wpromote's internal CRM / service directory at `https://api.polaris.wpromote.com`. Token auth (`Authorization: Token <api_token>` header, NOT Bearer). Tells us who has Paid Social, who the AMs / ADs / managers are, recipient emails. Used for routing and stakeholder notification. Reference implementation: `core/recipients.py` in `ps-social-daily-health-check` (~150 lines, sole Polaris file in that repo).
- **BigQuery (`polaris-data-317717.C<client_id>.facebook_ads__*`)** — the actual Meta data. Airbyte syncs daily from the Meta Marketing API. Campaign settings, ad sets, ads, creative, audiences live here. Service-account auth via GCP ADC. Riley + Nikki's sprint adds columns to these tables.
- **Direct Meta Marketing API** — for fields BigQuery doesn't sync, or sub-daily freshness. Probably out of scope for MVP.

Adapter layout in the Social repo (as built):

- `app/adapters/polaris/client.py` — `PolarisClient`, directory lookup (mirrors `core/recipients.py`)
- `app/adapters/bigquery/client.py` — `BigQueryMetaClient`, queries the 4 `facebook_ads__*` tables for one campaign
- `app/adapters/bigquery/resolver.py` — `BigQueryAccountResolver`, maps account_id → client_id

Check functions take `evidence` dicts shaped from BigQuery results: `{"campaign": {...}, "ad_sets": [...], "ads": [...], "client_id": ..., "campaign_id": ...}`.

Per-client routing: each client has its own BigQuery dataset (`C<client_id>`). Routing is "use the right dataset name in the query," not "swap auth contexts." No `MetaAccountRouting` secret needed.

**account_id → client_id resolution (decided):** the envelope carries the Meta `account_id`; the worker resolves `client_id` itself by querying the cross-client `summary.facebook_ads__account_performance` table (has both columns). Returns None for accounts with no performance data → orchestration surfaces a clear "account not found" error. Chose worker-side resolution over builder-typed or listener-resolved, for control. There is no cross-client `facebook_ads__accounts` table — only the performance one — hence that source.

**BigQuery field coverage — CLIENT-DEPENDENT (key live finding, 2026-05-27).** Per-client datasets do NOT share an identical schema. The first dig (`C00030334`, a sparse client) suggested only ~16 of ~37 checks were backed — but an active client (`C61854560`) had far more: `bid_strategy`, `daily_budget`, `lifetime_budget`, spend caps, `end_time`, `optimization_goal`, `attribution_spec`, and targeting (age/gender/location/audiences) **nested inside the adsets row** rather than a separate `facebook_ads__adset_targetings` table. Implications:
- Coverage varies by client; the "~16 of 37" figure was pessimistic (sparse client). Re-assess against a rich client before telling Riley/Nikki what's missing.
- **Targeting location varies**: nested in `facebook_ads__adsets` for some clients, separate `facebook_ads__adset_targetings` table for others. Ad-set checks must handle both.
- Because of this variance, **`BigQueryMetaClient` uses `SELECT *`** (not named columns) — naming columns breaks on whatever a given client's table is missing. Check functions read fields defensively (`.get()`; absent → Review).
- The ad `creative` record was all-null on the (old, paused) campaign tested — creative text may only populate for active ads. Validate creative/copy checks against an active ad.
- Template fix (Brandon confirmed): the age_min/age_max mapping was swapped — use age_min for Age Min.
- **Real objective values are legacy** (CONVERSIONS, LEAD_GENERATION, PAGE_LIKES), not the new ODAX `OUTCOME_*`. The `campaign_objective` check's value-map needs calibration with Brandon to cover both (it currently only coincidentally handles CONVERSIONS).

## Hard rule: do NOT touch the Search repo

The Search repo lives at `/Users/jack.fay/Paid Social QA Buddy Bot/qa-buddy-bot-main/` (unzipped from Maya's GitHub zip). It is **read-only reference**. No commits, no PRs, no edits. All Social work goes in the Social repo at `/Users/jack.fay/paid-social-qa-buddy`.

## Implementation status (Social repo)

Built and unit-tested (163 tests; CI runs them on push). The data layer + full orchestration have been validated against live BigQuery. Each adapter sits behind a Protocol in `app/core/contracts.py` (12-factor IV).

- **Adapters:**
  - `app/adapters/bigquery/` — `BigQueryMetaClient` (campaign/adset/ad fetch via `SELECT *`, ID validation, per-job cache) + `BigQueryAccountResolver` (account_id → client_id)
  - `app/adapters/polaris/` — `PolarisClient` (DRF pagination, `Token` auth, recipient resolution)
  - `app/adapters/slack/` — `SlackClient` (chat.postMessage, `Bearer` auth, transient/terminal error classification)
  - `app/adapters/storage/` — `InMemoryRunStore` + `FirestoreRunStore` (run lifecycle, notification dedup, tags records `qa_app="social"`)
  - `app/adapters/sheets/` — `GoogleSheetsClient` (alias-based header detection, batched writes, specific "not shared with SA" error) + pure `parser.py`
  - `app/adapters/gemini/` — `GeminiClient` (batched text checks via Gemini REST/httpx, confidence threshold, Review on any failure) + `StubGeminiClient`. NOT yet wired into orchestration.
- **Core:** `app/core/pipeline.py` (execute_checks, build_summary), `app/core/orchestration.py` (`SocialQAOrchestrationService` — full flow; every failure path returns a terminal result with a clear message)
- **Endpoint:** `app/api/server.py` `/tasks/qa/run` (parse → auth → timeout-guarded run → Slack post with dedup), `app/api/wiring.py` (adapter assembly, monkeypatch-able), `app/api/models.py`, `app/api/task_auth.py` (Cloud Tasks OIDC verification — implemented, fail-closed)
- **Checks:** `app/checks/meta_checks.py` — campaign-level (`campaign_objective`, `campaign_buying_type`, `campaign_status`, `campaign_start_date`, `campaign_bid_strategy`) + ad-set-level (`adset_status`, `adset_start_date`, `adset_end_date`, `adset_age_min`, `adset_age_max`, `adset_genders`, `adset_countries`). Targeting fields read via `app/checks/_targeting.py` (`read_targeting`) so nested-RECORD vs flat-column schemas both work. All registered in `app/checks/registry.py`. Ad-set semantics: builder input is "every ad set must match this expectation"; any divergence → Fix pointing at the first mismatched ad set.
- **Dev scripts:** `scripts/live_check.py` (live BQ resolver+fetch smoke), `scripts/local_qa_run.py` (full orchestration vs live BQ, in-memory sheet), `scripts/setup_test_sheet.py` (creates a test sheet via your ADC).
- **CI:** `.github/workflows/ci.yml` runs pytest on Python 3.11 on push/PR.

**Not yet done (rough priority order):**
1. Wire the Gemini adapter into orchestration (build the text-check batch from ad evidence, merge verdicts) — needs Brandon's text-check defs + an active ad with populated creative to validate.
2. Live Google Sheets read/write — user-OAuth Sheets scopes are blocked on this workspace; use the service account (`GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON`) and share the sheet with the SA. Until then sheets are exercised via the in-memory stand-in in `local_qa_run.py`.
3. Maya's listener change (route `qa_app=social`; relax the 10-digit account_id validation so Meta IDs aren't rejected).
4. GCP provisioning (`qa-buddy-runs-social` queue, `qa-buddy-worker-social{,-test}` Cloud Run, IAM incl. BQ read on `polaris-data-317717`).
5. Expand the check registry (Kerri locks check_ids; Riley/Nikki land fields; calibrate value-maps with Brandon).
6. Deploy + the full test-channel flow (gated on the above).

## Coordination contract with Maya

Lock these three in a shared doc before either repo ships changes. They are the entire surface area between her code and ours:

1. **Request envelope schema** — JSON the listener enqueues to Cloud Tasks. For Social: `platform: "social"`, `contract_version`, `request_id`, `sheet_url`, `account_id`, `campaign_id`, `campaign_name`, `payload` (Meta-specific fields). Missing `platform` defaults to `search` for backward compat.
2. **Worker URL + queue name** — `qa-buddy-runs-social` Cloud Tasks queue targets `https://qa-buddy-worker-social-<hash>.run.app/tasks/qa/run`. Listener env var: `QA_CLOUD_TASKS_WORKER_URL_SOCIAL`.
3. **Slack thread posting protocol** — worker posts directly to Slack threads via `SLACK_BOT_TOKEN` + `thread_ts` from the request, same pattern as Search. Worker needs the same shared bot token.

## GCP

- **Project:** `prj-prd-ai-ppc-qa-pkph` (shared between Search and Social)
- **Region:** `us-west1`
- **Service account:** `ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com`
- **Firestore collection (current):** `qa_runs` (single collection; Social split TBD — likely stay single, index by `platform`)
- **Firestore TTL policy** (one-time manual setup; ~24h lag; app already handles expiry at read-time, this is cleanup only):
  - Console: `https://console.cloud.google.com/firestore/databases/-default-/ttl?project=prj-prd-ai-ppc-qa-pkph`
  - Collection group: `qa_runs_pending_confirmations`
  - Timestamp field: `expires_at`

### Cloud Run deploy quirks (will burn you if forgotten)

- **Prod traffic is revision-pinned.** `gcloud run services update --image` alone does NOT shift traffic. Use 2-step: `--no-traffic` then `update-traffic --to-revisions <new>=100`.
- **Promote prod by re-tagging the tested image digest**, not by rebuilding. Keeps test/prod byte-identical.
- **Cloud Build VPC-SC log streaming error is expected** for this project. Verify build success with `gcloud builds describe <id> --region=global --format='value(status)'`, not by log streaming.
- **Trust `/readyz` for rollout health**, not CLI success text.

## Reference paths

- **Social repo (ours, the build):** `/Users/jack.fay/paid-social-qa-buddy/` → `github.com/jackfay2/paid-social-qa-buddy`. Tests: `source .venv/bin/activate && pytest -q` (Python 3.11). Transfer to the Wpromote org before MVP.
- **Search repo (read-only):** `/Users/jack.fay/Paid Social QA Buddy Bot/qa-buddy-bot-main/`
  - `AGENTS.md` — most concentrated guidance file
  - `README.md` — env var matrix + deploy commands
  - `app/listener/slack_parser.py` — where Maya adds the `platform` field
  - `app/checks/registry.py` + `search_checks.py` + `greystar_search_checks.py` — check-registry pattern
  - `app/adapters/google_ads/` — layout to mirror for `app/adapters/polaris/` (client.py + queries/ subfolder)
  - `app/core/contracts.py` — backing-service interface module
- **Original handoff doc** (Maya v1.1 "separate-repo approach") — pasted in conversation history; §3 superseded on repo-topology
- **Teammate's handoff plan** — `/Users/jack.fay/Downloads/paid-social-handoff-plan.md` — supersedes §3 of original on architecture (one listener / split workers)
- **Polaris reference implementation** — `/Users/jack.fay/Downloads/ps-social-daily-health-check/core/recipients.py` (the only file that talks to Polaris in that repo). Discovery flow at `core/discovery.py`. Env contract at `.env.example`.

## Verdict vocabulary (inherited unchanged from Search)

**Pass / Fix / Review / NA / Error.** Review is the safety valve — when in doubt, return Review. Builders have learned what these mean on the Search side. Do not introduce new verdicts.

## Hard rules (from the original handoff §4)

1. NEVER deploy to production without 99.9% confidence. Test GCP + test Slack workspace first.
2. ALWAYS default to Review on uncertainty — especially for Gemini text checks. The Peacock-Olympics incident (Feb 2026: "purchase event" vs expected "purchase," survived 2–3 rounds of manual QA, caught by Meta rep on Sunday) is the canonical reason this exists.
3. NEVER use Gemini for translation or nuanced typo detection. Maya tried this on Search; accuracy unacceptable. Gemini's role is narrow yes/no classification only.
4. Slack ack within 3 seconds. Slack API requirement. Background-thread any real work.
5. Check registry is direct dict lookup, not fuzzy match. Unknown `check_id` → `Error: Unrecognized`, never guessed.
6. Sheets must be shared with the service account. #1 user error. Make the failure mode loud and specific in Slack.
7. `check_id` is the row key, not row index. Row indices shift; `check_id`s don't.
8. Worker idempotency within a job — running it twice on the same sheet must not double-write.
9. Manual-by-design checks return `Review` with instructions, never auto-attempted.
10. NEVER write to a production sheet from a test deployment.

## Gemini usage rules

- One Gemini call per job, batching all text checks
- Default to `Review` on timeout, low confidence, or malformed response — never auto-Pass
- Log full prompt + response to Firestore per job for auditing
- In scope: spellcheck, capitalization rules, promo language detection, fair-housing compliance phrasing
- Out of scope: translation, nuanced typos beyond spellcheck, brand-voice judgment, anything generative

## Open decisions (✗ = resolved)

- ✗ GCP topology → shared listener, split workers
- ✗ Slack bot user → shared (`@qa-buddy`)
- ✗ Repo strategy → separate Social repo (hybrid); listener stays in Maya's repo
- ✗ Meta account routing JSON → not needed; client routing is by BigQuery dataset name (`C<client_id>`), not auth context swap
- ✗ `ad-filtering` branch merge timing → effectively resolved; Maya's canonical envelope shape includes `entity_filter`, indicating the branch is landing in `main`
- ✗ client_id source → worker resolves account_id → client_id via `summary.facebook_ads__account_performance`
- ✗ BigQuery field coverage today → known (~16 of ~37 deterministic; see Data architecture)
- ✗ Pre-existing BQ wrapper → none existed; built fresh
- ✗ Check naming → we own it, lowercase_underscore (Brandon OK'd; Kerri may revisit when back)
- ✗ Slack-vs-Social field naming → `qa_app` ("search"/"social"); listener infers from channel (Maya OK'd, handling greystar/test-channel mapping). **Naming overlap noted (2026-05-28):** Maya's existing envelope already carries a `resolved_platform` field (default `"google_ads"`, used by MCC routing). Whether `qa_app` = extending `resolved_platform`'s values vs. a new dimension is a question to confirm with her before either of us writes envelope code.
- ✗ Cloud Tasks OIDC auth → implemented (`app/api/task_auth.py`, fail-closed; was deferred)
- ✗ Data layer validated end-to-end against live BigQuery (2026-05-27) — correct verdicts
- ✗ Local Sheets auth → user-OAuth Sheets/Drive scopes are blocked on this Google Workspace; live sheets require the service account; local uses the in-memory sheet stand-in
- ✗ Per-client BQ schema variance → confirmed real; `BigQueryMetaClient` uses `SELECT *` (see Data architecture)
- ✗ Objective check value-map calibrated (2026-05-28) — per Brandon: match against ODAX, legacy Meta enums (CONVERSIONS, LEAD_GENERATION, PAGE_LIKES, etc.) map to their modern replacements
- ✗ Test setup architecture → listener-copy in a separate test Slack workspace (see Architecture); production listener stays unified
- ✗ Sheets auth workaround (2026-05-28) — Maya's pattern (per her main `app/adapters/sheets/google_sheets.py` + `app/config.py`): the SA JSON lives in **Secret Manager**, never downloaded as a key file. `QA_SHEETS_AUTH_MODE` ∈ `{service_account, adc, auto}`; `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON_SECRET_NAME` names the secret; `_resolve_secrets()` swaps the JSON into `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` at startup; `gspread.service_account_from_dict(payload)` from there. Mirror this exactly when we wire live sheets.
- Kerri's final `check_id` list — pending (Brandon is interim; template's checks + BQ fields are known, formal IDs TBD)
- BigQuery field timing (Riley + Nikki sprint) — which of the ~16 missing fields land, and when
- Daily-stale vs real-time — Airbyte syncs daily; confirm daily-stale acceptable, or whether direct Meta API needed for fresh-launch cases
- BigQuery auth for the SA — `ppc-qa-buddy@...` needs `roles/bigquery.dataViewer` on `polaris-data-317717` for prod (Jack's personal ADC has read today). Companion grant: `roles/bigquery.jobUser` on `prj-prd-ai-ppc-qa-pkph`. Permission to add the binding likely sits with ai-team@ or the data-warehouse owners; settle ownership with Maya.
- GCP provisioning ownership — who creates the `qa-buddy-runs-social` queue, deploys `qa-buddy-worker-social{,-test}` to Cloud Run, and grants the IAM. Brad said ai-team@ has access to everything; specific person/process TBD.
- "Ad Sets that Ads Should Be Live In" check — structural (ad→adset placement vs builder's expectation, per Brandon); Review-heavy for MVP unless the builder-input format is simple
- ✗ Firestore collection → single `qa_runs` collection with `qa_app` field (FirestoreRunStore tags records `qa_app="social"`)

## Testing & workflow conventions

- Social repo: `source .venv/bin/activate && pytest -q` (163 tests). The venv must be Python 3.11 (Homebrew at `/Users/jack.fay/homebrew`), not the system 3.9.
- New shells need Homebrew on PATH: `eval "$(/Users/jack.fay/homebrew/bin/brew shellenv)"` (added to `~/.zshrc`).
- Local worker runs: `QA_CLOUD_TASKS_AUTH_REQUIRED=false` (OIDC verification is implemented but unwanted locally), `QA_RUN_STORE_BACKEND=memory`. Live Sheets via ADC is blocked (scopes); use `scripts/local_qa_run.py` (in-memory sheet) or the service account for real sheets.
- `scripts/live_check.py` and `scripts/local_qa_run.py` hit live BigQuery (read-only) with your personal ADC — safe, no writes, no other GCP contact.
- Adapter tests mock the external client (gspread, httpx, bigquery, requests, firestore); wiring functions are monkeypatched in endpoint tests.
- Search-repo conventions that matter only when coordinating Maya's listener change: `EntityFilter` is the selection source of truth, dedupe keys include normalized filter intent, `pending_confirmation` is a valid run state.

## Next actions (when resuming work)

Cross-team (have lead time — fire these first):
1. **Maya** — listener change (spec drafted): for `qa_app=social`, skip the 10-digit account_id validation (Meta IDs are ~17 digits), skip MCC routing, route to the `qa-buddy-runs-social` queue + social worker URL. Get her timing.
2. **Brandon/Kerri** — canonical objective values (real data is legacy: CONVERSIONS / LEAD_GENERATION / PAGE_LIKES, plus ODAX `OUTCOME_*`) + whether legacy maps to new; and the MVP must-have check subset.
3. **GCP provisioning owner** — who creates the `qa-buddy-runs-social` queue, deploys `qa-buddy-worker-social{,-test}`, and grants the SA `roles/bigquery.dataViewer` on `polaris-data-317717` + Cloud Tasks invoke.

Solo build (after the above inputs land):
4. Wire Gemini into orchestration (text-check batch from ad evidence + merge); validate against an active ad with real creative.
5. Service-account Sheets auth → validate live sheet read/write (currently in-memory).
6. Expand the check registry (calibrate value-maps with Brandon).
7. Deploy to test, then the full test-channel flow.

## Repo housekeeping

This file lives at the Social repo root (`/Users/jack.fay/paid-social-qa-buddy/CLAUDE.md`) and is committed, so it travels with the code. A stale earlier copy exists at `/Users/jack.fay/Paid Social QA Buddy Bot/CLAUDE.md` (the old working dir) — the repo copy is canonical. Before MVP: transfer the repo from personal `jackfay2` to the Wpromote org so the team has access.
