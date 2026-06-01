#!/usr/bin/env bash
# Build the Social worker image and deploy it to Cloud Run.
#
#   ./build_and_deploy_worker.sh test     # build + deploy the TEST service
#   ./build_and_deploy_worker.sh prod     # promote the tested digest to PROD
#
# TEST  : builds a fresh image from this repo, deploys qa-buddy-worker-social-test,
#         grants the SA run.invoker on it, then sets OIDC audience to the service
#         URL and flips QA_CLOUD_TASKS_AUTH_REQUIRED=true.
# PROD  : re-tags the most-recent tested image DIGEST as prod (never rebuilds),
#         deploys qa-buddy-worker-social with --no-traffic, then shifts 100%
#         traffic to the new revision (revision-pinned prod, per CLAUDE.md).
#
# MUTATES GCP. Requires CONFIRM=1. Always do `test` and validate end-to-end in
# the test Slack workspace before `prod` (hard rule #1).
set -euo pipefail

ENVIRONMENT="${1:-}"
[[ "${ENVIRONMENT}" == "test" || "${ENVIRONMENT}" == "prod" ]] || {
  echo "usage: $0 <test|prod>"; exit 2; }

PROJECT="${PROJECT:-prj-prd-ai-ppc-qa-pkph}"
REGION="${REGION:-us-west1}"
SA="${SA:-ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com}"
AR="${AR:-us-west1-docker.pkg.dev/prj-prd-ai-ppc-qa-pkph/cloud-run-source-deploy}"
IMAGE="${AR}/qa-buddy-worker-social"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Timestamp must be passed in (scripts can't call date in a reproducible way);
# default to a placeholder the operator should override: TS=$(date +%Y%m%d-%H%M%S)
TS="${TS:-MANUAL-SET-TS}"

if [[ "${ENVIRONMENT}" == "test" ]]; then
  SERVICE="qa-buddy-worker-social-test"
  SLACK_SECRET="test-slack-bot-token"
else
  SERVICE="qa-buddy-worker-social"
  SLACK_SECRET="slack-bot-token"
fi

COMMON_ENV="QA_SERVICE_ROLE=worker,GCP_PROJECT_ID=${PROJECT},BQ_META_PROJECT=polaris-data-317717,QA_RUN_STORE_BACKEND=firestore,QA_FIRESTORE_COLLECTION_NAME=qa_runs,QA_SHEETS_AUTH_MODE=adc,QA_GEMINI_MODEL=gemini-2.5-flash,QA_WORKER_MAX_RUNTIME_SECONDS=720,QA_CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL=${SA}"
SECRETS="SLACK_BOT_TOKEN=${SLACK_SECRET}:latest,GEMINI_API_KEY=gemini-api-key:latest"

echo "== ${ENVIRONMENT} deploy plan =="
echo "  service : ${SERVICE}"
echo "  image   : ${IMAGE}:${ENVIRONMENT}-${TS}"
echo "  run as  : ${SA}"
echo "  secrets : ${SECRETS}"
echo "  env     : ${COMMON_ENV}"
[[ "${TS}" == "MANUAL-SET-TS" ]] && echo "  !! set TS=\$(date +%Y%m%d-%H%M%S) before running for real"

if [[ "${CONFIRM:-}" != "1" ]]; then
  echo; echo "DRY RUN. Re-run with CONFIRM=1 (and a real TS=) to execute."; exit 0
fi

if [[ "${ENVIRONMENT}" == "test" ]]; then
  echo ">> Building image from ${REPO_ROOT} via Cloud Build..."
  gcloud builds submit "${REPO_ROOT}" --project="${PROJECT}" \
    --tag="${IMAGE}:test-${TS}" --region=global
  # NOTE: VPC-SC log-streaming error is expected; verify with `gcloud builds describe`.

  echo ">> Deploying ${SERVICE} (auth not yet required; need URL first)..."
  gcloud run deploy "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
    --image="${IMAGE}:test-${TS}" --service-account="${SA}" \
    --no-allow-unauthenticated --ingress=all \
    --set-env-vars="${COMMON_ENV},QA_CLOUD_TASKS_AUTH_REQUIRED=false" \
    --set-secrets="${SECRETS}"

  URL="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" --format='value(status.url)')"
  echo ">> Service URL (status.url): ${URL}"

  # ⚠️ A Cloud Run service answers to TWO URL aliases (project-number form
  # `…-637315940254.…run.app` and hash form `…-kkih6nvcjq-uw.a.run.app`), and
  # status.url can return either. The OIDC audience the worker EXPECTS must
  # byte-match the audience the LISTENER signs (its SOCIAL_WORKER_AUDIENCE) or
  # every Cloud Tasks delivery 401s (broke the @-mention 2026-06-01). So pin the
  # audience to the listener's value, NOT to status.url. Falls back to status.url
  # only if the listener isn't deployed yet.
  AUDIENCE="$(gcloud run services describe qa-buddy-listener-social-test \
    --project="${PROJECT}" --region="${REGION}" --format='json' 2>/dev/null \
    | python3 -c 'import json,sys; e={x["name"]:x.get("value") for x in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"]}; print(e.get("SOCIAL_WORKER_AUDIENCE",""))' 2>/dev/null)"
  [[ -n "${AUDIENCE}" ]] || AUDIENCE="${URL}"
  echo ">> OIDC audience (must match listener SOCIAL_WORKER_AUDIENCE): ${AUDIENCE}"

  echo ">> Granting the SA run.invoker on ${SERVICE} (queue OIDC -> worker)..."
  gcloud run services add-iam-policy-binding "${SERVICE}" \
    --project="${PROJECT}" --region="${REGION}" \
    --member="serviceAccount:${SA}" --role="roles/run.invoker"

  echo ">> Second pass: enable OIDC auth with audience=${AUDIENCE}..."
  gcloud run services update "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
    --update-env-vars="QA_CLOUD_TASKS_AUTH_REQUIRED=true,QA_CLOUD_TASKS_OIDC_AUDIENCE=${AUDIENCE}"

  echo "Done. Hand Maya: QA_CLOUD_TASKS_WORKER_URL_SOCIAL=${URL}/tasks/qa/run"
  echo "Check health: curl -s ${URL}/readyz  (expect 200; 503 = a secret failed to resolve)"
else
  # PROD: promote the tested digest, never rebuild.
  echo ">> Resolving the most-recent tested image digest..."
  DIGEST="$(gcloud artifacts docker images list "${IMAGE}" --project="${PROJECT}" \
    --include-tags --filter='tags:test-*' --sort-by='~UPDATE_TIME' --limit=1 \
    --format='value(version)')"
  [[ -n "${DIGEST}" ]] || { echo "No tested image found to promote."; exit 1; }
  echo ">> Promoting digest ${DIGEST} -> ${IMAGE}:prod-${TS}"
  gcloud artifacts docker tags add "${IMAGE}@${DIGEST}" "${IMAGE}:prod-${TS}"

  echo ">> Deploying ${SERVICE} with --no-traffic (revision-pinned prod)..."
  gcloud run deploy "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
    --image="${IMAGE}:prod-${TS}" --service-account="${SA}" \
    --no-allow-unauthenticated --ingress=all --no-traffic \
    --set-env-vars="${COMMON_ENV},QA_CLOUD_TASKS_AUTH_REQUIRED=true" \
    --set-secrets="${SECRETS}"

  gcloud run services add-iam-policy-binding "${SERVICE}" \
    --project="${PROJECT}" --region="${REGION}" \
    --member="serviceAccount:${SA}" --role="roles/run.invoker" || true

  URL="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" --format='value(status.url)')"
  NEW_REV="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" --format='value(status.latestCreatedRevisionName)')"
  echo ">> New revision ${NEW_REV}. Set OIDC audience, then shift traffic."
  gcloud run services update "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
    --update-env-vars="QA_CLOUD_TASKS_OIDC_AUDIENCE=${URL}"
  echo ">> curl ${URL}/readyz before shifting traffic (expect 200)."
  echo ">> Shifting 100% traffic to ${NEW_REV}..."
  gcloud run services update-traffic "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
    --to-revisions="${NEW_REV}=100"
  echo "Done. Prod worker URL: ${URL}/tasks/qa/run"
fi
