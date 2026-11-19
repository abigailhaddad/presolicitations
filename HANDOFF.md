# presolicitations: operator guide

For whoever runs this next. Plain words; no developer background needed for most of it, but you will
need GitHub's Settings pages and a Hugging Face account.

## What it does

Every morning it reads SAM.gov's public opportunities file, keeps the **presolicitation** notices, downloads
each notice's PDF and DOCX attachments, extracts their text, and publishes everything to a Hugging Face
dataset. It never deletes anything. It does not call an LLM, so a run costs nothing.

## Where things live

| What | Where |
|---|---|
| The data (tables and a dataset card) | Hugging Face dataset `abigailhaddad/presolicitations` (not created yet) |
| The code and the schedules | GitHub repo `abigailhaddad/presolicitations` (not created yet) |
| What has been fetched | The tables themselves: a notice is "pending" until `attachments_fetched_at` is filled in |
| Run history | The Actions tab |
| A local copy while working | `dataset/` (ignored by git; never commit data) |

## What runs and when

| Workflow | Starts | Needs |
|---|---|---|
| **Daily presolicitations** (`daily.yml`) | 06:45 UTC if the variable `DAILY_ENABLED` is `true`. By hand any time; a manual run is a dry run unless you tick "publish". | secret `HF_TOKEN` |
| **Backfill presolicitations** (`backfill.yml`) | By hand only. | secret `HF_TOKEN` |
| **Tests** (`tests.yml`) | Every pull request and push to `main`. | nothing |

The daily and backfill workflows run the tests first, so a broken change stops the day's run. They share one
concurrency group, so the two can never write the dataset at the same time. GitHub's schedule can fire hours
late; that only delays the data, it loses none.

A daily run: pulls the current tables from Hugging Face, reads the CSV, adds new notices, fetches their
attachments, re-checks the live status of recent notices, runs the integrity checks, and publishes.

## What costs money

Nothing. No LLM calls, no paid APIs. GitHub Actions minutes are free on a public repo. The dataset is about 12 MB
now and grows by the text of new attachments.

## When it breaks

* **A red X in the Actions tab** is the only alert. Open the run; the failing step says why.
* **"integrity check failed, nothing written"**: the run found something wrong and published nothing. The
  message names it. The checks are: a notice or attachment that was there before is missing; duplicate IDs;
  an unknown attachment status or a malformed posted date; the number of new text rows not matching the new
  attachments; more than 35% of this run's PDFs coming back empty (the extractor is broken); more than 20% of
  live checks failing with something other than 404; fewer than 100 presolicitations in the CSV (a partial
  download). Fix the cause and re-run; nothing was lost.
* **"CSV columns missing"**: SAM.gov changed its file. Read the CSV header, update `csvsource.py`, add a
  test, push.
* **`pdftotext` not found**: the workflows install poppler if it is missing.
* **A failed run leaves no half-written files**; tables are written atomically and only after the checks pass.
* **Every bug becomes a check.** When you fix a bug, add a test that fails without the fix. The ones already
  there (links are not files, deleted and controlled attachments are never fetched, DOCX text is decoded,
  the CSV is latin-1, a notice that leaves the CSV is kept, ...) each came from a real mistake.

## The backfill

The CSV holds every presolicitation SAM.gov still lists as active, not only recent ones. The backfill loads
all of them. It runs in two phases.

1. **`meta`** (seconds, no SAM.gov API calls): adds every notice to the table with its CSV metadata. For
   backfilled notices `first_seen` is the day the backfill ran and `posted_date` keeps the real posting date.
   Existing notices keep their values and gain CSV-only columns they lacked.
2. **`fetch`** (hours, in chunks): for the next N pending notices, newest posted first, fetches attachments, text
   and live status. It stops starting new notices after `max_minutes`, publishes what it has, and the next run
   continues, because progress is stored in the data. Run it again until the summary says `pending_after: 0`.
   `offset` is normally 0.

Always run a chunk as a dry run first (leave "publish" off) and read `run_summary.json`.

**How big, from the CSV count and a 25-notice random sample (2026-11-19).** The CSV has 7,014 presolicitations:
3,889 posted in 2026, 501 in 2025, 2,624 earlier (most of them 2016 to 2019). The first manual pull covered 1,532
of them. After `meta`, 5,482 notices were pending. The sample took 69.5 seconds for 25 notices, about 2.8 seconds
each at the 0.4-second politeness delay, so the rest would take about **4.2 hours**, about six runs of 1,000
notices (about 47 minutes each). Size: the sample added about 2 MB of text, but one document in it was 2.9 million
characters; the first pull's average was about 4 KB of compressed text per notice. Expect somewhere between
about 20 MB and a few hundred MB of text in total; the other tables stay under about 10 MB. Treat both numbers
as estimates from a sample of 25: the 25 included 12 deleted attachments (of 74) and 7 controlled ones, and
older notices differ from newer ones.

The sample's notices were mostly already superseded (20 of 25) and none vanished (0 of 25), which is not what
the first manual pull's 5 vanished notices (of 1,532) suggested; do not assume either rate holds for all 5,482.

**Older presolicitations that are no longer in the active CSV** live in SAM.gov's yearly archive files
(`Archived%20Data/FY<year>_archived_opportunities.csv`, about 1 GB each). They were not counted or loaded.
`presol --csv-url <archive url> backfill meta` would load one; try one year first.

## Decisions made (Abigail, 2026-11-18)

* Names: GitHub repo and Hugging Face dataset are both `presolicitations`, under `abigailhaddad`.
* Public repo and public dataset.
* Backfill older presolicitations: yes (above).
* Contacts are published as on SAM.gov: names, emails and phone numbers are **not** stripped.
* Still to confirm: licenses. The code is MIT and the data card says CC0; both are proposed, her call.

## Personal-data scan (2026-11-19; flags only, nothing stripped)

Scanned: all 7,014 notice descriptions and contact columns, and the 1,226 text rows fetched so far (488 notices).
The 5,457 notices not yet fetched have no attachment text yet; scan again after the backfill.

* **Government and vendor contacts as published.** Contact columns: 8,720 emails (2,691 distinct), of which 8,665
  are `.gov` or `.mil` and 55 are organisations such as real-estate firms and universities; 5,702 phone numbers;
  9,639 names. Descriptions: 2,257 mention an email (3,642 mentions), 1,514 mention a phone number. Attachment
  text: 627 of 1,226 contain an email (2,541 mentions), 372 a phone number.
* **Flag: attendee lists of private individuals.** 9 attachments in 8 notices are industry-day attendee or
  sign-in lists (names, company titles, emails, phone numbers): about 542 email and 176 phone mentions. A few
  attendees give personal addresses (4 distinct consumer-domain addresses, 8 mentions, all in these lists). The agency
  published them, but they are private individuals' details rather than government contacts. Examples by file name:
  `Industry Day UNNO Attendees.pdf`, `MINSAV_Industry_Day_Attendee_List.pdf`,
  `W519TC-26-R-A061 Industry Day Attendees 22-23-Sep-2026.pdf`. Not stripped, as decided.
* **Redaction marks.** 0 FOIA-style (b)(6)/(b)(7) marks. The 11 matches were citations to 13 CFR 121.x(b)(6).
* **Social Security number patterns:** 0. **Consumer-domain emails** (gmail, yahoo, msn...): 0 in the contact
  columns and descriptions.
* "Personal residence" wording appears in notices (73 descriptions, 109 attachments); the ones read were about
  places (an official residence, patients' homes), not individuals.

## Creating the GitHub repo and the Hugging Face dataset (text only; not run)

The active `gh` account is `abigailhaddad-2`; pushing to `abigailhaddad/*` needs the other token.

```bash
cd ~/Documents/repos/presolicitations
export GH_TOKEN=$(gh auth token -u abigailhaddad)
gh repo create abigailhaddad/presolicitations --public --source . --remote origin \
  --description "Daily append-only mirror of SAM.gov presolicitation notices and attachment text"
git push -u origin main
gh variable set DAILY_ENABLED --body false -R abigailhaddad/presolicitations   # leave off until a dry run looks right
gh secret set HF_TOKEN -R abigailhaddad/presolicitations                      # paste a write token for the dataset

# Hugging Face: create the dataset, then upload the seeded + backfilled tables and the card
uv run python -c "from huggingface_hub import HfApi; HfApi().create_repo('abigailhaddad/presolicitations', repo_type='dataset', exist_ok=True)"
HF_TOKEN=... uv run presol --data-dir dataset/data push

# First checks: dry runs from Actions (Actions tab -> run workflow, leave "publish" off), read run_summary.json.
# Then: gh variable set DAILY_ENABLED --body true -R abigailhaddad/presolicitations
```

If `dataset/` is gone, rebuild it: `presol seed --list list.json --final final3.json` (the first manual pull's
files) then `presol backfill meta`.
