"""The Hugging Face dataset card. Every number is computed from the tables, none is typed."""
from __future__ import annotations

import pandas as pd

from . import config, store

DESC = {
    "text": "text extracted",
    "no_text": "fetched, no text came out (probably a scan; not OCR'd)",
    "link": "external link, no file",
    "deleted": "deleted by the agency",
    "controlled": "controlled access (needs a SAM.gov login)",
    "skipped_format": "not a PDF or DOCX (spreadsheets, slides, video, ...), not read",
    "big": f"over {config.MAX_FILE_BYTES // 1_000_000} MB, not downloaded",
    "excluded": "withheld on purpose: an industry-day attendee list (personal contact data of private individuals)",
    "download_error": "download failed",
    "extract_error": "downloaded, but the file would not open",
}


def _n(x) -> str:
    return f"{int(x):,}"


def render(tables: dict[str, pd.DataFrame], *, as_of: str, text_row_count: int | None = None) -> str:
    n, a = tables["notices"], tables["attachments"]
    years = n["posted_date"].map(store.year_of)
    by_year = n.groupby(years).agg(
        notices=("notice_id", "size"), distinct_sol=("sol_number", "nunique"),
        superseded=("live_latest", lambda s: int((s == False).sum())),  # noqa: E712
        fetched=("attachments_fetched_at", lambda s: int(s.notna().sum())),
    )
    att_year = a["notice_id"].map(dict(zip(n["notice_id"], years)))
    att_n = a.groupby(att_year).size()
    with_att = a[a["kind"].eq("file")].groupby(att_year[a["kind"].eq("file")]).size()
    year_rows = "\n".join(
        f"| {y} | {_n(r.notices)} | {_n(r.distinct_sol)} | {_n(r.superseded)} | {_n(r.fetched)} | {_n(att_n.get(y, 0))} |"
        for y, r in by_year.iterrows()
    )
    status = a["status"].value_counts()
    status_rows = "\n".join(f"| {s} | {_n(status.get(s, 0))} | {DESC[s]} |" for s in DESC if status.get(s, 0))
    latest = int((n["live_latest"] == True).sum())  # noqa: E712
    superseded = int((n["live_latest"] == False).sum())  # noqa: E712
    unchecked = int(n["live_latest"].isna().sum())
    vanished = int(n["vanished"].sum())
    left = int((~n["in_csv_now"]).sum())
    seed_n = int((n["source"] == "seed").sum())
    back_n = int((n["source"] == "backfill").sum())
    daily_n = int((n["source"] == "daily").sum())
    has_text = int(a["has_text"].sum())
    first, last = n["posted_date"].min(), n["posted_date"].max()
    texts_line = (f"{_n(text_row_count)} text rows are published" if text_row_count is not None
                  else f"{_n(has_text)} attachments have text")
    return f"""---
license: cc0-1.0
pretty_name: SAM.gov Presolicitations
language:
- en
tags:
- government
- procurement
- sam-gov
- federal-contracting
configs:
- config_name: notices
  data_files: data/notices/*.parquet
  default: true
- config_name: attachments
  data_files: data/attachments/*.parquet
- config_name: texts
  data_files: data/texts/*.parquet
- config_name: status_history
  data_files: data/status_history.parquet
---

# SAM.gov presolicitations

Presolicitation notices from SAM.gov (federal contract opportunities), their attachments, and the text
of those attachments. Updated daily by a GitHub Actions pipeline: `{config.GITHUB_REPO}`.

**As of {as_of}.** {_n(len(n))} notices posted {first} to {last}; {_n(len(a))} attachments;
{texts_line}.

## What is in it

* `notices`: one row per notice, with SAM.gov's metadata (agency, office, solicitation number, NAICS,
  set-aside, place of performance, response deadline, description) and the live status this pipeline
  last saw. Contact names, emails and phone numbers are as published on SAM.gov.
* `attachments`: one row per attachment SAM.gov lists, with what was done with it (see the status table).
* `texts`: the extracted text, one row per attachment that was read. Kept separate so `notices` stays small.
* `status_history`: every observation of a notice over time: first seen, live status checks, when it left
  the SAM.gov CSV, when SAM.gov stopped recognising it.

## Coverage

| Posted year | Notices | Distinct solicitation numbers | Superseded versions | Notices with attachments fetched | Attachments listed |
|---|---|---|---|---|---|
{year_rows}

"Attachments listed" counts only notices whose attachments have been fetched. A notice outside the
"fetched" column has not been looked at yet, so zero attachments there means *not yet*, not *none*.
Notices the pipeline has not yet live-checked have an empty `live_latest`, so the superseded column
undercounts until the checks catch up.

* **Where the notices came from.** {_n(seed_n)} were collected by the first manual pull, {_n(back_n)}
  were loaded from SAM.gov's current opportunities CSV by a backfill, and {_n(daily_n)} were collected by
  the daily run.
* **`first_seen` is not `posted_date`.** `posted_date` is when SAM.gov posted the notice. `first_seen` is the
  day this pipeline first collected it. For backfilled notices they are far apart.
* **Superseded versions are kept.** {_n(superseded)} notices are no longer the latest version of their
  notice; {_n(latest)} are the latest; {_n(unchecked)} have not had a live check yet. Several notice IDs can
  share one solicitation number, so count procurements by `sol_number`, not by rows.
* **Append-only.** Notices that leave the CSV (usually because they became solicitations) stay, with
  `in_csv_now = false` and `left_csv_on`: {_n(left)} so far. Notices SAM.gov no longer recognises stay,
  with `vanished = true` and the date first seen: {_n(vanished)} so far.

## Attachments: what was and was not read

| Status | Attachments | Meaning |
|---|---|---|
{status_rows}

Only PDF and DOCX files are read. PDFs are read with `pdftotext -layout`; scanned pages are not OCR'd,
so a scan has no text. DOCX text has XML entities decoded; Word field codes (a table of contents, for
example) can leave literal `&quot;` in the text.

## Known limits

* The source CSV lists only notices that are still presolicitations. A notice that already became a
  solicitation before it was first collected is not here, so older notices are undercounted. The
  daily collection is the reliable record from its start date on.
* Controlled attachments ({_n(status.get("controlled", 0))}) cannot be fetched without a SAM.gov login.
* Attachments the agency deleted ({_n(status.get("deleted", 0))}) are listed by SAM.gov but cannot be downloaded.
* {_n(status.get("excluded", 0))} attachments are listed but their text is not published: industry-day
  attendee and sign-in lists, which name and give the emails of employees of companies who signed up for
  an event. They are private individuals' contact details, and nothing here needs those documents, so
  the row stays (`status = excluded`, with `excluded_reason`) and the text does not. Government points of
  contact in notices are public records and are kept as published.
* {_n(status.get("no_text", 0))} PDFs came back with no text and are probably scans.
* Rows seeded by the first manual pull have date-only timestamps, and their `provenance` column says
  where a status was inferred from older records.
* This is a mirror of public notices, not a legal record. Check the notice on SAM.gov
  (`sam_url`) before relying on a detail.

## License

The notices and attachments are public records posted on SAM.gov. This compilation is dedicated to
the public domain under CC0 1.0.
"""
