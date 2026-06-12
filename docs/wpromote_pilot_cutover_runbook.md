# Wpromote pilot cutover runbook

How to move the QA bot from our test workspace (`@social_qa_test`) to a channel
in the **Wpromote** workspace for the AM pilot.

**Key principle:** this only swaps the **Slack-app-specific** config. The Cloud
Tasks queue, the worker, BigQuery, OIDC, and the service account are all
untouched. So the blast radius is small: if Slack delivery breaks, the data path
is fine, and rollback is just reverting a few values.

**This is a full cutover, not an add-on.** The test workspace stops working once
the tokens change (one listener verifies one signing secret; one worker posts
with one token). So finish all dress rehearsals in the test workspace *before*
cutting over.

---

## Prerequisites (what Anthony / the Wpromote app install gives you)

1. The app installed in the Wpromote workspace, with bot scopes
   `app_mentions:read` and `chat:write`.
2. The **Bot User OAuth Token** (`xoxb-...`).
3. The **Signing Secret** (App > Basic Information).
4. The ability to set the **Event Subscriptions Request URL** and subscribe to
   the `app_mention` event.
5. The **pilot channel ID** (open the channel in Slack > View channel details >
   the `C...` ID at the bottom).

---

## Steps

### 1. Store the two new secrets
```
printf '%s' 'xoxb-NEW-WPROMOTE-BOT-TOKEN' | \
  gcloud secrets create pilot-slack-bot-token --project=prj-prd-ai-ppc-qa-pkph --data-file=-
printf '%s' 'NEW-WPROMOTE-SIGNING-SECRET' | \
  gcloud secrets create pilot-slack-signing-secret --project=prj-prd-ai-ppc-qa-pkph --data-file=-
```
(If the secrets already exist, use `gcloud secrets versions add <name> --data-file=-` instead.)
The service account already has `secretmanager.secretAccessor` at the project level, so no extra grant.

### 2. Get the new app's bot user ID
The listener needs this to recognize its own @-mentions.
```
curl -s -H "Authorization: Bearer xoxb-NEW-WPROMOTE-BOT-TOKEN" https://slack.com/api/auth.test \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print('bot_user_id:',d.get('user_id'),'team:',d.get('team'))"
```
Note the `bot_user_id` (a `U...` value).

### 3. Update the listener (no rebuild needed, code is unchanged)
```
gcloud run services update qa-buddy-listener-social-test \
  --project=prj-prd-ai-ppc-qa-pkph --region=us-west1 \
  --update-secrets=SLACK_BOT_TOKEN=pilot-slack-bot-token:latest,SLACK_SIGNING_SECRET=pilot-slack-signing-secret:latest \
  --update-env-vars=SLACK_BOT_USER_ID=<NEW_BOT_USER_ID>,SLACK_BOT_MENTION=@<new_handle>,SOCIAL_CHANNEL_IDS=<WPROMOTE_CHANNEL_ID>
```
All five of these are app/workspace-specific and must change. Everything else on
the listener (`SOCIAL_WORKER_URL`, `SOCIAL_WORKER_AUDIENCE`, the queue, the SA)
stays exactly as is.

### 4. Update the worker (so it posts results AS the Wpromote bot)
```
gcloud run services update qa-buddy-worker-social-test \
  --project=prj-prd-ai-ppc-qa-pkph --region=us-west1 \
  --update-secrets=SLACK_BOT_TOKEN=pilot-slack-bot-token:latest
```
This is the easy step to forget. A bot token only posts in the workspace it came
from, so if the worker keeps the old token the ack shows up but the results post
never lands. Verify health after:
```
URL=https://qa-buddy-worker-social-test-637315940254.us-west1.run.app
TOKEN=$(gcloud auth print-identity-token --impersonate-service-account=ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com --audiences="$URL")
curl -s -H "Authorization: Bearer $TOKEN" "$URL/readyz"   # expect {"status":"ready"...} 200
```

### 5. Point the app's Event Request URL at the listener
Get the listener URL:
```
gcloud run services describe qa-buddy-listener-social-test \
  --project=prj-prd-ai-ppc-qa-pkph --region=us-west1 --format='value(status.url)'
```
In the Slack app config (Event Subscriptions): set **Request URL** to
`<listener-url>/slack/events`. Slack sends a verification challenge; the listener
answers it automatically, so it should flip to **Verified**. Subscribe to the
`app_mention` bot event. Reinstall the app if you changed scopes.

### 6. Invite the bot and smoke test
- `/invite @<bot>` in the pilot channel.
- @-mention the bot against a known-good campaign (Avara: `433162067600511` /
  `120233923140570101`) and confirm the ack and the results post both land in the
  thread.
- Run it once against a blank sheet to confirm the "no checks found" message.
- Have someone who isn't you @-mention it, to confirm there's no user gate
  (there isn't one in code, but verify in the real workspace).

---

## Do NOT touch (the plumbing that already works)
- `SOCIAL_WORKER_URL` and `SOCIAL_WORKER_AUDIENCE` on the listener, and
  `QA_CLOUD_TASKS_OIDC_AUDIENCE` on the worker. These three must stay byte-equal
  (all the project-number URL `...-637315940254...`). Changing one and not the
  others 401s every delivery. This bit Maya's listener on 2026-06-01.
- The Cloud Tasks queue (`qa-buddy-runs-social-test`) and the service account.

## Rollback (back to the test workspace)
Revert the same fields:
```
# listener
gcloud run services update qa-buddy-listener-social-test --project=prj-prd-ai-ppc-qa-pkph --region=us-west1 \
  --update-secrets=SLACK_BOT_TOKEN=social-test-slack-bot-token:latest,SLACK_SIGNING_SECRET=social-test-slack-signing-secret:latest \
  --update-env-vars=SLACK_BOT_USER_ID=U0B71RZQU4X,SLACK_BOT_MENTION=@social_qa_test,SOCIAL_CHANNEL_IDS=C0B6ASW9R9V
# worker
gcloud run services update qa-buddy-worker-social-test --project=prj-prd-ai-ppc-qa-pkph --region=us-west1 \
  --update-secrets=SLACK_BOT_TOKEN=social-test-slack-bot-token:latest
```

---

## Quick reference (current test-workspace values, as of 2026-06-12)

| Thing | Value |
|---|---|
| Project / region / SA | `prj-prd-ai-ppc-qa-pkph` / `us-west1` / `ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com` |
| Worker service / rev | `qa-buddy-worker-social-test` / `00027-hc9` |
| Worker URL + OIDC audience | `https://qa-buddy-worker-social-test-637315940254.us-west1.run.app` |
| Listener service | `qa-buddy-listener-social-test` |
| Cloud Tasks queue | `qa-buddy-runs-social-test` |
| Current test bot | user `U0B71RZQU4X`, mention `@social_qa_test`, channel `C0B6ASW9R9V` |
| Current secrets | `social-test-slack-bot-token`, `social-test-slack-signing-secret` |
| Listener Slack fields to swap | `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_BOT_USER_ID`, `SLACK_BOT_MENTION`, `SOCIAL_CHANNEL_IDS` |
| Worker Slack field to swap | `SLACK_BOT_TOKEN` |
