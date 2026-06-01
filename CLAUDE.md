# Paid Social QA Buddy Bot — Project Memory

Auto-loaded by Claude Code when working in the `paid-social-qa-buddy` repo. Captures durable project decisions so they don't have to be rederived each session.

## Project status

Maya Gundepudi (Search QA Buddy owner) is handing off the Paid Social extension to Jack Fay. Original MVP target was **June 5, 2026**; that date has passed and the realistic target slipped (single-developer project, dependency chain). Phase 1 is Meta only. Meta data comes from BigQuery (Airbyte-synced daily from the Meta Marketing API), not directly from Meta. Phases 2+ add TikTok, Snap, Reddit, Pinterest, LinkedIn using the same architecture with new connectors and registries.

**Build status: vertical slice built, data layer validated against live BigQuery, Gemini wired through orchestration, Sheets auth path matches Maya's prod pattern.** Repo at `github.com/jackfay2/paid-social-qa-buddy` (local: `/Users/jack.fay/paid-social-qa-buddy`). Built + unit-tested (339 tests, CI green on push): five backing-service adapters + a new Secret Manager adapter, account resolver, Gemini text-check adapter wired through orchestration (single batched call per job, per-ad evaluation with row-level aggregation, defaults to Review on any uncertainty), pipeline, orchestration, worker endpoint, Cloud Tasks OIDC auth, 5 campaign-level deterministic checks, 10 ad-set-level deterministic checks (status / start_date / end_date / age_min / age_max / genders / countries / conversion_event / attribution_setting / optimization_goal — targeting reads handle nested-vs-flat schema variance; **conversion_event is the Peacock-Olympics check**: strict event match, Review on any ambiguity, never a silent Pass on a near-match like "purchase event" vs "purchase"; attribution_setting parses Meta's `attribution_spec` list shape confirmed against live BQ), 4 ad-level deterministic checks (status / count / destination_url / call_to_action — URL normalizer deliberately strict: any whitespace or dot-less host returns Review; CTA value-matches the 18 dropdown labels), and 3 Gemini spelling text checks now defined (ad copy / headline / description) and routed through the batched text-check path. The **full worker has been run end-to-end against real BigQuery** (resolver + fetch + orchestration + checks → correct verdicts, 2026-05-27), and a **real QA summary was posted to the test Slack workspace** (2026-05-28, `scripts/slack_smoke.py`, token pulled live from Secret Manager). **Sheets auth is solved**: prod uses ADC as the attached SA `ppc-qa-buddy@…` (NOT a JSON key — no such secret exists; see Open decisions); locally we impersonate that SA (`scripts/sheet_run.py`) so reads/writes work without a key file. The real QA template has been snapshotted into `data/` (it is read-only to us — see Template handling model). See Implementation status below.

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
- **Ad `creative` is a nested RECORD (live finding, 2026-05-29, C61854560).** On the ads table, `creative` is a dict with keys incl. `body, title, name, call_to_action_type, link_url, object_story_spec, image_url, asset_feed_spec, …`. Confirmed: `creative.call_to_action_type` (validates `ad_call_to_action`) and `creative.link_url` (validates `ad_destination_url`). **Copy lives in flat `creative.body` / `creative.title`** for this client, NOT `object_story_spec.link_data.*` (which was empty) — so the Gemini spelling checks now carry flat fallbacks (`creative.body`, `creative.title`) via `TextCheckDefinition.fallback_fields` + `resolve_ad_text`.
- **`url_tags` is absent** from this client's creative record → `ad_utm_parameters` has no backing field here; deferred (don't guess the path).
- **`promoted_object` is NOT a column** on `C61854560`'s adsets table → `adset_conversion_event` will Review (safe) for this client. The check is still correct but **unvalidated against a client that actually has `promoted_object.custom_event_type`** — verify against one before trusting its Fix path.
- Template fix (Brandon confirmed): the age_min/age_max mapping was swapped — use age_min for Age Min.
- **Real objective values are legacy** (CONVERSIONS, LEAD_GENERATION, PAGE_LIKES), not the new ODAX `OUTCOME_*`. The `campaign_objective` check's value-map needs calibration with Brandon to cover both (it currently only coincidentally handles CONVERSIONS).

## Hard rule: do NOT touch the Search repo

The Search repo lives at `/Users/jack.fay/Paid Social QA Buddy Bot/qa-buddy-bot-main/` (unzipped from Maya's GitHub zip). It is **read-only reference**. No commits, no PRs, no edits. All Social work goes in the Social repo at `/Users/jack.fay/paid-social-qa-buddy`.

## Implementation status (Social repo)

Built and unit-tested (339 tests; CI runs them on push). The data layer + full orchestration have been validated against live BigQuery. Each adapter sits behind a Protocol in `app/core/contracts.py` (12-factor IV).

- **Adapters:**
  - `app/adapters/bigquery/` — `BigQueryMetaClient` (campaign/adset/ad fetch via `SELECT *`, ID validation, per-job cache) + `BigQueryAccountResolver` (account_id → client_id)
  - `app/adapters/polaris/` — `PolarisClient` (DRF pagination, `Token` auth, recipient resolution)
  - `app/adapters/slack/` — `SlackClient` (chat.postMessage, `Bearer` auth, transient/terminal error classification)
  - `app/adapters/storage/` — `InMemoryRunStore` + `FirestoreRunStore` (run lifecycle, notification dedup, tags records `qa_app="social"`)
  - `app/adapters/sheets/` — `GoogleSheetsClient` (alias-based header detection, batched writes, specific "not shared with SA" error, three auth modes: `service_account` / `adc` / `auto` — auto silently falls back to ADC when SA isn't configured or fails) + pure `parser.py`
  - `app/adapters/secrets/` — `SecretManagerService` (single-secret fetch with structured `SecretResolutionError`; `_resolve_secrets()` in `app/config.py` populates plaintext settings fields from `*_SECRET_NAME` indirection at startup; explicit env always wins)
  - `app/adapters/gemini/` — `GeminiClient` (batched text checks via Gemini REST/httpx, confidence threshold, Review on any failure) + `StubGeminiClient`. Wired into orchestration via `app/api/wiring.py:build_gemini_client` (stub when no API key).
- **Core:** `app/core/pipeline.py` (execute_checks, build_summary), `app/core/orchestration.py` (`SocialQAOrchestrationService` — full flow; every failure path returns a terminal result with a clear message)
- **Endpoint:** `app/api/server.py` `/tasks/qa/run` (parse → auth → timeout-guarded run → Slack post with dedup), `app/api/wiring.py` (adapter assembly, monkeypatch-able), `app/api/models.py`, `app/api/task_auth.py` (Cloud Tasks OIDC verification — implemented, fail-closed). `/readyz` is **fail-closed**: it calls `diagnostics_from_settings()` and returns **503** when any Secret Manager indirection failed to resolve (or settings load raises), so Cloud Run never routes traffic to a revision with a bad secret name / missing `secretAccessor` IAM. Response carries secret *names* + error codes only, never resolved values. `/healthz` stays a trivial liveness probe.
- **Checks:** `app/checks/meta_checks.py` — campaign-level (`campaign_objective`, `campaign_buying_type`, `campaign_status`, `campaign_start_date`, `campaign_bid_strategy`) + ad-set-level (`adset_status`, `adset_start_date`, `adset_end_date`, `adset_age_min`, `adset_age_max`, `adset_genders`, `adset_countries`) + ad-level (`ad_status`, `ad_count`, `ad_destination_url`). Targeting fields read via `app/checks/_targeting.py` (`read_targeting`) so nested-RECORD vs flat-column schemas both work. All registered in `app/checks/registry.py`. Multi-entity semantics (used by all per-adset and per-ad checks): builder input is "every entity must match this expectation"; any divergence → Fix pointing at the first mismatched entity with `(+N more)` suffix. Aggregate checks (e.g. `ad_count`) compare a single derived value. Ad destination URL reads defensively across several BQ schema paths (`link_url`, `destination_url`, `creative.link_url`, `creative.object_story_spec.link_data.link`) and uses a deliberately strict normalizer.
- **Text checks (Gemini):** `app/checks/text_checks.py` — `TextCheckDefinition` (check_id + instruction + ad_field dot-path) + `TEXT_CHECK_DEFINITIONS` registry (starts EMPTY by design — Brandon owns the canonical text-check set; adding entries needs no wiring change). `app/core/pipeline.py:execute_text_checks` builds one batched Gemini call across all text-check rows × all ads with populated text, then aggregates per-row by the same rule as ad-set checks (any ad Fix → row Fix; any Review → row Review; all Pass → row Pass; no ad text → Review). Orchestration calls it after deterministic checks when a `gemini_client` is wired; `None` (test default) skips the path cleanly.
- **Dev scripts:** `scripts/live_check.py` (live BQ resolver+fetch smoke), `scripts/local_qa_run.py` (full orchestration vs live BQ, in-memory sheet), `scripts/setup_test_sheet.py` (creates a test sheet via your ADC), `scripts/slack_smoke.py` (live BQ → checks → **real Slack post into the test workspace**; bot token from `SLACK_BOT_TOKEN` env only; `--channel` required, `--thread-ts` optional, `--dry-run` to build the message without posting), `scripts/sheet_run.py` (**the real-sheet runner**: impersonates `ppc-qa-buddy@…` for Sheets auth, reads a real QA sheet, runs checks vs live BQ, and WRITES verdicts back into the Pass-or-Fix/Action columns; `--sheet-url` required, `--account-id/--campaign-id` to pin a campaign, `--worksheet`, `--dry-run` to read+compute without writing, `--channel` to also post to Slack). The sheet must be shared with the SA email as Editor. This is the partial-E2E path that proves the worker→Sheets→Slack legs before GCP provisioning lands — bypasses only the Slack-mention trigger + Cloud Tasks queue.
- **CI:** `.github/workflows/ci.yml` runs pytest on Python 3.11 on push/PR.

**Not yet done (rough priority order):**
1. Populate `TEXT_CHECK_DEFINITIONS` from Brandon — wiring is in place but the registry is empty by design. Each entry is `{check_id, instruction, ad_field}`; validate the first one against an active ad with populated creative before trusting Fix verdicts.
2. **Live Sheets validation** — adapter code matches Maya's pattern; gated on (a) the SA JSON landing in a Secret Manager secret, (b) the test sheet being shared with `ppc-qa-buddy@…`, (c) `QA_USE_SECRET_MANAGER=true` + `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON_SECRET_NAME=<secret>` wired into the deployed worker. The local in-memory stand-in in `local_qa_run.py` still covers the orchestration path without those.
3. Maya's listener change (route `qa_app=social`; relax the 10-digit account_id validation so Meta IDs aren't rejected).
4. GCP provisioning (`qa-buddy-runs-social` queue, `qa-buddy-worker-social{,-test}` Cloud Run, IAM). **Turnkey runbook + scripts now in `deploy/`** (`README.md` + `create_social_queue.sh` + `grant_iam.sh` + `build_and_deploy_worker.sh`; all guarded behind `CONFIRM=1`, dry-run by default). Grounded against live project state 2026-05-29: only `qa-buddy-runs` exists (need `qa-buddy-runs-social{,-test}`); AR repo `cloud-run-source-deploy` exists; the SA already has `bigquery.dataViewer`@`polaris-data-317717` + `cloudtasks.enqueuer` + `datastore.user` + `secretmanager.secretAccessor`. **IAM delta needed: `bigquery.jobUser` on `prj-prd-ai-ppc-qa-pkph`** (run BQ jobs) + `run.invoker` on the new worker service. Worker uses `--set-secrets` for `SLACK_BOT_TOKEN`/`GEMINI_API_KEY` and `QA_SHEETS_AUTH_MODE=adc` (sheets shared with the SA; no JSON). Deployer's own access is inherited (not project-level visible) — confirm before running.
5. Expand the check registry (Kerri locks check_ids; Riley/Nikki land fields; calibrate value-maps with Brandon).
6. Deploy + the full test-channel flow (gated on the above).

**Partial-E2E milestone (2026-05-28):** `scripts/slack_smoke.py` runs the real orchestration (live BQ → real checks → real verdicts) and posts the summary to a real channel in the test Slack workspace. Dry-run confirmed the BQ + checks + message legs work end-to-end (discovered campaign `6276091730756`, client `C61854560`, `CONVERSIONS` objective → 2 Pass). The only unproven leg is the live `chat.postMessage`, which needs: (a) the test-workspace bot token in `SLACK_BOT_TOKEN`, (b) a channel ID with the bot invited, (c) the bot having `chat:write` scope. This is NOT the full Slack-triggered flow — the @-mention listener + Cloud Tasks queue legs remain blocked on GCP provisioning + Maya's listener change.

## Deployed test worker (2026-05-29)

The Social **test** path is live on Cloud Run — first real deploy:
- **Service:** `qa-buddy-worker-social-test` (us-west1), private (`--no-allow-unauthenticated`), runs as `ppc-qa-buddy@`. URL `https://qa-buddy-worker-social-test-637315940254.us-west1.run.app`.
- **Image:** `us-west1-docker.pkg.dev/.../cloud-run-source-deploy/qa-buddy-worker-social:test-20260529-145027` (Cloud Build, our repo's Dockerfile).
- **Queue:** `qa-buddy-runs-social-test` (mirrors `qa-buddy-runs`).
- **IAM (SA):** `bigquery.jobUser` + `bigquery.dataViewer`@polaris-data-317717 + `datastore.user` + `secretmanager.secretAccessor` + `cloudtasks.enqueuer` + `run.invoker`@the service.
- **Config:** secrets via Cloud Run `--set-secrets` (`SLACK_BOT_TOKEN`←`test-slack-bot-token`, `GEMINI_API_KEY`←`gemini-api-key`); `QA_SHEETS_AUTH_MODE=adc`; `QA_CLOUD_TASKS_AUTH_REQUIRED=true`; OIDC audience = service URL. `/readyz` → 200, no secret errors.
- **Health check:** `TOKEN=$(gcloud auth print-identity-token --impersonate-service-account=ppc-qa-buddy@... --audiences=<URL>); curl -H "Authorization: Bearer $TOKEN" <URL>/readyz`.
- All `-social`-named + isolated; Maya's services untouched. Full deploy steps + scripts in `deploy/`.
- **Step 4 attempted (2026-05-29):** hand-enqueued a real Cloud Task (queue → OIDC → worker). **OIDC + Cloud Run + the endpoint all worked (`POST /tasks/qa/run` → 200)**, but the run returned `error_code=account_resolution_failed`. **Root cause (real bug, found):** `BigQueryAccountResolver`/`BigQueryMetaClient` do `bigquery.Client(project=config.project)` where `config.project=polaris-data-317717` — so the query JOB runs *in the data-warehouse project*, where the SA has `dataViewer` but **not `jobUser`**. `project` is conflated: it's both the job/billing project AND the table-name namespace. **Fix (option B, the right one):** add a `billing_project` (= `gcp_project_id` / our project) for the `bigquery.Client(...)`, keep `config.project` for fully-qualified table names. Then jobs bill to `prj-prd-ai-ppc-qa-pkph` (SA has `jobUser` there ✓) and read `polaris-data-317717` (`dataViewer` ✓). Option A (grant SA `jobUser` on `polaris-data-317717`) is faster but touches the data-warehouse project's IAM + bills jobs there — avoid. Locally it works today only because Jack's ADC has access on `polaris-data-317717`.
- ✅ **REAL TEST DONE (2026-05-29) via the local worker path** (`sheet_run.py`, SA impersonation which carries the BQ + Sheets scopes): campaign `6065738140956` (75 ad sets) → read 12 check rows → live BQ → **Pass 5 | Fix 5 | Review 2**, verdicts WRITTEN to the test sheet (`1b8hp0…c8`) + summary POSTED to `#social-qa-buddy-testing`. Same deliverable as Maya's flow. Fixes were real (mismatched dates, `Men` vs `All`, empty country targeting); Reviews correct (empty `bid_strategy`; no conversion event → Peacock safeguard).
- ✅✅ **DEPLOYED-WORKER E2E PROVEN (2026-05-29).** After two fixes (below), a hand-enqueued Cloud Task ran the FULL real backend: Slack ack → Cloud Task → `qa-buddy-runs-social-test` → OIDC → Cloud Run worker → live BigQuery (75 ad sets) → 19-check registry → verdicts written to sheet → **`QA completed … Pass 5 | Fix 5 | Review 2`** posted to the `#social-qa-buddy-testing` thread (deploy-test-3). Identical to Maya's flow; only difference vs a true `@-mention` is the listener (I hand-enqueued the task).
  - **Fix 1 (code):** BQ jobs were billing to the data-warehouse project (no `jobUser`); split `billing_project` (= our project) from data `project`. Redeployed (`test-20260529-155450`, revision 00003-68d).
  - **Fix 2 (infra):** BigQuery API wasn't enabled in `prj-prd-ai-ppc-qa-pkph` (Maya's worker uses Google Ads, not BQ — we're the first BQ consumer). `gcloud services enable bigquery.googleapis.com`. Additive; Maya unaffected.
- **For the true `@-mention`:** only Gate 3 remains — Maya's listener routing (`qa_app=social` → `qa-buddy-runs-social[-test]` → our worker URL with OIDC). Every other leg is proven on real infra.
- **To re-run the deployed worker manually:** post a Slack ack to get a `thread_ts`, then `gcloud tasks create-http-task --queue=qa-buddy-runs-social-test --url=<worker>/tasks/qa/run --oidc-service-account-email=ppc-qa-buddy@… --oidc-token-audience=<worker> --body-content='{envelope}'`. Worker URL: `https://qa-buddy-worker-social-test-637315940254.us-west1.run.app`.

## Deployed test listener (2026-06-01)

The merged search+social listener is deployed (idle until the Events-URL repoint):
- **Service:** `qa-buddy-listener-social-test` (us-west1), **public** (`--allow-unauthenticated`, like any Slack webhook — protected at the app layer by signing-secret verification), runs as `ppc-qa-buddy@`. URL `https://qa-buddy-listener-social-test-637315940254.us-west1.run.app`.
- **Events endpoint:** `POST /slack/events`. Verified: `/readyz`→200 (correct social config), unsigned `/slack/events`→401 (signing enforced). (`/healthz` returns a Google-edge 404 — a GFE reserved-path quirk, not our app; harmless.)
- **Secrets:** `SLACK_SIGNING_SECRET`←`test-slack-signing-secret`, `SLACK_BOT_TOKEN`←`test-slack-bot-token`.
- **Routing:** `SOCIAL_CHANNEL_IDS=C0B6ASW9R9V` → social queue → our worker. `SEARCH_WORKER_URL` deliberately **unset** so it can't reach Maya's workers (Search path inert until configured).
- **Idle:** the test Slack app's Events URL still points at Maya's listener; nothing flows here until the repoint (Gate 3).
- **⚠️ Repoint coordination:** when Maya repoints the Events URL here, *Search* @-mentions in the test workspace would also hit this listener → empty `SEARCH_WORKER_URL` → enqueue fails. So either repoint only while she's not Search-testing, OR set `SEARCH_WORKER_URL` (+ audience) to her *test* worker so Search keeps working through our copy (faithful merged-listener behavior; her test worker, not prod). Her call.

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
- **Secret Manager secrets** (confirmed present 2026-05-28, Jack's ADC has list + version-access on this project):
  - `slack-bot-token`, `slack-signing-secret` — prod Slack workspace
  - `test-slack-bot-token`, `test-slack-signing-secret` — **test** Slack workspace (use these for the test worker + `slack_smoke.py`)
  - `gemini-api-key`, `google-ads-*`, `qa-mcc-routes-json-{prod,test}` — Search-side, FYI
  - Pull a value without leaking it into logs: `SLACK_BOT_TOKEN=$(gcloud secrets versions access latest --secret=test-slack-bot-token --project=prj-prd-ai-ppc-qa-pkph) python scripts/slack_smoke.py --channel C…` (inline env var, never echoed).
- **Test Slack bot:** bot user ID `U0B3EJ7PZ5Z` (Maya, 2026-05-28) — used by the listener to detect the `@qa-buddy` mention; not needed by the worker's outbound post.
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

## Brief targets & scope (Maya's Project Brief, May 2026)

Durable facts from the brief — keep in mind across the project:
- **Success bar:** bot agrees with a human reviewer on Pass/Fix **100%** on a labeled test set. **Coverage target: 91%** of checklist items deterministic (Pass/Fix/N/A); ~9% surface as Review. Adoption: >50% of Meta QAs within a month of pilot.
- **Runtime:** <2 min typical, <5 min most, **hard stop at 12 min** — `QA_WORKER_MAX_RUNTIME_SECONDS` default is now **720s** (aligned 2026-05-29). Slack ack <3s, processing start <30s, 99.5% availability.
- **Every fixed-value field has dropdown validation** (the MASTER DATA VALIDATION tab). Validates the value-map calibration approach: for fixed-value fields, value-match against the dropdown value. Yes/No fields are the *non*-fixed-value ones.
- **Gemini scope (text only):** spellcheck, capitalization, pricing language, promotional copy in headlines, **fair-housing compliance**. Narrow yes/no, never generative; uncertainty→Review; track Gemini-flagged checks separately to measure error rate before rollout. ~$0.01/job.
- **Always ≥1 manual check** (the `download_changes` equivalent) → Review with instructions. **Implemented (2026-05-29):** `download_changes` + `ad_creative_dimensions` in `pipeline.ALWAYS_REVIEW_CHECK_ACTIONS` (manual gate runs before the blank/N-A gate, so they surface even with empty builder input). The Slack summary now **mirrors Maya's Search-bot format** (verified against her test runs 2026-05-29): `QA completed for {name} (account_id=…, campaign_id=…)` / `Summary: Pass…|Fix…|Review…|N/A…|Error…` / `Sheet: {url}` / `request_id: {id}`, with a surfaced `Fixes:` block (our brief-backed addition; Maya's shows counts only). Built in `_build_summary_message`.
- **Final QA sign-off stays with humans;** bot is first-pass, never sole approver. Gemini outputs are advisory, with reasoning logged.
- ⚠️ **The brief predates the BigQuery decision.** It says actuals come from "Meta Marketing API or Polaris," and its Architecture section still has Search/Google-Ads leftovers (MCC OAuth2, GAQL queries, campaign-name lookup). The implemented + validated reality is **BigQuery** (Airbyte from the Meta API). Treat the brief's data-source/arch wording as aspirational; BigQuery is canonical.

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
- ✗ Slack-vs-Social field naming → `qa_app`, **defaulted to `search`** (Maya re-confirmed directly 2026-05-28: "we already decided to do the qa_app and its defaulted to search"). It is a **separate field** from her existing `resolved_platform` (default `"google_ads"`, used by MCC routing) — keeping them distinct means Social routing doesn't trip her Google Ads MCC branches. Listener stamps `qa_app="social"` on Social requests; our worker reads that. Settled — safe to write envelope code against it.
- ✗ Cloud Tasks OIDC auth → implemented (`app/api/task_auth.py`, fail-closed; was deferred)
- ✗ Data layer validated end-to-end against live BigQuery (2026-05-27) — correct verdicts
- ✗ Today's new ad-set conversion-config checks validated against LIVE BigQuery (2026-05-29) — campaign `6065738140956` (C61854560, 75 ad sets; real values: `optimization_goal=PAGE_LIKES`, `attribution_spec=[{CLICK_THROUGH,1}]`, no `custom_event_type`). Results: `optimization_goal`→Fix (Conversions vs PAGE_LIKES), `attribution_setting`→Fix (parses the real list-of-dicts shape), `conversion_event`→Review (engagement campaign has no conversion event → the Peacock check correctly escalates instead of guessing). Confirms the parsers handle the real `attribution_spec` array + `promoted_object` shapes, not just synthetic test data. Note: `conversion_event` will Review on non-conversion (engagement/awareness/traffic) campaigns — expected and correct.
- ✗ Sheets auth — SOLVED (2026-05-28). **Prod pattern is ADC with the attached SA, NOT a JSON key.** Confirmed: Maya's `google_sheets.py` `adc` mode calls `google.auth.default(scopes=[spreadsheets, drive])` + `gspread.authorize()`; `.env.example` defaults `QA_SHEETS_AUTH_MODE=adc`; and there is **no** `google-sheets-service-account-json` secret in Secret Manager (only `google-oauth2-ppc-qa-buddy-*` for the Ads API). So the deployed worker authenticates to Sheets as `ppc-qa-buddy@…` and **sheets must be shared with that SA email**. The earlier "SA JSON in Secret Manager" note was wrong — that path exists in code (`service_account` mode) but isn't how prod runs. **Local testing**: Jack's personal ADC can't do Sheets (Workspace blocks the user OAuth scopes), but he has `serviceAccountTokenCreator` on the SA, so `scripts/sheet_run.py` impersonates `ppc-qa-buddy@…` (`google.auth.impersonated_credentials` → `gspread.authorize`) — same identity as prod, no key file. Inject the authorized gspread into `GoogleSheetsClient(gspread_client=…)`.
- ✗ Per-client BQ schema variance → confirmed real; `BigQueryMetaClient` uses `SELECT *` (see Data architecture)
- ✗ Objective check value-map calibrated (2026-05-28) — per Brandon: match against ODAX, legacy Meta enums (CONVERSIONS, LEAD_GENERATION, PAGE_LIKES, etc.) map to their modern replacements
- ✗ Test setup architecture → listener-copy in a separate test Slack workspace (see Architecture); production listener stays unified
- ✗ Test Cloud Run deploy ownership (2026-05-28) — us. Jack will need deploy IAM on `prj-prd-ai-ppc-qa-pkph` to push the test listener + social worker test service.
- ✗ Test Slack app (2026-05-28) — already configured in the test workspace Maya added us to; `SLACK_BOT_TOKEN` available once she shares it.
- ✗ Listener changes — us (2026-05-28). We make the three deltas in a copy of her listener, deploy to our own test Cloud Run, validate end-to-end in the test workspace, then hand Maya a verified PR for prod merge. Message sent to her 13:21 with 4 open mechanical questions (naming, copy location, sync strategy, test bot token).
- ✗ Sheets auth workaround (2026-05-28) — Maya's pattern (per her main `app/adapters/sheets/google_sheets.py` + `app/config.py`): the SA JSON lives in **Secret Manager**, never downloaded as a key file. `QA_SHEETS_AUTH_MODE` ∈ `{service_account, adc, auto}`; `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON_SECRET_NAME` names the secret; `_resolve_secrets()` swaps the JSON into `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` at startup; `gspread.service_account_from_dict(payload)` from there. Mirror this exactly when we wire live sheets.
- Kerri's final `check_id` list — pending (Brandon is interim). **Full template→check_id mapping now drafted: `docs/meta_qa_template_mapping.md`** (every template row → proposed check_id + status: 11 BUILT, ~15 to build, 3 Gemini, 2-3 MANUAL, 1 skip). Confirmed our parser is a faithful match of Maya's (keys on `Check_ID` col A, skips empty, same `lowercase_underscore` style; col B is reference-only). The Meta template's col A is blank in the draft — populating it is the template-completion task.
- ✗ **Yes/No vs value-match — ANSWERED by Brandon (2026-06-01).** The "Yes/No" rows are NOT uniform: (a) **Location** is a *value-match* vs a builder-provided location → that's `adset_countries` (already built). (b) **Spend min/max, interests, exclusions** are **bidirectional presence checks**: builder "Yes" → must be PRESENT (Fix if absent); but **even on No/blank, still verify it isn't there** — to catch a setting "accidentally included" (→ Review, since it may be intentional). Built 2026-06-01: `adset_spend_minimum`, `adset_spend_maximum`, `adset_audiences`, `adset_audience_exclusions` (in `ALWAYS_RUN_CHECK_IDS` so blank input doesn't skip them). (c) **Naming conventions** → standardized but manipulated per-manager/per-client → **MANUAL/Review, pending Kerri**. (d) **check_id list** → Brandon is looking at Kerri's canonical copy and doesn't see our proposed IDs; finalizing IDs in that copy is **gated on Kerri's return**.
- ✗ **Template handling model (2026-05-29)** — confirmed against Maya's repo. The Meta sheet (`docs.google.com/.../12CMnQyqwgmKwGaujE5Hu64sswvlDfFT5Cs-JEJNtF4Y`, "Meta QA" tab) is the **source-of-truth template**, analog of Maya's "Finalized Campaign Template" (`data/new_search_export.csv`). **We never write into it.** `Check_ID` (col A) is pre-populated as part of *finalizing* the template (blank today = not finalized); the bot does NOT write check_ids at runtime — it writes only verdicts (Pass-or-Fix / Action / QA initial) into a per-request COPY a builder filled in. Snapshotted both tabs into our repo: `data/meta_qa_template_export.csv` + `data/meta_master_data_validation_export.csv`. Testing uses offline fixtures / our own copies, never the master.
- ✗ **MASTER DATA VALIDATION tab = builder-input value source of truth (2026-05-29).** The template's 2nd tab lists the canonical dropdown values per field (objective, bid strategy, buying type, CTA ×18, attribution specs, CBO/ABO budget strategy, age, gender). Drives the check value-maps. **17 rows are green-highlighted = confirmed MVP set; 10 already built, 7 to build (4 of them Yes/No).** Full table in `docs/meta_qa_template_mapping.md`.
- ✗ **Value-maps calibrated to the validation tab (2026-05-29)** — fixed two false-Review bugs: `campaign_buying_type` "Reservation"→RESERVED; `campaign_bid_strategy` "Highest volume or value"→LOWEST_COST_WITHOUT_CAP (+ "Cost per result goal"→COST_CAP confirmed). Objective + gender maps already matched. Naming/discovery via the dropdown labels, not guesses.
- BigQuery field timing (Riley + Nikki sprint) — which of the ~16 missing fields land, and when
- Daily-stale vs real-time — Airbyte syncs daily; confirm daily-stale acceptable, or whether direct Meta API needed for fresh-launch cases
- BigQuery auth for the SA — `ppc-qa-buddy@...` needs `roles/bigquery.dataViewer` on `polaris-data-317717` for prod (Jack's personal ADC has read today). Companion grant: `roles/bigquery.jobUser` on `prj-prd-ai-ppc-qa-pkph`. Permission to add the binding likely sits with ai-team@ or the data-warehouse owners; settle ownership with Maya.
- GCP provisioning ownership — who creates the `qa-buddy-runs-social` queue, deploys `qa-buddy-worker-social{,-test}` to Cloud Run, and grants the IAM. Brad said ai-team@ has access to everything; specific person/process TBD.
- "Ad Sets that Ads Should Be Live In" check — structural (ad→adset placement vs builder's expectation, per Brandon); Review-heavy for MVP unless the builder-input format is simple
- ✗ Firestore collection → single `qa_runs` collection with `qa_app` field (FirestoreRunStore tags records `qa_app="social"`)

## Testing & workflow conventions

- Social repo: `source .venv/bin/activate && pytest -q` (339 tests). The venv must be Python 3.11 (Homebrew at `/Users/jack.fay/homebrew`), not the system 3.9.
- New shells need Homebrew on PATH: `eval "$(/Users/jack.fay/homebrew/bin/brew shellenv)"` (added to `~/.zshrc`).
- Local worker runs: `QA_CLOUD_TASKS_AUTH_REQUIRED=false` (OIDC verification is implemented but unwanted locally), `QA_RUN_STORE_BACKEND=memory`. Live Sheets via ADC is blocked (scopes); use `scripts/local_qa_run.py` (in-memory sheet) or the service account for real sheets.
- `scripts/live_check.py` and `scripts/local_qa_run.py` hit live BigQuery (read-only) with your personal ADC — safe, no writes, no other GCP contact.
- Adapter tests mock the external client (gspread, httpx, bigquery, requests, firestore); wiring functions are monkeypatched in endpoint tests.
- Search-repo conventions that matter only when coordinating Maya's listener change: `EntityFilter` is the selection source of truth, dedupe keys include normalized filter intent, `pending_confirmation` is a valid run state.

## Next actions (when resuming work)

**End state as of 2026-05-29 (big day):** the **test worker is deployed and proven E2E on live infra** — a hand-enqueued Cloud Task ran queue → OIDC → Cloud Run worker → live BigQuery → 19 checks → verdicts written to a real sheet + summary posted to `#social-qa-buddy-testing` (`Pass 5 | Fix 5 | Review 2`). GCP test path provisioned (queue, SA IAM incl. `bigquery.jobUser`, Cloud Run, OIDC, BigQuery API enabled). Listener copy started: vendored + social-routing core (`RoutingQAQueue` + `qa_app` envelope) built & tested. 345 worker tests + 6 listener tests green.

**The ONLY blocker to a true `@-mention` is the listener leg.** Everything downstream is proven on real infra.

Next (in order):
1. **Finish the listener copy** (ours, mostly unblocked) — see `listener/README.md`: (a) wire `qa_app` from intake → the `CloudTasksRequest` envelope (parse in `slack_parser` or infer from channel), (b) a focused FastAPI server entrypoint wiring both queues via `RoutingQAQueue`, (c) deploy as `qa-buddy-listener-social-test`.
2. **Test Slack app** (needs Maya/Slack admin) — a separate test Slack app whose Events URL → our test listener (one app = one Events URL, so we can't reuse `@QA Buddy Bot Test`). THE one external dependency for the `@-mention` flow.
3. **Brandon** — the Yes/No-vs-value-match decision (blocks ~4 confirmed checks: spend min/max, naming, audiences, exclusions) + bless the `check_id` list (`docs/meta_qa_template_mapping.md`).
4. **Expand checks** as Brandon answers + as the template `Check_ID` column is finalized.
5. **Prod promotion** — `deploy/build_and_deploy_worker.sh prod` (re-tag tested digest, 2-step traffic) once the test flow is validated end-to-end.

Deployed test worker URL: `https://qa-buddy-worker-social-test-637315940254.us-west1.run.app`. Manual re-run recipe: see the "Deployed test worker" section above.

## Repo housekeeping

This file lives at the Social repo root (`/Users/jack.fay/paid-social-qa-buddy/CLAUDE.md`) and is committed, so it travels with the code. A stale earlier copy exists at `/Users/jack.fay/Paid Social QA Buddy Bot/CLAUDE.md` (the old working dir) — the repo copy is canonical. Before MVP: transfer the repo from personal `jackfay2` to the Wpromote org so the team has access.
