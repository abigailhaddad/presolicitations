"""Checks that must pass before anything is written or published. Any failure aborts the run."""
from __future__ import annotations

import re

import pandas as pd

from . import classify, config


class IntegrityError(RuntimeError):
    pass


def check(before: dict[str, pd.DataFrame], after: dict[str, pd.DataFrame], stats: dict,
          csv_counts: dict | None = None) -> list[str]:
    bad: list[str] = []
    bn, an = before["notices"], after["notices"]
    ba, aa = before["attachments"], after["attachments"]

    # Append-only: nothing that was there may be gone.
    lost = set(bn["notice_id"]) - set(an["notice_id"])
    if lost:
        bad.append(f"{len(lost)} notices present before are missing now, e.g. {sorted(lost)[:3]}")
    lost_a = (set(zip(ba["notice_id"], ba["resource_id"])) - set(zip(aa["notice_id"], aa["resource_id"])))
    if lost_a:
        bad.append(f"{len(lost_a)} attachments present before are missing now")
    if len(after["status_history"]) < len(before["status_history"]):
        bad.append("status_history shrank")

    # Uniqueness.
    if an["notice_id"].duplicated().any():
        bad.append(f"{int(an['notice_id'].duplicated().sum())} duplicate notice_id")
    if aa.duplicated(["notice_id", "resource_id"]).any():
        bad.append("duplicate (notice_id, resource_id) in attachments")
    if len(aa) and not set(aa["notice_id"]) <= set(an["notice_id"]):
        bad.append("attachments reference a notice that is not in notices")

    # Values.
    unknown = set(aa["status"].dropna()) - set(classify.STATUSES)
    if unknown or aa["status"].isna().any():
        bad.append(f"attachment status missing or unknown: {sorted(unknown)}")
    if len(an) and not an["posted_date"].fillna("").map(lambda d: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))).all():
        bad.append("a notice has a missing or malformed posted_date")
    if (an["first_seen"] > an["last_seen"]).any():
        bad.append("first_seen is after last_seen for some notice")

    # Texts written this run match the attachments added this run.
    if len(after["texts"]) != stats.get("attachment_text", 0) + stats.get("attachment_no_text", 0):
        bad.append(f"new texts ({len(after['texts'])}) do not match new text/no_text attachments "
                   f"({stats.get('attachment_text', 0) + stats.get('attachment_no_text', 0)})")

    # Extraction health.
    pdfs = stats.get("pdf_fetched", 0)
    if pdfs >= 20:
        pct = 100 * stats.get("pdf_empty", 0) / pdfs
        if pct > config.MAX_EMPTY_PDF_PCT:
            bad.append(f"{pct:.0f}% of {pdfs} PDFs fetched this run came back empty "
                       f"(limit {config.MAX_EMPTY_PDF_PCT:.0f}%): extractor broken?")
    live_n = stats.get("live_checks", 0) + stats.get("live_errors", 0)
    if live_n >= 20 and 100 * stats.get("live_errors", 0) / live_n > config.MAX_LIVE_ERROR_PCT:
        bad.append(f"{stats['live_errors']} of {live_n} live checks failed with something other than 404")
    if csv_counts is not None and csv_counts["presolicitations"] < config.MIN_CSV_PRESOL_ROWS:
        bad.append(f"the CSV held only {csv_counts['presolicitations']} presolicitation rows "
                   f"(minimum {config.MIN_CSV_PRESOL_ROWS}): partial download?")
    return bad


def assert_ok(*args, **kw) -> None:
    bad = check(*args, **kw)
    if bad:
        raise IntegrityError("; ".join(bad))
