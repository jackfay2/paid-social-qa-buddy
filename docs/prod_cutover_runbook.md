# Social prod cutover — runbook + coordination contract

**Status:** prep, 2026-06-04. Test path is live + validated
(`qa-buddy-worker-social-test` rev `00022-nlh`, `/readyz` 200). This is the
ready-to-execute plan for going to prod. **Nothing here is executed yet** — prod
cutover is a deliberate, approved, coordinated step (hard rule #1: 99.9%
confidence, test first).

## Current infra state (verified 2026-06-04)
| Component | Test | Prod |
|---|---|---|
| Worker (ours) | `qa-buddy-worker-social-test` ✓ | **missing — create `qa-buddy-worker-social`** |
| Queue | `qa-buddy-runs-social-test` ✓ | **missing — create `qa-buddy-runs-social`** |
| Listener (Maya's) | `qa-buddy-listener-social-test` ✓ | shared `qa-buddy-listener` ✓ — **needs the platform→social routing change** |

Project `prj-prd-ai-ppc-qa-pkph`, region/location `us-west1`. Prod Search
(`qa-buddy-worker` / `qa-buddy-runs` / `qa-buddy-listener`) already runs here —
Social slots in beside it.

## Prerequisites (before any prod step)
1. **Stakeholder approval** (Kerri / Sami) to go live.
2. **Carrie's check_id list** ideally locked (not strictly blocking — the registry
   runs whatever check_ids are in the sheet — but it sets the template).
3. The shared **`@qa-buddy` prod Slack app** handles the social mention (Brad's
   one-app rule); prod worker posts via the shared `slack-bot-token`. No separate
   social Slack app in prod.
4. Secrets already in Secret Manager (shared with prod Search): `slack-bot-token`,
   `gemini-api-key`, and the Sheets SA JSON. `/readyz` will confirm they resolve.

## The coordination contract (the entire surface with Maya's listener)
Lock these three; they're the whole interface:
1. **Envelope** the listener enqueues (JSON): `request_id`, `channel_id`,
   `thread_ts`, `sheet_url`, `account_id` (or legacy `customer_id`), `campaign_id`,
   `campaign_name`, `qa_app: "social"`, `requester_text` (raw @-mention text — also
   carries the "Peacock" keyword), optional `peacock`. Missing `qa_app`/`platform`
   defaults to search (back-comp).
2. **Queue + worker URL:** `qa-buddy-runs-social` (us-west1) → POSTs to
   `https://qa-buddy-worker-social-<hash>.run.app/tasks/qa/run` with an OIDC token.
   Listener env: `QA_CLOUD_TASKS_WORKER_URL_SOCIAL` + the queue name.
3. **OIDC audience — the byte-match trap (this broke the test @-mention 2026-06-01):**
   the worker answers two URL aliases (project-number `…-637315940254.…` and hash
   `…-kkih6nvcjq-uw.…`). The listener's `SOCIAL_WORKER_AUDIENCE` MUST byte-match the
   worker's `QA_CLOUD_TASKS_OIDC_AUDIENCE`. Pick ONE alias and set both to it.

## Cutover steps

### A. Ours (worker + queue)
1. **Create the prod queue:**
   ```
   gcloud tasks queues create qa-buddy-runs-social --location=us-west1 \
     --project=prj-prd-ai-ppc-qa-pkph
   ```
2. **Promote the tested image to prod** (re-tag the tested digest, never rebuild —
   keeps test/prod byte-identical). Uses the deploy script's prod path:
   ```
   CONFIRM=1 TS=$(date +%Y%m%d-%H%M%S) ./deploy/build_and_deploy_worker.sh prod
   ```
   This deploys `qa-buddy-worker-social` with `--no-traffic`, grants the SA
   `run.invoker`, sets `QA_CLOUD_TASKS_OIDC_AUDIENCE`, then shifts 100% traffic to
   the new revision (revision-pinned prod). Uses `slack-bot-token` (shared).
3. **Verify before trusting:** `curl -s <prod-url>/readyz` → expect 200
   (`status: ready`, no secret errors). Trust `/readyz`, not CLI text.
4. **Hand Maya:** the prod worker URL (`…/tasks/qa/run`) + the exact OIDC audience.

### B. Maya's (listener routing) — the one-pager for her
- Route `qa_app == "social"` (else default search) → enqueue to
  `qa-buddy-runs-social` (us-west1).
- Cloud Task targets `QA_CLOUD_TASKS_WORKER_URL_SOCIAL = <prod worker>/tasks/qa/run`.
- OIDC token: service account `ppc-qa-buddy@…`, **audience = the exact worker URL
  alias from A.4** (byte-match — see the trap above).
- Envelope per the contract; forward the raw mention text as `requester_text`.

### C. Smoke test (prod, real @-mention)
A real `@qa-buddy` mention in the prod social channel with a known good standard
campaign → confirm: listener acks <3s → task enqueued → worker runs → verdicts
written to the sheet → summary posted in-thread. Use a well-synced client (e.g.
`C51305634`-class) so settings checks are deterministic.

## Rollback
- Worker: `gcloud run services update-traffic qa-buddy-worker-social
  --to-revisions <previous>=100` (instant revert; revisions are pinned).
- Listener: Maya reverts the routing (social falls back to search-default or a
  "not yet live" path). Queue can be paused: `gcloud tasks queues pause
  qa-buddy-runs-social`.

## What's verified ready vs pending
- ✓ Worker code + image (tested, rev 00022-nlh), `/readyz`, checks validated on 5
  real clients (0 errors), deploy script prod path, secrets exist.
- ⏳ Create prod queue + worker (A1–A2), Maya's routing (B), approval + smoke (C).
