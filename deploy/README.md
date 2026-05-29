# Deploy runbook — Paid Social QA worker

Turnkey deploy of the Social worker to Cloud Run + the `qa-buddy-runs-social`
Cloud Tasks queue, in the **shared** project `prj-prd-ai-ppc-qa-pkph`
(us-west1), alongside Maya's Search services. Grounded against the live project
state on 2026-05-29 (read-only inspection).

> **Nothing here runs automatically.** These are scripts + a checklist. GCP
> changes only when *you* execute a script, and the mutating ones require
> `CONFIRM=1`. Hard rules apply: **test service first**, never prod from a test
> deploy, 99.9% confidence before prod.

## Current state (what already exists)

| Thing | Value |
|---|---|
| Project / region | `prj-prd-ai-ppc-qa-pkph` / `us-west1` |
| Worker service account | `ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com` |
| Search services (mirror these) | `qa-buddy-worker`, `qa-buddy-worker-test`, `qa-buddy-listener(-test)` |
| Existing queue | `qa-buddy-runs` (Search) — **`qa-buddy-runs-social` does NOT exist yet** |
| Artifact Registry | `us-west1-docker.pkg.dev/prj-prd-ai-ppc-qa-pkph/cloud-run-source-deploy` (DOCKER) |
| Secrets (Secret Manager) | `slack-bot-token`, `slack-signing-secret`, `test-slack-bot-token`, `test-slack-signing-secret`, `gemini-api-key` |

## Names we will create

| Thing | Test | Prod |
|---|---|---|
| Cloud Run service | `qa-buddy-worker-social-test` | `qa-buddy-worker-social` |
| Cloud Tasks queue | `qa-buddy-runs-social-test` | `qa-buddy-runs-social` |
| Image (in existing AR repo) | `qa-buddy-worker-social:test-<ts>` | re-tag tested digest → `:prod-<ts>` |

## IAM checklist (the ask for whoever provisions)

Grounded against live IAM on 2026-05-29. The SA `ppc-qa-buddy@` currently has
`cloudtasks.enqueuer`, `datastore.user`, `secretmanager.secretAccessor`, and
`bigquery.dataViewer` on `polaris-data-317717`.

| Role | Resource | Status | Why |
|---|---|---|---|
| `roles/bigquery.dataViewer` | `polaris-data-317717` | ✅ already granted | read Meta data |
| `roles/bigquery.jobUser` | `prj-prd-ai-ppc-qa-pkph` | ❌ **NEEDED** | run BQ query jobs (dataViewer alone can't) |
| `roles/run.invoker` | `qa-buddy-worker-social[-test]` | ❌ needed at create | queue's OIDC token invokes the worker |
| `roles/cloudtasks.enqueuer` | project | ✅ already granted | listener enqueues to the social queue |
| `roles/datastore.user` | project | ✅ already granted | Firestore run store |
| `roles/secretmanager.secretAccessor` | project | ✅ already granted | read slack/gemini secrets |

**Deployer (you) needs** (confirm — not visible at project level, likely
inherited via `ai-team@` / a folder role): `run.admin` (or `run.developer`),
`cloudbuild.builds.editor`, `artifactregistry.writer`, `cloudtasks.admin`, and
`iam.serviceAccountUser` on `ppc-qa-buddy@` (to deploy a service that runs as
it). Granting the SA roles above needs project IAM admin — ask `ai-team@` if you
can't.

Run `./grant_iam.sh` (with `CONFIRM=1`) to apply the SA deltas, or hand this
table to the provisioner.

## Order of operations

1. **Confirm deploy access** (roles above). `gcloud auth login`; `gcloud config set project prj-prd-ai-ppc-qa-pkph`.
2. **Create the queues** — `./create_social_queue.sh` (mirrors `qa-buddy-runs` rate/retry config).
3. **Grant SA IAM deltas** — `./grant_iam.sh` (adds `bigquery.jobUser`; `run.invoker` is applied by the deploy script once the service exists).
4. **Build + deploy the TEST worker** — `./build_and_deploy_worker.sh test`. Note its URL.
5. **Wire OIDC** — the deploy script sets `QA_CLOUD_TASKS_OIDC_AUDIENCE` to the service URL and `QA_CLOUD_TASKS_AUTH_REQUIRED=true` on the second pass (after the URL is known).
6. **Hand Maya the test worker URL** for her listener: `QA_CLOUD_TASKS_WORKER_URL_SOCIAL` + route `qa_app=social` → `qa-buddy-runs-social-test`.
7. **Validate end-to-end in the test Slack workspace** (real `@-mention`). Verify `/readyz` is 200 first (it's fail-closed on bad secrets).
8. **Promote to prod** — `./build_and_deploy_worker.sh prod` re-tags the *tested image digest* (never rebuilds) and does the 2-step traffic migration. Then Maya points the prod listener at the prod worker URL + `qa-buddy-runs-social`.

## Deploy quirks (baked into the scripts — from CLAUDE.md)

- **Prod traffic is revision-pinned.** Updating the image alone does NOT shift traffic. The prod path deploys `--no-traffic` then `update-traffic --to-revisions <new>=100`.
- **Promote by re-tagging the tested digest**, not rebuilding — keeps test/prod byte-identical.
- **Cloud Build VPC-SC log-streaming error is expected.** Verify success with `gcloud builds describe <id> --region=global --format='value(status)'`, not log streaming.
- **Trust `/readyz` for rollout health** (it returns 503 if a secret fails to resolve), not CLI success text.

## Runtime config the worker gets

- **Plain env** (`--set-env-vars`): `QA_SERVICE_ROLE=worker`, `GCP_PROJECT_ID`, `BQ_META_PROJECT=polaris-data-317717`, `QA_RUN_STORE_BACKEND=firestore`, `QA_FIRESTORE_COLLECTION_NAME=qa_runs`, `QA_SHEETS_AUTH_MODE=adc`, `QA_CLOUD_TASKS_AUTH_REQUIRED`, `QA_CLOUD_TASKS_OIDC_AUDIENCE`, `QA_CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL=ppc-qa-buddy@…`, `QA_GEMINI_MODEL`, `QA_WORKER_MAX_RUNTIME_SECONDS=720`.
- **Secret-backed env** (`--set-secrets`, native Cloud Run mounting): `SLACK_BOT_TOKEN` ← `test-slack-bot-token` (test) / `slack-bot-token` (prod); `GEMINI_API_KEY` ← `gemini-api-key`.
- **Sheets need no secret** — auth is ADC as the attached SA; the QA sheet (per request) must be shared with `ppc-qa-buddy@…`. (Do NOT set `QA_USE_SECRET_MANAGER`; that path is for the SA-JSON mode we don't use.)
- **No Google-Ads / MCC env** — those are Search-only.

## Rollback

Cloud Run keeps revisions. To roll back:
`gcloud run services update-traffic qa-buddy-worker-social --region=us-west1 --to-revisions <previous-revision>=100`.
