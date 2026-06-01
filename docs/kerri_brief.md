# Paid Social QA Bot — status + what I need from you

**One line:** the Social QA bot is **working end-to-end** — a builder `@-mentions` it in Slack with the account/campaign/sheet, and it reads the campaign's real settings from Meta data, checks them against the builder's expected values, **writes Pass/Fix/Review into the QA sheet, and posts a summary in the thread** — just like the Search QA Buddy, now for Meta.

**Live demo:** I can run it in front of you — `@Social QA Test` in `#social-qa-buddy-testing` → it fills the sheet + replies with a summary in ~10 seconds.

## What it checks today (against your template)

Legend: ✅ automated · 🔨 building · 🤖 AI text check · ✋ manual (bot flags for human)

**Campaign level**
- Campaign Objective ✅ · Buying Type ✅ · Bid Strategy ✅ · Budget 🔨

**Ad set level**
- Event Name ✅ — *the "purchase event" vs "purchase" safeguard (the Peacock incident); strict match, escalates to Review if unsure*
- Start Date ✅ · End Date ✅ · Age Min ✅ · Age Max ✅ · Gender ✅ · Location ✅
- Spend Minimum ✅ · Spend Maximum ✅ — *also flags if a setting is there when it shouldn't be*
- Interests / Custom Audiences ✅ · Audience Exclusions ✅ · Optimization for Ad Delivery ✅ · Attribution Setting ✅
- Conversion Event Location 🔨 · Placements 🔨 · Ad Sets that Ads Should Be Live In 🔨
- Name – Aligned with Conventions ✋

**Ad level**
- Ad Status ✅ · Call To Action ✅ · Landing Page URL ✅
- Ad Copy / Headline / Description spelling 🤖 · Correct Creative 1×1 & 9×16 ✋ · Name – Aligned with Conventions ✋
- Facebook Page 🔨 · Instagram Account 🔨 · Site Links 🔨 · Advantage+ Creative 🔨 · Display URL 🔨 · Tracking Pixel 🔨 · UTM Parameters 🔨

**The bot never guesses.** When it isn't sure (a value it can't read, ambiguous input, a manual-only item), it returns **Review** and asks a human — never a silent Pass. Final sign-off always stays with the team.

## What I need from you (the 3 things that unblock the rest)

1. **Bless the check list.** Your template's `Check_ID` column is blank today, and the bot keys off it. I've drafted a proposed ID for every row — I just need you to confirm/adjust so we can fill that column in the canonical template. *(Once that's in, builders can run real QAs.)*

2. **Naming conventions.** Brandon said the "Name – Aligned with Conventions" rules get tailored per manager/client. If there's an encodable pattern, I'll automate it; otherwise it stays a manual-review row. Your call on the rule.

3. **MVP priority.** Of the 🔨 "building" checks, which are must-haves for a pilot vs. nice-to-have later? That tells me what to build next.

## What's already settled (no action needed)
- The architecture (shared listener, Meta data via BigQuery, same Slack/verdict model as Search), the value lists (objective, bid strategy, CTA, etc. — pulled from your template's data-validation tab), and the deploy. It's running on test infrastructure now; prod is a flip of a switch once you've signed off on the checks.
