# Paid Social QA Buddy Bot Pilot Guide

Welcome to the pilot. This guide walks you through using the QA Buddy Bot to check
your Meta campaigns. Read it once before your first run and keep it handy for the
filling-in and troubleshooting sections.

> **What it is:** a Slack bot that checks a Meta campaign against the values you
> expect and writes a verdict (Pass / Fix / Review) on every row of your QA sheet,
> then posts a summary back in Slack. It does in under a minute what you would
> otherwise do by hand, line by line.

> **This is a beta.** Please run it alongside your normal QA for now, not as the
> only gate, while we tune it. Your feedback is the whole point of the pilot.

---

## How it works in one minute

1. You make your own copy of a QA sheet and type in what each setting *should* be.
2. You @-mention the bot in Slack with your account ID, campaign ID, and your
   sheet link.
3. The bot pulls the campaign's real settings, compares them to what you entered,
   writes a verdict on each row, and posts a summary in the channel.

That is the whole loop. The rest of this guide is detail.

---

## The five verdicts

Every row gets one of these. This is the bot's whole vocabulary.

| Verdict | What it means | What you do |
|---|---|---|
| **Pass** | The campaign matches what you entered. | Nothing. |
| **Fix** | It does **not** match. The bot shows you the actual value it found. | Go correct the campaign (or fix your expected value if you mistyped it). |
| **Review** | The bot could not confirm this one automatically, so it is asking you to check it by hand. It leaves a note with whatever it does know. | Eyeball that row yourself. |
| **N/A** | You left that row blank, so the bot skipped it. | Nothing. Fill it in if you want it checked. |
| **Error** | Something was off with that row itself (rare). | Check the row, or ask in the channel. |

**The key thing to know:** the bot is built to **never tell you something Passed
when it is not sure.** When it cannot confirm a value, it returns **Review**, not
Pass. So a Review is the bot being careful, not the bot failing. You will see some
Reviews on every run, and that is by design (more on why below).

---

## What the bot checks

You do not have to fill in every row. The bot covers these areas, and you choose
which ones to check by filling in the matching rows:

- **Campaign settings:** objective, buying type, budget, bid strategy.
- **Ad set targeting:** age, gender, location, audiences, audience exclusions,
  placements, flight start and end dates, optimization goal, attribution,
  spend floors and caps.
- **Ads:** status, destination URL, call-to-action button, creative dimensions.
- **Ad copy (checked by AI):** spelling in the body, headline, and description.
- **Naming conventions:** confirms your ad set names and ad names contain the
  parts you expect.

---

## One-time setup (do this first)

**1. Make your own copy of the template.**
Open the template, then **File > Make a copy**:

`[TODO: template link: https://docs.google.com/spreadsheets/d/1rTfqYA3xjvQyHwnsEj9c9gF_exzjvIz1Aq6Sbwe1TSQ/edit]`

> Work only in **your copy**. Never use the template link itself as your
> `Sheet_url`, always use your own copy's link, so the bot writes to your sheet
> and not the shared template.

**2. Share your copy with the bot, as Editor.**
The bot needs write access to put verdicts in your sheet. Share your copy with:

```
ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com
```

This is the thing people forget most often. If you skip it, the bot will tell you
to share the sheet and stop. It will not guess.

---

## Step 1: Tell the bot what you expect

In your copy, fill in the **"Builder Input"** column with the value each setting
*should* have for this campaign. Leave a row blank to skip it (it comes back N/A).

| If the row is about... | Enter... | Example |
|---|---|---|
| Objective, buying type, optimization goal | the setting name as it reads in Ads Manager | `Traffic`, `Auction`, `Link clicks` |
| Status | `Active` or `Paused` | `Active` |
| Start / end dates | a date as `MM/DD/YYYY` | `06/01/2026` |
| Ages | the number | `18`, `65` |
| Genders | `All`, `Men`, or `Women` | `All` |
| Countries / location | the country code(s) or name(s) | `US`, `CA` |
| Budget / spend amounts | the amount | `500` |
| Call to action | the button text | `Learn More` |
| Destination URL | the full URL, or just the domain | `example.com` |
| Presence rows (audiences, exclusions, spend caps) | `Yes` or `No` | `Yes` |
| Naming rows (ad set name, ad name) | the expected name or its key parts, comma-separated | `Peacock, FBIG, ACQ` |
| Spelling rows | leave blank, the bot reads your ad copy on its own | *(blank)* |
| Anything you do not want checked | leave it blank | *(blank)* |

> **Naming rows changed:** these used to say "Yes/No." Now you enter the expected
> name or its key parts instead, and the bot confirms each ad set and ad name
> contains them. If you leave the old "Yes/No" in, the bot will gently remind you
> to update that row.

---

## Step 2: Run it in Slack

In `[TODO: pilot channel name]`, @-mention the bot with these lines (each on its
own line):

```
[TODO: bot handle, e.g. @Social QA Test]
Account_id: <your Meta ad account id>
Campaign_id: <your Meta campaign id>
Sheet_url: <the link to your copy>
```

A few things so it works the first time:
- **Make the @-mention a real mention** (type `@`, then pick the bot from Slack's
  autocomplete). Plain text will not trigger it.
- **One field per line**, exactly as above.
- *(Peacock only: if you are QA'ing a Peacock campaign, add one more line that just
  says `peacock`. Most campaigns do not need this.)*

The bot replies within a few seconds to confirm it got the request, then posts the
full results when it finishes (usually under a minute).

---

## Step 3: Read your results

You get results in two places:

- **In your sheet:** a verdict on each row, plus a short note (for example, the
  actual value it found on a Fix, or what to check on a Review).
- **In Slack:** a one-line summary, `Pass | Fix | Review | N/A | Error`, with any
  Fixes called out by name.

Work the **Fixes** first (those are real mismatches), then glance through the
**Reviews** (those are the rows the bot wants a human on).

---

## What is normal in the beta

A few things will look like problems but are not. Knowing them up front saves
confusion:

- **Some rows always come back Review right now.** Settings like budget,
  conversion event, optimization goal, attribution, spend caps, and audiences are
  not yet synced into our data for every client. Those rows return **Review with a
  note**, not because anything is wrong, but because the bot will not guess. As
  that data gets added, these turn automatic on their own. For your first run, pick
  a client whose data is well-synced for the smoothest experience.
- **Spelling is AI-checked on up to the first 25 ads.** If your campaign has more,
  the bot tells you in the note to eyeball the rest. This keeps the run fast.
- **A couple of checks are manual on purpose** (for example, confirming the actual
  1x1 and 9x16 creative). The bot returns Review with instructions rather than
  pretending to verify something it cannot see.
- **You will see Reviews on every run.** That is the safety design, not a fault.

---

## If something goes wrong

The bot tries to tell you exactly what to fix. The common ones:

| The bot says something like... | What to do |
|---|---|
| "...isn't shared with the bot's service account" | Share **your copy** with the service-account email (Editor) from setup step 2. |
| "Google Sheet not found" | Check the `Sheet_url`, it should be your copy's link. |
| "...couldn't be mapped to a client" | Double-check the `Account_id`. |
| Summary is all zeros (`Pass 0 \| Fix 0 \| ...`) | You are probably running on a blank sheet. Start from a fresh copy of the template, which has the hidden check column the bot reads. |

If you are stuck, post in the channel with your campaign ID and a screenshot.

---

## Do and don't

- **Do** work in your own copy of the template.
- **Do** fill in only the rows you actually want checked.
- **Don't** paste the template link as your `Sheet_url`. Always use your own copy.
- **Don't** treat the bot as your only QA yet, it is a beta. Keep your normal
  process running alongside it.
- **Do** trust a Fix to be flagging a real difference from what you entered, and a
  Review to be a genuine "please look at this," not noise.

---

## Feedback (the reason for the pilot)

Found a verdict that looks wrong, or something confusing? Tell us in this channel
(the same place you run the bot) with the **campaign ID** and a **screenshot**.
What helps us most is a clear "this Fix or Review was wrong, and here's why." That
is the kind of thing that lets us tune it.

---

## Cheat sheet

- **Template to copy:** `[TODO: template link]`
- **Share your copy with (Editor):** `ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com`
- **Run it:** `[TODO: bot handle]` + `Account_id:` + `Campaign_id:` + `Sheet_url:` (one per line)
- **Verdicts:** Pass = matches, Fix = does not match, Review = check by hand, N/A = left blank, Error = bad row
- **Golden rule:** the bot never auto-Passes when unsure. A Review means "look at this," not "it failed."
