# Paid Social QA Buddy Bot: Project Handoff

A complete rundown for someone picking up the project. Read it top to bottom once; it is organized so you can come back to any section later.

## If you read nothing else

The bot QAs Meta ad campaigns: an account manager says what a campaign should be, the bot reads what it actually is from BigQuery, and flags the differences. The single hardest constraint is that it must never falsely say something passed, when unsure it returns "Review." The single biggest dependency is that the client's data has to be synced into BigQuery, or the bot is blind to it. Most of what is happening right now is about a data migration that fixes that dependency for more clients.

## 1. What it is

The Paid Social QA Buddy Bot is a Slack bot that QAs Meta ad campaigns. An account manager fills a QA sheet with what a campaign is supposed to be, mentions the bot in Slack with the account ID, campaign ID, and sheet link, and the bot pulls the campaign's real settings, compares them to the expectations, writes a verdict on every row of the sheet, and posts a summary back in Slack. It replaces the manual, line-by-line QA that AMs do by hand today.

It is the Paid Social extension of the existing Search QA Buddy (Maya Gundepudi's project). Phase 1 is Meta only. Later phases add TikTok, Snap, Reddit, Pinterest, and LinkedIn using the same architecture.

## 2. The five verdicts (the core concept)

Every checked row gets one of these:

| Verdict | Meaning |
|---|---|
| Pass | The campaign matches what the AM entered. |
| Fix | It does not match. The bot shows the actual value it found. |
| Review | The bot could not confirm it automatically, so it asks a human to check, with a note. |
| N/A | The row was left blank, so it was skipped. |
| Error | Something was wrong with the row itself (rare). |

The cardinal rule, inherited from Search and non-negotiable: the bot never returns a false Pass or Fix. When it is not sure, it returns Review. This exists because of the "Peacock-Olympics incident," where a wrong value survived multiple rounds of manual QA and was caught by a Meta rep on a Sunday. Defaulting to Review on any uncertainty is the entire safety philosophy. Do not erode it to make the numbers look better.

## 3. How it works, end to end

1. The AM fills the "Builder Input" column of a QA sheet with the expected values.
2. The AM mentions the bot in Slack, with Account_id, Campaign_id, and Sheet_url, one per line.
3. The listener service verifies the Slack request, parses the message, and enqueues a task to a Cloud Tasks queue.
4. The worker service picks up the task, resolves the account to a client, reads the campaign's real settings from BigQuery, runs the checks, writes the verdicts back into the sheet, and posts a summary to the Slack thread.
5. Spelling and other text checks go through Gemini in a single batched call.

## 4. The architecture

- Shared listener, split workers per platform. One Slack app, one events URL, one listener; it routes by platform to a per-platform worker. Social has its own worker (this repo). Search is Maya's.
- Cloud Run plus Cloud Tasks plus OIDC. The worker is private; the queue delivers each task with an OIDC token whose audience must byte-match the worker's expected audience.
- 12-Factor, mandated by Brad (head engineer). Treat it as a hard constraint, not a style preference.
- The adapter pattern. The bot reads Meta data through a swappable client interface, so the data source can be changed by config. This matters a lot for the migration in section 6.

## 5. The data layer (the most important and most misunderstood part)

Read this carefully, because it is where new people get confused.

The bot does not call the Meta API directly. It reads Meta data from BigQuery, where Airbyte syncs it daily from the Meta Marketing API. Three things to internalize:

- The current source is the project `polaris-data-317717`. There is one dataset per client, named `C<client_id>` (for example `C73556393`). Each has the tables `facebook_ads__campaigns`, `facebook_ads__adsets`, `facebook_ads__adset_targetings`, `facebook_ads__ads`, and `facebook_ads__ad_creatives`.
- An account ID resolves to a client (the dataset) through the table `summary.facebook_ads__account_performance`. If a client is not in that table, the bot literally cannot see it.
- The hard gate for the entire bot: the client must be synced into BigQuery. About 301 clients are. If a client is not, the bot returns nothing for it, no matter what.

There is also Polaris (the CRM at api.polaris.wpromote.com) used for recipient and routing lookups, but that is secondary. The QA data itself is BigQuery.

## 6. The big current thing: the data migration

The Meta data is moving to a new marts platform, the project `prj-npd-plrs-tst-marts-onfd` (Riley and Nikki's work). It matters for two reasons:

- It has fields the current source lacks: conversion event (`promoted_object`), optimization goal, attribution, and spend caps. Those missing fields are why roughly seven checks currently come back Review. The marts makes them automatic.
- It likely has more clients than the current 301. Two pilot clients, Kendra Scott and ZAGG, are not in `polaris-data-317717` at all, but may be in the marts.

The bot's data source is a config knob (`BQ_META_PROJECT`), and the marts uses the same `C<id>.facebook_ads__*` layout, so the repoint is very likely a pure config swap rather than a rewrite. It goes live tomorrow. Access is pending Brad's sign-off, the bot's service account needs read access on the marts. We are framing that as continuity, because the bot already reads prod Meta data through a locked-down read-only service account, so it is the same pattern following the data to a new home, not a new kind of access. There is a ready-to-run check, `scripts/verify_new_marts.py`, that confirms access, structure, fields, and the unsynced clients the moment access lands.

## 7. The pilot

Five AMs are piloting. Status by client:

- Avara (`C73556393`) and Spindrift (`C52559738`): synced, validated end to end, bot-ready.
- Kendra Scott and ZAGG: not synced, blocked until the marts.
- The others: unknown until they send a campaign.

Two realities the pilot has taught us:

- AMs use their own QA sheet formats, not our template. Theirs have per-campaign tabs, manual TRUE/FALSE columns, and an "Output" column holding the expected values. So the model is not "make them adopt our template." It is: they send their sheet, and we pull the campaign (from the campaign hyperlink inside the sheet) and the expected values, and run the bot for them. There is a clean guide for AMs, but for the pilot we run it on their behalf.
- Screen every client first. Resolve their account against the synced data before committing them. Only synced clients can use the bot today.

## 8. Hard rules and gotchas (the things that will bite you)

- Never write to the master QA template (`12CMnQyqwgmKwGaujE5Hu64sswvlDfFT5Cs-JEJNtF4Y`). It has no check IDs anyway, so fresh copies of it are broken. AMs work from a distribution copy (`1rTfqYA3xjvQyHwnsEj9c9gF_exzjvIz1Aq6Sbwe1TSQ`) that does have them.
- The sheet must be shared with the service account `ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com` as Editor, or the bot cannot read or write it. This is the single most common user error.
- The listener only answers in allowlisted channels (`SOCIAL_CHANNEL_IDS`, currently `C0B6ASW9R9V`). Mentions anywhere else are ignored.
- The OIDC audience must byte-match between the listener (`SOCIAL_WORKER_AUDIENCE`) and the worker (`QA_CLOUD_TASKS_OIDC_AUDIENCE`), in the project-number URL form. A mismatch makes every queue delivery 401.
- Cloud Build log-streaming errors are expected on this project (VPC-SC). The build still succeeds. Verify with `gcloud builds describe`, not the streamed logs. The deploy script stops on this error, so the deploy is finished by hand (the cutover runbook documents it).
- Gemini is for narrow yes/no text classification only, like spelling, never translation or nuanced typos. One batched call per job, temperature 0 for determinism, fail-safe to Review, and it caps at the first 25 ads per run.

## 9. How to run, test, and deploy

- Run a QA against real data locally: `scripts/sheet_run.py --sheet-url ... --account-id ... --campaign-id ...`. It impersonates the service account; add `--dry-run` to skip the write.
- Screen a client or check the marts: `scripts/verify_new_marts.py` (run it once the marts is live).
- Deploy the worker: `deploy/build_and_deploy_worker.sh test` (or `prod`). Test auto-routes traffic to the new revision; prod is revision-pinned and needs a two-step traffic shift. Never deploy to prod without near-certainty, and validate in the test Slack workspace first.
- Auth for local work: you impersonate the worker service account with gcloud, which requires `roles/iam.serviceAccountTokenCreator` on it.

## 10. People and who owns what

- Jack Fay: current implementer, handing the project to you.
- Maya Gundepudi: owns Search QA Buddy and the shared listener repo.
- Brad Ash: head engineer; owns GCP, access, and architecture. The marts access decision is his.
- Carrie: owns the QA sheet template and the final check_id list.
- Riley Cheok and Nikki: BigQuery field additions and the new marts.
- Kerri Lewis: Paid Social stakeholder; approved the check list and the naming-convention approach.
- Anthony Murillo: security, and the Slack app install in the Wpromote workspace.

## 11. Key resources

- Repo: `/Users/jack.fay/paid-social-qa-buddy` (hyphens, not the older spaces folder). `CLAUDE.md` at the root is the living, detailed project memory. Read it first.
- GCP: project `prj-prd-ai-ppc-qa-pkph`, region `us-west1`, service account `ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com`.
- Test worker: `qa-buddy-worker-social-test` (currently revision 00027-hc9). Listener: `qa-buddy-listener-social-test`. Queue: `qa-buddy-runs-social-test`. Test Slack: the `@Social QA Test` app, channel `C0B6ASW9R9V`.
- Docs (in `docs/`): the pilot guide, the Wpromote cutover runbook, the check coverage map, and the standard and Peacock specs. The reader-facing ones have clean Word versions.

## 12. What is next (the immediate roadmap)

1. Tomorrow: the marts goes live. Run `verify_new_marts.py`. If it reports a config swap, repoint `BQ_META_PROJECT`, re-validate, and the unsynced clients and the gated checks come online.
2. Slack: get the bot installed in the Wpromote workspace (Anthony), and lock down the bot handle and the pilot channel.
3. Pilot: screen each AM's account, run the bot for the synced ones, and collect "this Fix or Review was wrong" feedback, which is the whole point of the pilot.
4. Production: once validated and Kerri signs off, promote to prod (the shared `@qa-buddy` app and the prod queue). The hard rule holds: never without near-certainty, and test first.
