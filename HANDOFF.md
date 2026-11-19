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
* Contacts are published as on SAM.gov: names, emails and phone numbers are **not** stripped, except that industry-day attendee lists are withheld (see below).
* Still to confirm: licenses. The code is MIT and the data card says CC0; both are proposed, her call.

## Personal-data scan (2026-11-19; flags only, nothing stripped)

Scanned: all 7,014 notice descriptions and contact columns, and the 1,226 text rows fetched so far (488 notices).
The 5,457 notices not yet fetched have no attachment text yet; scan again after the backfill.

* **Government and vendor contacts as published.** Contact columns: 8,720 emails (2,691 distinct), of which 8,665
  are `.gov` or `.mil` and 55 are organisations such as real-estate firms and universities; 5,702 phone numbers;
  9,639 names. Descriptions: 2,257 mention an email (3,642 mentions), 1,514 mention a phone number. Attachment
  text: 627 of 1,226 contain an email (2,541 mentions), 372 a phone number.
* **Decision (2026-11-19): industry-day attendee lists are not published.** 9 attachments in 8 notices of the first
  pull were attendee or sign-in lists of private individuals (names, company titles, emails, phone numbers), and the
  backfill sample added 2 more rows of the same files under variant names (11 rows, 10 notices, 7 distinct files in
  all). Abigail: "we dont need those documents." `privacy.py` withholds them: the attachment ROW stays (name, notice,
  `status = excluded`, `excluded_reason`), the text is neither downloaded (when the file name gives it away) nor
  published. Rules: the name says attendee, attendance, registrant, roster, industry-day list or sign-in sheet; or the
  text has 20+ distinct emails at 5+ per 1,000 characters with under 20% at .gov/.mil. `presol exclude` applies the rule to
  tables already on disk (idempotent; the only place text rows are removed). After it: 0 text rows for an excluded
  attachment, 0 consumer-domain (gmail, yahoo, msn...) email mentions in the published text, 0 SSN-shaped numbers
  (`scripts/scan_personal_data.py`). A looser check (10+ distinct emails, under half .gov/.mil) finds two other
  files, both contractor specifications of 800 KB to 2.9 MB with a few dozen contacts; neither is a list of people.
  A blank "Industry Day Registration Form" (.docx, only the agency's three submission addresses) is published.
  Government points of contact in notices and attachments stay as published (Abigail: "we dont need to drop public data").
* **Redaction marks.** 0 FOIA-style (b)(6)/(b)(7) marks. The 11 matches were citations to 13 CFR 121.x(b)(6).
* **Social Security number patterns:** 0. **Consumer-domain emails** (gmail, yahoo, msn...): 0 in the contact
  columns and descriptions.
* "Personal residence" wording appears in notices (73 descriptions, 109 attachments); the ones read were about
  places (an official residence, patients' homes), not individuals.

## State of play (2026-11-19)

* **GitHub:** `abigailhaddad/presolicitations`, public, created and pushed with the `abigailhaddad` account (the
  active `gh` account is `abigailhaddad-2`, so use `GH_TOKEN=$(gh auth token -u abigailhaddad)` for that repo).
  The Tests workflow passes on GitHub. No secrets or variables are set; the scheduled daily run stays off until
  `DAILY_ENABLED` is `true`.
* **Hugging Face:** `abigailhaddad/presolicitations` does **not exist yet**. Nothing has been uploaded. Create it and
  upload only once the local backfill has finished and `presol check` passes (commands below).
* **Local backfill:** `scripts/run_backfill.sh` is running in the background (see below). It writes only to the
  git-ignored `dataset/`.

## Commands Abigail runs herself (they need her tokens)

```bash
cd ~/Documents/repos/presolicitations

# 1. Secret for the daily and backfill workflows (a Hugging Face WRITE token; gh asks for the value, nothing is echoed)
export GH_TOKEN=$(gh auth token -u abigailhaddad)
gh secret set HF_TOKEN -R abigailhaddad/presolicitations

# 2. Create the dataset and put the card up (the card says what the dataset is and what is withheld)
.venv/bin/hf auth login                      # or: export HF_TOKEN=<write token> for this shell only
.venv/bin/hf repo create abigailhaddad/presolicitations --repo-type dataset
.venv/bin/hf upload abigailhaddad/presolicitations dataset/README.md README.md --repo-type dataset

# 3. When the local backfill has finished (tail work/backfill.log shows DONE) and this prints "ok": true:
.venv/bin/python -m presolicitations check
HF_TOKEN=<write token> .venv/bin/python -m presolicitations --data-dir dataset/data push

# 4. Read the first runs before the schedule is switched on: Actions tab -> "Daily presolicitations" -> Run workflow
#    (leave "publish" off), open run_summary.json. Then:
gh variable set DAILY_ENABLED --body true -R abigailhaddad/presolicitations
```

If the exclusion rules ever change after data is on the Hub, the pipeline will not delete the old files there
(it never deletes on the Hub); remove them with `hf repo delete-files` or the web UI.

## The local backfill

Started 2026-11-19 with `nohup scripts/run_backfill.sh`. Chunks of 100 notices (about 5 minutes each), newest
posted first. Progress, one line per chunk, in `work/backfill.log`:

```bash
tail -n 5 ~/Documents/repos/presolicitations/work/backfill.log     # latest chunks
pgrep -f scripts/run_backfill.sh                                   # running? (lowest pid is the script)
kill $(cat work/backfill.pid)                                      # stop it; start again later with the same command
```

It stops on its own when nothing is pending (`DONE`), when free disk falls below 10 GB, when a chunk fails (the
integrity check aborts that chunk and writes nothing from it), or after three chunks in a row with no progress.
Every finished chunk is already saved, and restarting continues where it stopped. If `work/backfill.lock` is left
behind after a crash and no script is running, delete that directory.

If `dataset/` is gone, rebuild it: `presol seed --list list.json --final final3.json` (the first manual pull's
files), `presol backfill meta`, then `presol exclude`.
