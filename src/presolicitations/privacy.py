"""What is not published: attachments that are mostly personal contact data of private individuals.

Government points of contact in notices are public records and are kept as published. An industry-day
attendee list is different: it names and gives the email addresses (and sometimes personal gmail or
msn addresses and phone numbers) of employees of companies who signed up to attend an event. They are
private individuals, and nothing in this dataset needs those documents, so the attachment ROW is kept
(name, notice, status, reason) and the TEXT is neither fetched nor published.

Two rules, either one excludes:

* name: the file name says it is an attendee, attendance, registrant, roster, industry-day list or
  sign-in sheet;
* text: at least 20 distinct email addresses, at least 5 per 1,000 characters, and fewer than 20% of
  them at .gov or .mil. A solicitation or a specification has a few contacts in a lot of text, and
  agency contact lists are mostly .gov or .mil, so neither matches.

Found on the first pull: 9 attachments in 8 notices (6 distinct files; the same list is attached to
several notices; two of the nine are blank sign-in sheets with no extractable text).
"""
from __future__ import annotations

import re

EXCLUDED_REASON = "industry-day attendee list (personal contact data of private individuals)"

_NAME = re.compile(
    r"attendee|attendance|registrant|\broster\b|industry[ _-]*day[ _-]*list|sign[ _-]*in[ _-]*sheet",
    re.I,
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_GOV = re.compile(r"\.(gov|mil)$", re.I)

MIN_DISTINCT_EMAILS = 20
MIN_EMAILS_PER_1000_CHARS = 5.0
MAX_GOV_SHARE = 0.20


def by_name(name: str | None) -> bool:
    return bool(name and _NAME.search(name))


def email_stats(text: str | None) -> tuple[int, int, float]:
    """(distinct emails, of which .gov/.mil, distinct emails per 1,000 characters)."""
    if not text:
        return 0, 0, 0.0
    emails = {e.lower() for e in _EMAIL.findall(text)}
    gov = sum(1 for e in emails if _GOV.search(e))
    return len(emails), gov, len(emails) / max(len(text), 1) * 1000


def by_text(text: str | None) -> bool:
    n, gov, density = email_stats(text)
    return n >= MIN_DISTINCT_EMAILS and density >= MIN_EMAILS_PER_1000_CHARS and gov / max(n, 1) < MAX_GOV_SHARE


def reason(name: str | None, text: str | None = None) -> str | None:
    """EXCLUDED_REASON if either rule fires, else None."""
    return EXCLUDED_REASON if by_name(name) or by_text(text) else None
