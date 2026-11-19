"""Convert the first manual pull (list.json + final3.json from the research folder) into the tables.

The conversion keeps every field and every record: 1,532 notices, 2,445 attachments. Where the first
pull did not record an attachment's SAM.gov type (about 1,366 notices were fetched before the
"type" field was kept), the status is derived from what it did record, and `provenance` says so.
"""
from __future__ import annotations

import pandas as pd

from . import csvsource, store
from .extract import ext_of

SEED_DATE = "2026-11-18"


def sam_url(notice_id: str) -> str:
    return f"https://sam.gov/opp/{notice_id}/view"


def _attachment_row(notice_id: str, x: dict) -> dict:
    name = x.get("name") or ""
    text = x.get("text")
    skip = x.get("skip") or ""
    err = x.get("err") or ""
    prov = "seed"
    kind = "file"
    if text is not None:
        status = "text" if text.strip() else "no_text"
    elif skip == "link":
        kind, status = "link", "link"
    elif skip == "deleted":
        status = "deleted"
    elif skip == "controlled":
        status = "controlled"
    elif skip == "big":
        status = "big"
    elif skip.startswith("ext:"):
        status = "skipped_format"
        if not name.strip():
            kind, status = "link", "link"
            prov = "seed: inferred link (empty name; the first pull did not record the API type)"
        elif "." not in name:
            kind = "unknown"
            prov = "seed: name has no extension (link text or a bare title), format not checked"
    elif err.startswith("HTTP Error 400"):
        status = "deleted"
        prov = "seed: HTTP 400 on download, read as deleted (3 of the 15 re-tested live said 'resource has been deleted')"
    elif err.startswith("HTTP Error 401"):
        status = "controlled"
    elif "not a zip" in err:
        status = "extract_error"
    elif err:
        status = "download_error"
    else:
        raise ValueError(f"unclassifiable seed attachment {notice_id} {x.get('rid')}: {x}")
    deleted = x.get("deleted")
    return {
        "notice_id": notice_id, "resource_id": x["rid"], "name": name, "ext": ext_of(name),
        "kind": kind, "status": status,
        "size_bytes": x.get("size"), "access": x.get("access"),
        "deleted": None if deleted is None else str(deleted) == "1",
        "error": err or None,
        "text_chars": len(text.strip()) if text else 0,
        "has_text": bool(text and text.strip()),
        "fetched_at": SEED_DATE, "source": "seed", "provenance": prov, "excluded_reason": None,
    }


def seed_tables(list_json: list[dict], final3: list[dict]) -> dict[str, pd.DataFrame]:
    by_id = {r["notice_id"]: r for r in final3}
    notices, atts, texts, hist = [], [], [], []
    for n in list_json:
        nid = n["NoticeId"]
        r = by_id[nid]
        live = r.get("live") or {}
        is404 = "404" in (live.get("err") or r.get("err") or "")
        notices.append({
            "notice_id": nid, "sol_number": n["Sol#"], "title": n["Title"],
            "department": n["Department/Ind.Agency"], "sub_tier": n["Sub-Tier"], "office": n["Office"],
            "posted_date": n["PostedDate"][:10], "description": n["Description"], "sam_url": sam_url(nid),
            **{c: None for c in csvsource.EXTRA_COLUMNS},
            "first_seen": SEED_DATE, "last_seen": SEED_DATE, "in_csv_now": True, "left_csv_on": None,
            "live_checked_at": SEED_DATE, "live_http": 404 if is404 else 200,
            "live_type": live.get("type"), "live_latest": live.get("latest"),
            "live_deleted": live.get("deleted"), "live_archived": live.get("archived"),
            "live_cancelled": live.get("cancelled"), "live_status": live.get("status"),
            "live_version": live.get("version"),
            "vanished": is404, "vanished_on": SEED_DATE if is404 else None,
            "attachments_fetched_at": SEED_DATE, "source": "seed",
        })
        hist.append({"notice_id": nid, "checked_at": SEED_DATE, "event": "seen", "http": None,
                     "type": None, "latest": None, "deleted": None, "archived": None,
                     "cancelled": None, "status": None, "version": None,
                     "note": "first collected by the seed pull"})
        hist.append({"notice_id": nid, "checked_at": SEED_DATE,
                     "event": "vanished" if is404 else "live_check",
                     "http": 404 if is404 else 200, "type": live.get("type"),
                     "latest": live.get("latest"), "deleted": live.get("deleted"),
                     "archived": live.get("archived"), "cancelled": live.get("cancelled"),
                     "status": live.get("status"), "version": live.get("version"), "note": None})
        for x in r["files"]:
            atts.append(_attachment_row(nid, x))
            if x.get("text") is not None:
                texts.append({"notice_id": nid, "resource_id": x["rid"], "text": x["text"]})
    out = {
        "notices": pd.DataFrame(notices), "attachments": pd.DataFrame(atts),
        "texts": pd.DataFrame(texts), "status_history": pd.DataFrame(hist),
    }
    return {t: store.normalize(df, t) for t, df in out.items()}


def reconcile(tables: dict[str, pd.DataFrame], list_json: list[dict], final3: list[dict]) -> list[str]:
    """Field-by-field comparison of the seeded tables against the source files. [] means they agree."""
    bad: list[str] = []
    # Later runs add rows; only the seeded rows are compared (their CSV-only columns may be enriched).
    seeded = tables["notices"][tables["notices"]["source"] == "seed"]
    n = seeded.set_index("notice_id")
    a = tables["attachments"][tables["attachments"]["source"] == "seed"].set_index(["notice_id", "resource_id"])
    t = tables["texts"].set_index(["notice_id", "resource_id"])["text"]
    t = t[t.index.isin(a.index)]
    if len(n) != len(list_json):
        bad.append(f"notices: {len(n)} rows vs {len(list_json)} in list.json")
    f3 = {r["notice_id"]: r for r in final3}
    src_atts = [(r["notice_id"], x) for r in final3 for x in r["files"]]
    if len(a) != len(src_atts):
        bad.append(f"attachments: {len(a)} rows vs {len(src_atts)} in final3")
    # Attachments withheld by privacy.py keep their row but not their text; they are the only allowed gap.
    withheld = set(a.index[a["status"] == "excluded"])
    src_text = sum(1 for nid, x in src_atts if x.get("text") is not None and (nid, x["rid"]) not in withheld)
    if len(t) != src_text:
        bad.append(f"texts: {len(t)} rows vs {src_text} with text in final3")
    for row in list_json:
        nid = row["NoticeId"]
        if nid not in n.index:
            bad.append(f"notice {nid} missing")
            continue
        r = n.loc[nid]
        pairs = [("sol_number", row["Sol#"]), ("title", row["Title"]), ("department", row["Department/Ind.Agency"]),
                 ("sub_tier", row["Sub-Tier"]), ("office", row["Office"]), ("description", row["Description"]),
                 ("posted_date", row["PostedDate"][:10]), ("sam_url", sam_url(nid))]
        live = f3[nid].get("live") or {}
        pairs += [("live_type", live.get("type")), ("live_latest", live.get("latest")),
                  ("live_archived", live.get("archived")), ("live_status", live.get("status"))]
        for col, want in pairs:
            got = r[col]
            if (pd.isna(got) and want is None) or got == want:
                continue
            bad.append(f"notice {nid} {col}: {got!r} != {want!r}")
    for nid, x in src_atts:
        key = (nid, x["rid"])
        if key not in a.index:
            bad.append(f"attachment {key} missing")
            continue
        r = a.loc[key]
        if r["name"] != (x.get("name") or ""):
            bad.append(f"attachment {key} name differs")
        want_size = x.get("size")
        if not (pd.isna(r["size_bytes"]) and want_size is None) and r["size_bytes"] != want_size:
            bad.append(f"attachment {key} size differs")
        if key in withheld:
            if key in t.index:
                bad.append(f"attachment {key} is excluded but its text is still in the table")
        elif x.get("text") is not None:
            if key not in t.index or t.loc[key] != x["text"]:
                bad.append(f"attachment {key} text differs")
        elif key in t.index:
            bad.append(f"attachment {key} has text in the table but none in the source")
    return bad
