"""SAM.gov's public contract-opportunities CSV.

Read as latin-1 (regression: the first pull saw mojibake in one record because the file is not
UTF-8). The CSV only lists notices that are still active, so a notice that has since become a
solicitation is simply absent; the pipeline keeps its own record (append-only).

Contact columns are copied as published on SAM.gov. They are public government and vendor contacts.
"""
from __future__ import annotations

import csv
import io
import sys
from collections.abc import Iterator

import requests

from . import config

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# CSV column -> notices column. The first group must exist or the run fails (schema check).
REQUIRED = {
    "NoticeId": "notice_id", "Title": "title", "Sol#": "sol_number",
    "Department/Ind.Agency": "department", "Sub-Tier": "sub_tier", "Office": "office",
    "PostedDate": "posted_date", "Type": "_type", "Description": "description", "Link": "_link",
}
# Optional: filled when the CSV has them, left empty when it does not.
OPTIONAL = {
    "NaicsCode": "naics_code", "ClassificationCode": "classification_code",
    "SetASide": "set_aside", "ResponseDeadLine": "response_deadline",
    "PopCity": "pop_city", "PopState": "pop_state", "PopZip": "pop_zip", "PopCountry": "pop_country",
    "OrganizationType": "organization_type", "AdditionalInfoLink": "additional_info_link",
    "PrimaryContactTitle": "primary_contact_title", "PrimaryContactFullname": "primary_contact_name",
    "PrimaryContactEmail": "primary_contact_email", "PrimaryContactPhone": "primary_contact_phone",
    "SecondaryContactTitle": "secondary_contact_title", "SecondaryContactFullname": "secondary_contact_name",
    "SecondaryContactEmail": "secondary_contact_email", "SecondaryContactPhone": "secondary_contact_phone",
}
EXTRA_COLUMNS = list(OPTIONAL.values())


class CsvSchemaError(RuntimeError):
    pass


def rows_from_stream(raw) -> Iterator[dict]:
    """Yields every CSV row as a dict keyed by notices-table column names (all notice types)."""
    r = csv.reader(io.TextIOWrapper(raw, encoding="latin-1", newline=""))
    header = next(r)
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise CsvSchemaError(f"CSV columns missing: {missing}")
    pick = {c: (header.index(c), name) for c, name in {**REQUIRED, **OPTIONAL}.items() if c in header}
    width = max(i for i, _ in pick.values())
    for row in r:
        if len(row) <= width:
            continue
        out = {name: row[i] for i, name in pick.values()}
        out["posted_date"] = out["posted_date"][:10]
        for extra in EXTRA_COLUMNS:
            out.setdefault(extra, "")
        yield out


def presolicitations(raw) -> tuple[list[dict], dict]:
    """(presolicitation rows, {"rows": all rows read, "presolicitations": n}). One pass."""
    total = 0
    out = []
    for row in rows_from_stream(raw):
        total += 1
        if row["_type"] == config.NOTICE_TYPE:
            out.append(row)
    return out, {"rows": total, "presolicitations": len(out)}


class _Stream(io.RawIOBase):
    """A readable byte stream over a streaming HTTP response.

    Regressions found on the first live run: (1) once the Response is garbage collected requests
    closes the stream; (2) urllib3's raw stream marks itself closed at end of data, and TextIOWrapper
    then raises "I/O operation on closed file". Reading through iter_content avoids both.
    """

    def __init__(self, response):
        self._r = response  # keep the response alive
        self._it = response.iter_content(chunk_size=1 << 20)
        self._buf = memoryview(b"")

    def readable(self):
        return True

    def readinto(self, b):
        while not len(self._buf):
            try:
                self._buf = memoryview(next(self._it))
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]  # a view, not a copy: copying here made a 220 MB read take minutes
        return n


def fetch_csv(url: str = config.CSV_URL):
    """Open the CSV as a stream (a file-like object of bytes)."""
    r = requests.get(url, stream=True, timeout=300, headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    return _Stream(r)


def count_by_year(raw) -> dict:
    """Read-only census of the CSV's presolicitations: rows by posted year, with description text.

    Note the CSV has no attachment-link column; attachments are only discoverable through the API.
    """
    from collections import Counter

    total, types = 0, Counter()
    years, with_desc, ids = Counter(), Counter(), set()
    for row in rows_from_stream(raw):
        total += 1
        types[row["_type"]] += 1
        if row["_type"] != config.NOTICE_TYPE:
            continue
        y = row["posted_date"][:4] if row["posted_date"][:4].isdigit() else "unknown"
        years[y] += 1
        with_desc[y] += int(bool(row["description"].strip()))
        ids.add(row["notice_id"])
    return {"rows": total, "types": dict(types.most_common()),
            "presolicitations": sum(years.values()), "unique_ids": len(ids),
            "by_posted_year": dict(sorted(years.items())),
            "with_description_text": dict(sorted(with_desc.items()))}
