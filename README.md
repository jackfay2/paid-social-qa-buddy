# Paid Social QA Buddy

Meta-platform worker for the Paid Social QA Buddy bot. Part of the QA Buddy ecosystem; the listener and Search worker live in a separate repo owned by Maya Gundepudi.

See `CLAUDE.md` at the repo root for project memory: architecture, hard rules, open decisions, references.

## What this service does

- Receives Cloud Tasks from the `qa-buddy-runs-social` queue (enqueued by the shared listener)
- Reads Meta data from BigQuery (`polaris-data-317717.C<client_id>.facebook_ads__*`, Airbyte-synced daily)
- Looks up client directory info via Polaris (`https://api.polaris.wpromote.com`)
- Runs deterministic checks plus batched Gemini text checks
- Writes verdicts back to the QA sheet
- Posts a summary to the original Slack thread

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env
```

## Run the worker locally

```bash
uvicorn app.api.server:app --host 0.0.0.0 --port 8080 --reload
```

Then `curl http://127.0.0.1:8080/readyz` to confirm.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

## Repo layout

```
app/
  api/        FastAPI app, healthz/readyz/task endpoint
  core/       Backing-service Protocols (contracts.py), orchestration (TBD)
  adapters/   Concrete impls of each Protocol (polaris/, bigquery/, sheets/, slack/, storage/, gemini/) — added as each lands
  checks/     Check registry plus per-check modules — populated per Carrie's locked check_id list
tests/        pytest suite
```

## Hard rules

1. NEVER deploy to production without 99.9% confidence. Test GCP + test Slack workspace first.
2. ALWAYS default to `Review` on uncertainty. The Peacock-Olympics incident is the canonical why.
3. Slack ack within 3 seconds. Worker posts summary to the thread within ~2 minutes typical, 12 minutes max.
4. Check registry is direct dict lookup, not fuzzy match. Unknown `check_id` → `Error: Unrecognized`.
5. NEVER write to a production sheet from a test deployment.

Full rule list in `CLAUDE.md`.

## 12-Factor

Mandated by Brad Ash as the design foundation. Bootstrapped here: structured JSON logging to stdout (XI), env-driven config via `pydantic-settings` (III), backing-service Protocols (IV), SIGTERM handler (IX), pinned dependencies (II, X). Treat as a hard constraint going forward.
