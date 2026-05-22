# Paid Social QA Buddy Bot — Project Memory

Auto-loaded by Claude Code when working in `/Users/jack.fay/Paid Social QA Buddy Bot/`. Captures durable project decisions so they don't have to be rederived each session.

## Project status

Maya Gundepudi (Search QA Buddy owner) is handing off the Paid Social extension to Jack Fay. MVP target: **June 5, 2026**. Phase 1 is Meta only. Meta data comes from BigQuery (Airbyte-synced daily from the Meta Marketing API), not directly from Meta. Phases 2+ add TikTok, Snap, Reddit, Pinterest, LinkedIn using the same architecture with new connectors and registries.

## People

- **Jack Fay** — implementer (user of this Claude Code instance)
- **Maya Gundepudi** — Search QA Buddy owner, handing off Social; owns the existing repo
- **Brad Ash** — head engineer at Wpromote; GCP/architecture authority. Mandated 12-Factor as the design foundation
- **Carrie** — Paid Social team, owns the QA sheet template and final `check_id` list
- **Riley, Nikki** — BigQuery field additions to make missing Meta fields available to Polaris
- **Jason Burma, Anthony Murillo** — security / tech leadership
- **Kerri Lewis, Sami Stoltenberg** — Paid Social team leaders / stakeholder approval
- **ai-team@wpromote.com** — group with access to all GCP/Firestore tooling (Brad granted)

## Architecture (locked)

- **Shared listener, split workers per platform.** Slack physics: one Slack app `@qa-buddy` → one Events API URL → one listener service. Listener reads `platform` field from the parsed message and enqueues to the platform-specific Cloud Tasks queue.
- **Existing repo (Maya's, hosts the listener):** listener + Search worker stay there. Maya makes the `platform` routing change in her repo.
- **New separate Social repo (Jack's, not yet created):** Social worker + BigQuery adapter (Meta data) + Polaris adapter (small, client directory lookup) + check registry + Meta-specific config. Deploys its own Cloud Run worker. Receives tasks from `qa-buddy-runs-social` Cloud Tasks queue.
- **Slack model:** `@-mention` with `key: value` lines (one field per line). Not slash commands. `@qa-buddy` is the shared mention.
- **One shared Slack app** per Brad ("managing too many Slack apps is an overhead nightmare").
- **12-Factor app principles** are mandated by Brad as the foundation. Treat as a hard constraint, not a style guide.

## Data architecture (correct mental model)

The original handoff doc treated Polaris as the Meta data source. It is not. Three distinct systems:

- **Polaris** — Wpromote's internal CRM / service directory at `https://api.polaris.wpromote.com`. Token auth (`Authorization: Token <api_token>` header, NOT Bearer). Tells us who has Paid Social, who the AMs / ADs / managers are, recipient emails. Used for routing and stakeholder notification. Reference implementation: `core/recipients.py` in `ps-social-daily-health-check` (~150 lines, sole Polaris file in that repo).
- **BigQuery (`polaris-data-317717.C<client_id>.facebook_ads__*`)** — the actual Meta data. Airbyte syncs daily from the Meta Marketing API. Campaign settings, ad sets, ads, creative, audiences live here. Service-account auth via GCP ADC. Riley + Nikki's sprint adds columns to these tables.
- **Direct Meta Marketing API** — for fields BigQuery doesn't sync, or sub-daily freshness. Probably out of scope for MVP.

Implication for adapter layout in the new Social repo:

- `app/adapters/polaris/client.py` — small, directory lookup
- `app/adapters/bigquery/meta_client.py` — the real data layer, analog of `app/adapters/google_ads/` in the Search repo

Check functions take `evidence` dicts shaped from BigQuery results.

Per-client routing: each client has its own BigQuery dataset (`C<client_id>`). Routing is "use the right dataset name in the query," not "swap auth contexts." No `MetaAccountRouting` secret needed.

## Hard rule: do NOT touch the Search repo

The Search repo lives at `/Users/jack.fay/Paid Social QA Buddy Bot/qa-buddy-bot-main/` (unzipped from Maya's GitHub zip). It is **read-only reference**. No commits, no PRs, no edits. All Social work goes in the new Social repo (location TBD when Jack creates it).

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
- Carrie's final `check_id` list — pending
- BigQuery field timing (Riley + Nikki sprint) — fields not landed by MVP → checks return `Review`
- BigQuery field coverage today — need schema/manifest of `facebook_ads__*` tables to know which checks are Phase-1 deterministic vs Review-by-design
- Pre-existing Python module wrapping BigQuery Meta queries — unknown; ask before writing fresh
- Daily-stale vs real-time — Airbyte syncs daily; confirm daily-stale is acceptable for QA, or whether direct Meta API is needed for fresh-launch cases
- BigQuery auth — does `ppc-qa-buddy@...` have BQ read on `polaris-data-317717`, or is a new SA needed
- Firestore collection split — likely single collection indexed by `qa_app` field, not yet decided
- Slack-vs-Social field naming in envelope (`qa_app` vs `qa_platform` vs other) — open Slack discussion with Maya

## Testing & workflow conventions

- Always `source .venv/bin/activate` before pytest. Otherwise `pytest: command not found`.
- Smoke set for any listener/queue path change: `tests/test_cloud_tasks_queue.py`, `tests/test_prompt16_smoke_flow.py`, `tests/test_slack_listener.py`.
- When `ad-filtering` lands: `EntityFilter` is the source of truth for selection — never reimplement filter logic inside check modules. Respect `QA_FILTER_ENFORCE_CHECK_LEVELS`. `pending_confirmation` is a valid run state.
- Dedupe keys must include normalized filter intent — top-level campaign/thread alone collapses distinct requests.
- Dataclass payload tests: no `**obj.__dict__` + same-field override (causes duplicate-kwarg `TypeError`). Use `dataclasses.replace`.

## Next actions (when resuming work)

1. Lock the coordination contract with Maya (envelope schema, `qa_app` naming, Social-specific field set). Open Slack discussion in progress.
2. Confirm BigQuery field coverage and auth: which `facebook_ads__*` columns exist today, and whether `ppc-qa-buddy@...` has BQ read on `polaris-data-317717`.
3. Bootstrap the new Social repo (location TBD): pyproject, FastAPI worker skeleton, Cloud Run + Cloud Tasks config, structured JSON logging, env-driven config loader (pydantic-settings or equivalent), SIGTERM handler, backing-service interfaces (`MetaDataClient` backed by BigQuery, `PolarisClient`, `SheetClient`, `SlackClient`, `RunStore`), pytest, CI. 12-Factor compliant from line one.
4. Vertical slice against Peacock: BigQuery Meta client minimum + Polaris client minimum + 1–2 deterministic checks (bid strategy + optimization event are the Peacock-Olympics class) + sheet I/O + Slack thread post + Firestore record.
5. Expand the check registry per Carrie's locked template.

## When the new Social repo exists

Copy this file to the new repo's root (`<social-repo>/CLAUDE.md`) so it travels with the code. This copy at `/Users/jack.fay/Paid Social QA Buddy Bot/CLAUDE.md` can stay as the working-directory memory until then.
