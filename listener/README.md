# Listener copy (search + social routing)

A vendored copy of Maya's Slack listener, so we can add **social routing** and
run the merged search+social listener **without touching her live listener**.
This is the test path from the handoff plan ("copy her test listener into a new
test service with the Social changes added; production listener stays unified").

## Status

- ✅ **Vendored baseline (2026-05-29).** Faithful, unmodified copy of her
  listener layer from `qa-buddy-bot` (the 2026-05-28 zip). Self-contained
  (every `app.*` import resolves within this folder) and verified importing.
  - `app/listener/*.py` — slack_listener, slack_parser, slack_messages, slack_models, cloud_tasks_service, entity_filter
  - `app/routing.py` — MCC routing (Search)
  - `app/adapters/tasks/` — `CloudTasksQAQueue` (project, location, queue_name, worker_url, service_account_email, OIDC)
- ✅ **Routing core (2026-05-29)** — `qa_app` added to `CloudTasksRequest`
  (defaults `"search"` → no regression) + `app/listener/platform_router.py`
  `RoutingQAQueue` routes by `payload.qa_app` (social→social queue,
  else→search). Her enqueue service is **unmodified** — inject the router as
  its `queue`. 6 tests in `tests/test_platform_router.py` (run `cd listener && pytest`).
- ✅ **`qa_app` intake via channel inference (2026-06-01)** — `RoutingQAQueue`
  now takes `social_channel_ids` and routes social when `payload.channel_id` is
  a configured social channel (the locked "listener infers from channel"
  decision). This keeps **her enqueue service 100% unmodified** — no editing the
  3 `CloudTasksRequest` construction sites; the router infers right before
  enqueue. 10 tests in `tests/test_platform_router.py`.
- ⬜ **Server entrypoint** — focused FastAPI app: `POST /slack/events` (verify
  signing secret, url_verification challenge, 3s ack + background processing),
  wiring two `CloudTasksQAQueue`s (search = `qa-buddy-runs` → her worker; social
  = `qa-buddy-runs-social-test` → our worker URL + OIDC) into a `RoutingQAQueue`
  with `social_channel_ids={C0B6ASW9R9V}`, injected into
  `SlackCloudTasksEnqueueService(queue=router, run_store=…)`. Coupling to map:
  her enqueue service needs a `run_store` (for dedupe/retry-window) — check
  which methods it calls + back it with Firestore or in-memory for the test.
- ⬜ Deploy as `qa-buddy-listener-social-test` (Cloud Run, reuse
  `test-slack-bot-token` + `test-slack-signing-secret` from Secret Manager).
- ⬜ **Same-bot repoint (not a separate app):** point the existing test Slack
  app's Events URL at this listener (Maya/Slack admin). Our copy carries her full
  Search routing, so Search behaves identically — it just adds Social. One URL,
  one shared listener = the prod topology.

## How her enqueue works (the routing seam)

`SlackCloudTasksEnqueueService.enqueue(payload)` calls an injected
`self.queue: CloudTasksQAQueue`. The queue is configured (in the server wiring)
with a single `queue_name` + `worker_url` + OIDC. So routing = **which queue
instance** handles a request. Today there's one (Search → `qa-buddy-runs`).

The envelope (`CloudTasksRequest`) uses `customer_id` for the account id. Our
worker's `SocialTaskRequest` already accepts `customer_id` as an alias for
`account_id`, so **her enqueue payload maps straight to our worker** — no
envelope change needed on our side.

## Social-routing plan (the "add our code" part)

1. **Parse `qa_app`** — in `slack_parser.py` add `qa_app`/`platform` to
   `_FIELD_ALIASES` and surface it on the parsed result (default `"search"`).
   (Or infer from the channel, per the locked decision — `qa_app` "search"/"social".)
2. **Second queue (social)** — construct a `CloudTasksQAQueue` configured for:
   - `queue_name = qa-buddy-runs-social-test`
   - `worker_url = https://qa-buddy-worker-social-test-637315940254.us-west1.run.app/tasks/qa/run`
   - `service_account_email = ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com`
   - OIDC audience = the worker base URL
3. **Route by `qa_app`** — `SlackCloudTasksEnqueueService` picks the social
   queue when `qa_app=social`, else the search queue. For social, skip the
   10-digit `account_id` validation + MCC routing (Meta IDs are ~17 digits).
   Search path stays byte-identical → no regression.
4. **Server entrypoint** — a focused `listener/app/api/server.py` (FastAPI):
   Slack events route (verify signing secret, 3s ack, `app_mention`/message
   handling), wiring that builds **both** queues + the enqueue service. (Prefer
   a small focused entrypoint over vendoring her whole `api/server.py`, which
   carries worker-role + Search wiring we don't need.)

## Remaining infra (the external dependency)

- **Deploy** this as `qa-buddy-listener-social-test` (Cloud Run, `QA_SERVICE_ROLE=listener`-style).
- **Slack app:** a Slack app has exactly one Events Request URL. The test
  workspace's `@QA Buddy Bot Test` points at *Maya's* listener. To send
  `@-mentions` here without disturbing hers, create a **separate test Slack app**
  (its own bot token + signing secret) with Events URL → this listener. Needs
  Maya/Jack in the Slack admin.

## Sync

This is a vendored copy. Re-sync from her `main` when she ships listener
changes (she shipped 9 days of commits between the 05-19 and 05-28 zips). Keep
our social-routing changes as a clear, separable layer on top.
