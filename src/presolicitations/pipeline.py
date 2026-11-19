"""The daily run, the backfill, and the pieces they share.

Rules (each is a regression from the first manual pull):

* Append-only. A notice that disappears from the CSV is kept, marked `in_csv_now = False` with the
  date it left (usually it became a solicitation). Nothing is ever deleted.
* A notice that SAM.gov now answers 404 for is kept and marked `vanished` with the date first seen.
* A superseded notice (`live_latest = False`) is kept and flagged; it is never re-checked.
* Controlled and deleted attachments are recorded and never fetched. No attachment cap.
* A failed fetch leaves `attachments_fetched_at` empty, so the next run retries it. That field is the
  resume point for both the daily run and the backfill.
* `first_seen` is the day this pipeline first saw a notice. `posted_date` is when SAM.gov posted it.
  They are different columns because a backfilled notice has an old `posted_date` and a new `first_seen`.
"""
from __future__ import annotations

import datetime as dt
import random
import time
from collections import Counter

import pandas as pd

from . import classify, config, csvsource, extract, store
from .sam import SamError
from .seed import sam_url

NOTICE_DEFAULTS = {
    "left_csv_on": None, "live_checked_at": None, "live_http": None, "live_type": None,
    "live_latest": None, "live_deleted": None, "live_archived": None, "live_cancelled": None,
    "live_status": None, "live_version": None, "vanished": False, "vanished_on": None,
    "attachments_fetched_at": None,
}


def live_fields(d: dict) -> dict:
    data2 = d.get("data2") or {}
    b = lambda v: None if v is None else bool(v)  # noqa: E731
    return {
        "type": data2.get("type"),
        "version": None if data2.get("version") is None else str(data2["version"]),
        "latest": b(d.get("latest")), "deleted": b(d.get("deleted")),
        "archived": b(d.get("archived")), "cancelled": b(d.get("cancelled")),
        "status": (d.get("status") or {}).get("code"),
    }


def _recs(df) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict("records")


class Run:
    """Holds the tables as records, applies steps, and hands back normalised tables."""

    def __init__(self, tables: dict[str, pd.DataFrame], client, now: dt.datetime):
        self.client = client
        self.ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.today = now.strftime("%Y-%m-%d")
        self.now = now
        self.stats: Counter = Counter()
        self.notices = {r["notice_id"]: r for r in _recs(tables["notices"])}
        self.atts = _recs(tables["attachments"])
        self._att_keys = {(a["notice_id"], a["resource_id"]) for a in self.atts}
        self.hist = _recs(tables["status_history"])
        self.texts: list[dict] = []  # new texts only

    # ---- bookkeeping -------------------------------------------------------------------------
    def _h(self, nid, event, http=None, live=None, note=None):
        live = live or {}
        self.hist.append({
            "notice_id": nid, "checked_at": self.ts, "event": event, "http": http,
            "type": live.get("type"), "latest": live.get("latest"), "deleted": live.get("deleted"),
            "archived": live.get("archived"), "cancelled": live.get("cancelled"),
            "status": live.get("status"), "version": live.get("version"), "note": note,
        })

    def _vanish(self, nid, where):
        n = self.notices[nid]
        if not n["vanished"]:
            self.stats["vanished_new"] += 1
        n["vanished"], n["vanished_on"] = True, n["vanished_on"] or self.today
        self._h(nid, "vanished", 404, note=f"{where} says the notice does not exist")

    # ---- CSV steps ---------------------------------------------------------------------------
    def csv_presence(self, csv_ids: set[str]) -> None:
        """Who is still in the CSV, who left, who came back."""
        for nid, n in self.notices.items():
            if nid in csv_ids:
                n["last_seen"] = self.today
                if not n["in_csv_now"]:
                    n["in_csv_now"], n["left_csv_on"] = True, None
                    self._h(nid, "returned_csv", note="back in the CSV as a presolicitation")
                    self.stats["returned_csv"] += 1
            elif n["in_csv_now"]:
                n["in_csv_now"], n["left_csv_on"] = False, self.today
                self._h(nid, "left_csv", note="no longer in the CSV as a presolicitation "
                        "(usually converted to a solicitation)")
                self.stats["left_csv"] += 1

    def add_notice(self, row: dict, source: str, note: str) -> None:
        nid = row["notice_id"]
        n = {c: row.get(c) or None for c in csvsource.EXTRA_COLUMNS}
        n.update({
            "notice_id": nid, "sol_number": row["sol_number"], "title": row["title"],
            "department": row["department"], "sub_tier": row["sub_tier"], "office": row["office"],
            "posted_date": row["posted_date"], "description": row["description"],
            "sam_url": sam_url(nid), "first_seen": self.today, "last_seen": self.today,
            "in_csv_now": True, "source": source, **NOTICE_DEFAULTS,
        })
        self.notices[nid] = n
        self._h(nid, "seen", note=note)
        self.stats["new_notices"] += 1

    def enrich(self, row: dict) -> None:
        """Fill CSV-only columns that an earlier pull did not have. Never overwrites a value."""
        n = self.notices[row["notice_id"]]
        changed = False
        for c in csvsource.EXTRA_COLUMNS:
            if n.get(c) is None and row.get(c):
                n[c] = row[c]
                changed = True
        self.stats["enriched"] += int(changed)

    # ---- per-notice steps --------------------------------------------------------------------
    def fetch(self, nid: str) -> None:
        """Attachments and text for one notice. Leaves it pending if the listing itself fails."""
        n = self.notices[nid]
        try:
            listing = self.client.resources(nid)
        except SamError as e:
            if e.status == 404:
                self._vanish(nid, "the resources endpoint")
            else:
                self.stats["resources_errors"] += 1
                self._h(nid, "resources_error", e.status, note=str(e)[:200])
            return
        added = 0
        for a in listing:
            row = self._attachment(nid, a)
            key = (nid, row["resource_id"])
            if key in self._att_keys:
                continue
            self._att_keys.add(key)
            self.atts.append(row)
            self.stats[f"attachment_{row['status']}"] += 1
            added += 1
        n["attachments_fetched_at"] = self.ts
        self.stats["attachments_added"] += added

    def _attachment(self, nid: str, a: dict) -> dict:
        name = (a.get("name") or "").strip()
        rid = a.get("resourceId")
        p = classify.plan(a)
        row = {
            "notice_id": nid, "resource_id": rid, "name": name, "ext": extract.ext_of(name),
            "kind": p["kind"], "status": p["status"], "size_bytes": a.get("size") or None,
            "access": a.get("accessStatus"), "deleted": str(a.get("deletedFlag")) == "1",
            "error": None, "text_chars": 0, "has_text": False, "fetched_at": self.ts,
            "source": "daily", "provenance": None,
        }
        if not p["fetch"]:
            return row
        try:
            data = self.client.download(rid)
            if len(data) > config.MAX_FILE_BYTES:
                row["status"] = "big"
                return row
            text = extract.extract(name, data)
            row["status"] = classify.status_after_text(text)
            row["text_chars"] = len(text.strip())
            row["has_text"] = bool(text.strip())
            self.texts.append({"notice_id": nid, "resource_id": rid, "text": text})
            if row["ext"] == "pdf":
                self.stats["pdf_fetched"] += 1
                self.stats["pdf_empty"] += int(not text.strip())
        except SamError as e:
            row["status"] = classify.status_after_error(e.status, e.body)
            row["error"] = str(e)[:300]
        except Exception as e:  # bad zip, pdftotext failure: our side, record it
            row["status"] = "extract_error"
            row["error"] = f"{type(e).__name__}: {e}"[:300]
        return row

    def live(self, nid: str) -> None:
        n = self.notices[nid]
        try:
            lv = live_fields(self.client.live(nid))
        except SamError as e:
            n["live_checked_at"], n["live_http"] = self.ts, e.status
            if e.status == 404:
                self._vanish(nid, "the live endpoint")
            else:
                self.stats["live_errors"] += 1
                self._h(nid, "live_error", e.status, note=str(e)[:200])
            return
        self.stats["live_checks"] += 1
        if n["live_latest"] is not False and lv["latest"] is False:
            self.stats["became_superseded"] += 1
        n.update(live_checked_at=self.ts, live_http=200, live_type=lv["type"], live_latest=lv["latest"],
                 live_deleted=lv["deleted"], live_archived=lv["archived"], live_cancelled=lv["cancelled"],
                 live_status=lv["status"], live_version=lv["version"])
        self._h(nid, "live_check", 200, lv)

    # ---- selections --------------------------------------------------------------------------
    def pending(self, include_backfill: bool = True) -> list[str]:
        """Notices whose attachments have not been fetched, newest posted first.

        The daily run passes include_backfill=False: backfilled notices are the backfill's job, so a
        daily run never quietly turns into a multi-hour crawl of thousands of old notices.
        """
        ids = [nid for nid, n in self.notices.items()
               if n["attachments_fetched_at"] is None and not n["vanished"]
               and (include_backfill or n["source"] != "backfill")]
        return sorted(ids, key=lambda i: (self.notices[i]["posted_date"], i), reverse=True)

    def live_due(self, recheck_days: int) -> list[str]:
        horizon = (self.now - dt.timedelta(days=recheck_days)).strftime("%Y-%m-%d")
        due = [nid for nid, n in self.notices.items()
               if not n["vanished"] and n["live_latest"] is not False
               and not (n["live_checked_at"] is None and n["source"] == "backfill")  # backfill's job
               and (n["live_checked_at"] is None or n["posted_date"] >= horizon)]
        return sorted(due, key=lambda i: (self.notices[i]["live_checked_at"] or "", i))

    def result(self) -> tuple[dict[str, pd.DataFrame], dict]:
        rows = {"notices": list(self.notices.values()), "attachments": self.atts,
                "texts": self.texts, "status_history": self.hist}
        out = {t: store.normalize(pd.DataFrame(r, columns=list(store.SCHEMAS[t])), t)
               for t, r in rows.items()}
        return out, {"as_of": self.today, "run_at": self.ts, **dict(sorted(self.stats.items()))}


def run_daily(
    tables, *, client, csv_rows: list[dict], now: dt.datetime, since: str = config.WINDOW_START,
    limit: int | None = None, recheck_days: int = 30, max_live_checks: int = 600,
):
    run = Run(tables, client, now)
    by_id = {r["notice_id"]: r for r in csv_rows}
    run.csv_presence(set(by_id))
    new = sorted((r for r in csv_rows if r["notice_id"] not in run.notices and r["posted_date"] >= since),
                 key=lambda r: (r["posted_date"], r["notice_id"]))
    own_pending = run.pending(include_backfill=False)
    work = own_pending + [r["notice_id"] for r in new]
    if limit is not None:
        work = work[:limit]
    run.stats["csv_new_in_window"] = len(new)
    run.stats["deferred_by_limit"] = len(own_pending) + len(new) - len(work)
    for nid in work:
        if nid not in run.notices:
            run.add_notice(by_id[nid], "daily", "first collected by the daily run")
        else:
            run.stats["pending_retried"] += 1
        run.fetch(nid)
    due = run.live_due(recheck_days)
    cap = max_live_checks if limit is None else min(max_live_checks, limit)  # --limit bounds the whole run
    for nid in due[:cap]:
        run.live(nid)
    run.stats["live_checks_deferred"] = max(0, len(due) - cap)
    return run.result()


def backfill_meta(tables, *, csv_rows: list[dict], now: dt.datetime):
    """Load EVERY presolicitation row in the CSV into the notices table: metadata only.

    Existing notices keep their values and gain any CSV-only columns they lacked. New notices get
    first_seen = today and keep their real posted_date. No SAM.gov API call is made.
    """
    run = Run(tables, None, now)
    run.csv_presence({r["notice_id"] for r in csv_rows})
    for r in csv_rows:
        if r["notice_id"] in run.notices:
            run.enrich(r)
        else:
            run.add_notice(r, "backfill", f"backfilled from the CSV (posted {r['posted_date']})")
    return run.result()


def backfill_fetch(
    tables, *, client, now: dt.datetime, offset: int = 0, limit: int = 300,
    max_seconds: float | None = None, sample: int | None = None, seed: int = 7,
):
    """Attachments, text and live status for the next `limit` pending notices (resumable).

    Pending = attachments not yet fetched, so re-running continues where the last run stopped.
    `offset` skips that many pending notices (normally 0). `sample` takes a random sample instead,
    for estimating cost. `max_seconds` stops starting new notices after that long, so a run fits in
    a GitHub Actions job; everything done so far is kept.
    """
    run = Run(tables, client, now)
    pend = run.pending()
    if sample:
        chunk = random.Random(seed).sample(pend, min(sample, len(pend)))
    else:
        chunk = pend[offset:offset + limit]
    start = time.monotonic()
    done = 0
    for nid in chunk:
        if max_seconds is not None and time.monotonic() - start > max_seconds:
            run.stats["stopped_for_time"] = 1
            break
        run.fetch(nid)
        if not run.notices[nid]["vanished"]:
            run.live(nid)
        done += 1
    run.stats["chunk_requested"] = len(chunk)
    run.stats["chunk_done"] = done
    run.stats["pending_after"] = len(run.pending())
    run.stats["seconds"] = round(time.monotonic() - start, 1)
    return run.result()
