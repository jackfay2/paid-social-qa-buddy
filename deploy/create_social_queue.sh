#!/usr/bin/env bash
# Create the Social Cloud Tasks queue(s), mirroring qa-buddy-runs' rate/retry
# config (captured live 2026-05-29). Idempotent-ish: errors if the queue
# already exists — that's fine, treat as "already done".
#
# MUTATES GCP. Requires CONFIRM=1 to actually run.
#   ./create_social_queue.sh            # prints the plan, does nothing
#   CONFIRM=1 ./create_social_queue.sh  # creates the queues
set -euo pipefail

PROJECT="${PROJECT:-prj-prd-ai-ppc-qa-pkph}"
LOCATION="${LOCATION:-us-west1}"
QUEUES=("qa-buddy-runs-social-test" "qa-buddy-runs-social")

# Mirrors qa-buddy-runs (Search) exactly. Note: maxBurstSize is NOT a
# `queues create` flag — Cloud Tasks derives it from the dispatch rate
# (verified: rate=10 -> burst=10, matching qa-buddy-runs).
RATE=(--max-dispatches-per-second=10 --max-concurrent-dispatches=20)
RETRY=(--max-attempts=5 --min-backoff=5s --max-backoff=300s --max-doublings=16 --max-retry-duration=3600s)

echo "Plan: create Cloud Tasks queues in ${PROJECT}/${LOCATION}:"
for q in "${QUEUES[@]}"; do echo "  - ${q}"; done
echo "  rate:  ${RATE[*]}"
echo "  retry: ${RETRY[*]}"

if [[ "${CONFIRM:-}" != "1" ]]; then
  echo; echo "DRY RUN. Re-run with CONFIRM=1 to create."; exit 0
fi

for q in "${QUEUES[@]}"; do
  echo "Creating ${q}..."
  gcloud tasks queues create "${q}" \
    --project="${PROJECT}" --location="${LOCATION}" \
    "${RATE[@]}" "${RETRY[@]}"
done
echo "Done. Verify: gcloud tasks queues list --project=${PROJECT} --location=${LOCATION}"
