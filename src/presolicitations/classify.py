"""Decide, from SAM.gov's attachment record alone, what to do with an attachment.

Every rule here is a trap the first pull fell into (see review/ in the research folder):

* `type == "link"` is an external URL (FedConnect, PIEE, ...). It has no name and no file; 815 of the
  first pull's attachments were these, and a name-based filter mislabelled them "skipped extension".
* `deletedFlag == "1"` is an attachment the agency removed. The API still lists it, and downloading it
  returns HTTP 400 "The resource has been deleted".
* `accessStatus != "public"` is controlled access (needs a SAM.gov login). Never fetched.
* Only PDF and DOCX are read; everything else is recorded and skipped.
* More than MAX_FILE_BYTES is recorded and skipped.
* An attachment whose name says it is an industry-day attendee list (or similar) is recorded and
  never downloaded; see privacy.py. A list that is only recognisable from its text is excluded after
  extraction (pipeline._attachment).
"""
from __future__ import annotations

from . import config, privacy
from .extract import ext_of

STATUSES = (
    "text",          # extracted, non-blank
    "no_text",       # fetched, but the extractor returned nothing: probably a scan
    "link",          # external URL, no file
    "deleted",       # removed by the agency
    "controlled",    # needs a SAM.gov login
    "skipped_format",
    "big",
    "excluded",      # withheld on purpose: personal contact data of private individuals (privacy.py)
    "download_error",
    "extract_error",
)


def plan(att: dict) -> dict:
    """Returns {kind, status, fetch, reason}: `fetch` is True only for an attachment worth downloading."""
    name = (att.get("name") or "").strip()
    out = {"kind": "file", "status": None, "fetch": False, "reason": None}
    if att.get("type") == "link" or (not name and att.get("uri")):
        out.update(kind="link", status="link")
    elif str(att.get("deletedFlag")) == "1":
        out["status"] = "deleted"
    elif att.get("accessStatus", "public") != "public":
        out["status"] = "controlled"
    elif privacy.by_name(name):
        out.update(status="excluded", reason=privacy.EXCLUDED_REASON)
    elif ext_of(name) not in config.FETCHABLE_EXTENSIONS:
        out["status"] = "skipped_format"
    elif (att.get("size") or 0) > config.MAX_FILE_BYTES:
        out["status"] = "big"
    else:
        out["fetch"] = True
    return out


def status_after_error(http_status: int, body: str) -> str:
    """Map a failed download to a status. 400 "deleted" and 401 are not failures of ours."""
    if http_status == 400 and "deleted" in body.lower():
        return "deleted"
    if http_status == 401:
        return "controlled"
    return "download_error"


def status_after_text(text: str) -> str:
    return "text" if text.strip() else "no_text"
