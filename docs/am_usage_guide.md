# Paid Social QA Bot — How to use it (for Account Managers)

The QA bot checks a Meta campaign against the values you expect, writes a verdict
(Pass / Fix / Review) on each row of your QA sheet, and posts a summary in Slack.
It is meant to replace the manual line-by-line QA.

> **Beta note:** this is a test version. Use it on a **copy** of the template, not
> your live QA sheet.

## 1. Set up your QA sheet (do this first)
1. **Make a copy** of the QA template (open it, then File > Make a copy):
   `https://docs.google.com/spreadsheets/d/1rTfqYA3xjvQyHwnsEj9c9gF_exzjvIz1Aq6Sbwe1TSQ/edit`
   > ⚠️ Work only in **your copy**. **Never paste the template link above as your
   > `Sheet_url`** — always use your own copy's link, so the bot writes to your
   > sheet and not the shared template.
2. **Share the copy with the bot**, as **Editor**:
   ```
   ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com
   ```
   This is the #1 thing people forget. If it is not shared, the bot will tell you
   to share it and stop (it will not guess).
3. **Fill in the "Builder Input" column** with the expected value for each row
   (what the campaign *should* be). Leave a row blank to skip it (shows as N/A).
   - **Naming rows:** enter the expected name or its key parts, comma-separated
     (e.g. `Peacock, FBIG, ACQ`). The bot confirms each ad set / ad name contains
     them. (Do **not** put Yes/No here anymore.)

## 2. Run the QA
In _[TODO Jack: which Slack channel]_, mention the bot with these lines:
```
@Social QA Test
Account_id: <the Meta ad account id>
Campaign_id: <the Meta campaign id>
Sheet_url: <link to your QA sheet copy>
peacock          <- include this line ONLY if it is a Peacock campaign
```
The bot replies within a few seconds to acknowledge, then posts the results when
it finishes (usually under a minute).

## 3. Read the results
The bot writes a verdict on each row and posts a summary `Pass | Fix | Review | N/A | Error`:
- **Pass** — the campaign matches what you entered.
- **Fix** — it does not match; the bot shows you the actual value.
- **Review** — the bot could not verify it automatically; check it manually (see below).
- **N/A** — you left that row blank.
- **Error** — something was off with that row (rare).

**About Review (important):** some settings (budget, optimization goal, conversion
event, attribution, spend caps) are not yet synced into our data for every client.
Those rows come back **Review with a note** — that is expected, not a bug, and will
become automatic as those fields get added. _For your first run, pick a client
whose data is fully synced for the best experience._

## 4. If something goes wrong
The bot tells you exactly what to fix:
| Message | What to do |
|---|---|
| "isn't shared with the bot's service account" | Share your sheet (Editor) with the email in step 1 |
| "Google Sheet not found" | Check the `Sheet_url` |
| "couldn't be mapped to a client" | Double-check the `Account_id` |

## 5. Feedback
Found a wrong verdict or something confusing? Post it in _[TODO Jack: feedback
channel]_ with the campaign id + a screenshot. The more "this Fix/Review was
wrong" reports, the faster we tune it.
