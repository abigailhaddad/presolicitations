# presolicitations

A daily, append-only mirror of SAM.gov **presolicitation** notices: the notice, every attachment
SAM.gov lists, and the text of the PDF and DOCX attachments. The data lives on Hugging Face
(`abigailhaddad/presolicitations`); this repo is the code that collects it. Nothing in it calls an LLM,
so a run costs nothing.

*Names are as chosen by Abigail; the repo has not been created on GitHub or Hugging Face yet.*

## How it works

1. **Read SAM.gov's public opportunities CSV** and keep the rows whose Type is `Presolicitation`.
2. **For each new notice,** ask SAM.gov's API for its attachment list, download the PDFs and DOCX files,
   and extract their text (`pdftotext -layout`; DOCX XML is decoded). Links, deleted, controlled-access,
   oversized and other-format attachments are recorded and never downloaded.
3. **Check each recent notice's live status** (latest version? archived? still exists?).
4. **Run integrity checks.** If any fails, nothing is written and nothing is published.
5. **Publish** the tables and a generated dataset card to Hugging Face (only with `--publish`).

It never deletes anything. A notice that leaves the CSV (usually because it became a solicitation) stays,
marked with the date it left. A notice SAM.gov stops recognising stays, marked `vanished`.

## Tables (on Hugging Face under `data/`)

| Table | Files | Notes |
|---|---|---|
| `notices` | `notices/notices-<posted year>.parquet` | Metadata, contacts as published, live status. Small. |
| `attachments` | `attachments/attachments-<posted year>.parquet` | One row per listed attachment and what was done with it. No text. |
| `texts` | `texts/texts-<posted year>-<run date>.parquet` | Extracted text. A new file each run; never rewritten. |
| `status_history` | `status_history.parquet` | Every observation over time. |

"Year" is the **posted** year, never the year the notice was first collected: `first_seen` and
`posted_date` are separate columns because a backfilled notice has an old posted date and a new first-seen date.

## Commands

```bash
uv sync
uv run pytest -q                                  # offline; no network
uv run presol csv-counts                          # read-only census of the CSV
uv run presol seed --list L.json --final F.json   # one-time: build tables from the first manual pull
uv run presol daily --dry-run --limit 5           # live, read-only on SAM.gov; writes only local files
uv run presol --pull daily --publish              # what the workflow runs on schedule
uv run presol backfill meta                       # load every presolicitation in the CSV (no API calls)
uv run presol --pull backfill fetch --limit 1000 --max-seconds 16200 --publish
uv run presol check                               # integrity checks on the tables on disk
uv run presol exclude                             # withhold industry-day attendee lists from tables on disk (privacy.py)
uv run python scripts/scan_personal_data.py dataset/data   # personal-data scan of what would be published
```

Dry run is the default everywhere. `--publish` uploads; `--dry-run` overrides it. Publishing needs
`HF_TOKEN` in the environment; the token is never read from a file.

See [HANDOFF.md](HANDOFF.md) for what runs when, what to do when it breaks, the backfill, and the
commands to create the GitHub repo and the Hugging Face dataset.

## What is not published

Industry-day attendee and sign-in lists list private individuals' names and emails, and nothing here
needs them. Their attachment rows stay (`status = excluded`, with `excluded_reason`); their text is not
downloaded or published (`src/presolicitations/privacy.py`). Government points of contact are kept as
published on SAM.gov.

## Licenses

Code: MIT. Data: CC0 (the notices are public records posted on SAM.gov).
