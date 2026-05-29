#!/usr/bin/env bash
# Grant the Social worker SA the IAM deltas it doesn't already have.
# Grounded against live IAM 2026-05-29: the SA already has bigquery.dataViewer
# on polaris-data-317717, cloudtasks.enqueuer, datastore.user, and
# secretmanager.secretAccessor. The only project-level delta is bigquery.jobUser.
# (run.invoker on the worker service is applied by build_and_deploy_worker.sh
# once the service exists.)
#
# MUTATES GCP IAM. Requires CONFIRM=1. You need project IAM-admin rights to run
# this; if you don't, hand deploy/README.md's IAM table to ai-team@.
set -euo pipefail

PROJECT="${PROJECT:-prj-prd-ai-ppc-qa-pkph}"
SA="${SA:-ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com}"

echo "Plan: grant to ${SA}:"
echo "  - roles/bigquery.jobUser on ${PROJECT}  (run BQ query jobs)"
echo "  (already present, not re-granted: bigquery.dataViewer@polaris-data-317717,"
echo "   cloudtasks.enqueuer, datastore.user, secretmanager.secretAccessor)"

if [[ "${CONFIRM:-}" != "1" ]]; then
  echo; echo "DRY RUN. Re-run with CONFIRM=1 to apply."; exit 0
fi

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA}" \
  --role="roles/bigquery.jobUser" \
  --condition=None
echo "Done. Verify: gcloud projects get-iam-policy ${PROJECT} \\"
echo "  --flatten='bindings[].members' --filter='bindings.members:${SA}' --format='value(bindings.role)'"
