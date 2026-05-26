# Paid Social QA Buddy Bot — Project Memory

Auto-loaded by Claude Code when working in the `paid-social-qa-buddy` repo. Captures durable project decisions so they don't have to be rederived each session.

## Project status

Maya Gundepudi (Search QA Buddy owner) is handing off the Paid Social extension to Jack Fay. Original MVP target was **June 5, 2026**; that date has passed and the realistic target slipped (single-developer project, dependency chain). Phase 1 is Meta only. Meta data comes from BigQuery (Airbyte-synced daily from the Meta Marketing API), not directly from Meta. Phases 2+ add TikTok, Snap, Reddit, Pinterest, LinkedIn using the same architecture with new connectors and registries.

**Build status: vertical slice complete (mocked).** The Social worker repo exists at `github.com/jackfay2/paid-social-qa-buddy` (local: `/Users/jack.fay/paid-social-qa-buddy`). All five backing-service adapters, the account resolver, pipeline, orchestration service, worker endpoint, and 2 deterministic checks are built and unit-tested (142 tests). Everything is verified against mocks — **not yet run against live data.** See Implementation status below.

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
- **Existing repo (Maya's, hosts the listener):** listener + Search worker stay there. Maya makes the `platform` routing change in her repo.
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

**BigQuery field coverage (schema dig 2026-05-22):** only ~16 of Kerri's ~37 checks have backing fields today.
- `facebook_ads__campaigns`: objective, buying_type present; **daily_budget, bid_strategy NOT** (Riley/Nikki).
- `facebook_ads__adsets` + `facebook_ads__adset_targetings`: name, start_time, effective_status, age_min/max, genders, countries, location_types, excluded_custom_audiences, optimization present; **spend min/max, end_time, placements, promoted_object (pixel/event), attribution NOT.**
- `facebook_ads__ads`: name, effective_status, status, bid_type/amount, denormalized `creative` (title, body, call_to_action_type, object_url) present; **description, site_links, display URL/caption, actor_id, instagram_user_id NOT.**
- Missing-field checks return Review until the columns land. The age_min/age_max template mapping was swapped (Brandon confirmed) — use age_min for Age Min.

## Hard rule: do NOT touch the Search repo

The Search repo lives at `/Users/jack.fay/Paid Social QA Buddy Bot/qa-buddy-bot-main/` (unzipped from Maya's GitHub zip). It is **read-only reference**. No commits, no PRs, no edits. All Social work goes in the Social repo at `/Users/jack.fay/paid-social-qa-buddy`.

## Implementation status (Social repo)

Built and unit-tested (142 tests, all mocked — NOT yet run against live data). Each adapter sits behind a Protocol in `app/core/contracts.py` (12-factor IV).

- **Adapters:**
  - `app/adapters/bigquery/` — `BigQueryMetaClient` (campaign/adset/ad fetch, ID validation, per-job cache) + `BigQueryAccountResolver` (account_id → client_id)
  - `app/adapters/polaris/` — `PolarisClient` (DRF pagination, `Token` auth, recipient resolution)
  - `app/adapters/slack/` — `SlackClient` (chat.postMessage, `Bearer` auth, transient/terminal error classification)
  - `app/adapters/storage/` — `InMemoryRunStore` + `FirestoreRunStore` (run lifecycle, notification dedup, tags records `qa_app="social"`)
  - `app/adapters/sheets/` — `GoogleSheetsClient` (alias-based header detection, batched writes, specific "not shared with SA" error) + pure `parser.py`
- **Core:** `app/core/pipeline.py` (execute_checks, build_summary), `app/core/orchestration.py` (`SocialQAOrchestrationService` — full flow; every failure path returns a terminal result with a clear message)
- **Endpoint:** `app/api/server.py` `/tasks/qa/run` (parse → timeout-guarded run → Slack post with dedup), `app/api/wiring.py` (adapter assembly, monkeypatch-able for tests), `app/api/models.py`
- **Checks:** `app/checks/meta_checks.py` — `campaign_objective`, `campaign_buying_type` (Meta-enum normalization so `Traffic` == `OUTCOME_TRAFFIC`; ambiguity → Review). Registered in `app/checks/registry.py`.

**Not yet done (rough priority order):**
1. Run against live data — everything is mocked; highest-value next validation
2. Cloud Tasks OIDC auth verification (deferred; endpoint refuses when auth required-but-unimplemented; local sets `QA_CLOUD_TASKS_AUTH_REQUIRED=false`)
3. GCP provisioning (`qa-buddy-runs-social` queue, `qa-buddy-worker-social{,-test}` Cloud Run, IAM incl. BQ read on `polaris-data-317717`)
4. Maya's listener change (route `qa_app=social`; relax the 10-digit account_id validation so Meta account IDs aren't rejected)
5. Gemini text-check adapter (none built yet)
6. Expand the check registry past the 2 starter checks

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
- ✗ Slack-vs-Social field naming → `qa_app` ("search"/"social"); listener infers from channel (Maya OK'd, handling greystar/test-channel mapping)
- Kerri's final `check_id` list — pending (Brandon is interim; template's checks + BQ fields are known, formal IDs TBD)
- BigQuery field timing (Riley + Nikki sprint) — which of the ~16 missing fields land, and when
- Daily-stale vs real-time — Airbyte syncs daily; confirm daily-stale acceptable, or whether direct Meta API needed for fresh-launch cases
- BigQuery auth for the SA — `ppc-qa-buddy@...` needs `roles/bigquery.dataViewer` on `polaris-data-317717` for prod (Jack's personal ADC has read today)
- "Ad Sets that Ads Should Be Live In" check — structural (ad→adset placement vs builder's expectation, per Brandon); Review-heavy for MVP unless the builder-input format is simple
- ✗ Firestore collection → single `qa_runs` collection with `qa_app` field (FirestoreRunStore tags records `qa_app="social"`)

## Testing & workflow conventions

- Social repo: `source .venv/bin/activate && pytest -q` (142 tests). The venv must be Python 3.11 (Homebrew at `/Users/jack.fay/homebrew`), not the system 3.9.
- New shells need Homebrew on PATH: `eval "$(/Users/jack.fay/homebrew/bin/brew shellenv)"` (added to `~/.zshrc`).
- Local worker runs: `QA_CLOUD_TASKS_AUTH_REQUIRED=false` (OIDC not implemented), `QA_RUN_STORE_BACKEND=memory`, sheets via ADC (`gcloud auth application-default login`).
- Adapter tests mock the external client (gspread, httpx, bigquery, requests, firestore); wiring functions are monkeypatched in endpoint tests.
- Search-repo conventions that matter only when coordinating Maya's listener change: `EntityFilter` is the selection source of truth, dedupe keys include normalized filter intent, `pending_confirmation` is a valid run state.

## Next actions (when resuming work)

1. **Run the vertical slice against live data** (highest value): a real Peacock account/campaign with BQ data + a test QA sheet shared with Jack's Google account, with check_ids `campaign_objective` and `campaign_buying_type` in column A plus expected values. Run the worker locally, POST a task payload, watch real verdicts. Surfaces integration bugs mocks can't (real schemas, real enum values, real sheet I/O).
2. Implement Cloud Tasks OIDC auth verification (pre-deploy gate).
3. Provision GCP: `qa-buddy-runs-social` queue, `qa-buddy-worker-social{,-test}` Cloud Run, IAM (BQ read for the SA, Cloud Tasks invoke).
4. Maya's listener change: route `qa_app=social`, relax the 10-digit account_id validation for Meta IDs.
5. Expand the check registry as Kerri locks check_ids and Riley/Nikki land BQ fields.
6. Build the Gemini text-check adapter (spellcheck / headline / description rows) — batched, Review-on-uncertainty.

## Repo housekeeping

This file lives at the Social repo root (`/Users/jack.fay/paid-social-qa-buddy/CLAUDE.md`) and is committed, so it travels with the code. A stale earlier copy exists at `/Users/jack.fay/Paid Social QA Buddy Bot/CLAUDE.md` (the old working dir) — the repo copy is canonical. Before MVP: transfer the repo from personal `jackfay2` to the Wpromote org so the team has access.
