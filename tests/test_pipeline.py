import datetime as dt

import pandas as pd
import pytest

from presolicitations import integrity, pipeline, store
from presolicitations.sam import SamError
from conftest import NOW, FakeClient, MINIMAL_PDF, att, csv_row, make_docx


def empty_tables():
    return {t: store.empty(t) for t in store.SCHEMAS}


def test_new_notice_attachments_are_classified_and_none_dropped():
    atts = [att(f"p{i}", name=f"f{i}.pdf") for i in range(9)]  # more than the old cap of 6
    atts += [att("link", name="", type="link", uri="https://x"), att("del", deletedFlag="1"),
             att("ctl", accessStatus="controlled"), att("xl", name="s.xlsx"), att("big", size=99_000_000)]
    files = {f"p{i}": MINIMAL_PDF for i in range(9)}
    c = FakeClient(resources={"N1": atts}, files=files)
    out, summary = pipeline.run_daily(empty_tables(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    a = out["attachments"].set_index("resource_id")
    assert len(a) == 14
    assert (a.loc[[f"p{i}" for i in range(9)], "status"] == "text").all()
    assert a.loc["link", "status"] == "link" and a.loc["del", "status"] == "deleted"
    assert a.loc["ctl", "status"] == "controlled" and a.loc["xl", "status"] == "skipped_format"
    assert a.loc["big", "status"] == "big"
    assert set(c.downloaded) == {f"p{i}" for i in range(9)}  # never fetched: link, deleted, controlled, xlsx, big
    assert len(out["texts"]) == 9 and summary["pdf_fetched"] == 9


def test_download_errors_become_statuses_not_crashes():
    atts = [att("a"), att("b"), att("c"), att("d", name="d.docx"), att("e", name="e.docx")]
    files = {"a": SamError(400, '{"message":"The resource has been deleted."}'), "b": SamError(401, "nope"),
             "c": SamError(500, "boom"), "d": b"not a zip", "e": make_docx("<w:t>ok &amp; ok</w:t>")}
    c = FakeClient(resources={"N1": atts}, files=files)
    out, _ = pipeline.run_daily(empty_tables(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    s = out["attachments"].set_index("resource_id")["status"].to_dict()
    assert s == {"a": "deleted", "b": "controlled", "c": "download_error", "d": "extract_error", "e": "text"}
    assert out["texts"].set_index("resource_id").loc["e", "text"] == "ok & ok"


def test_window_and_first_seen_versus_posted(seeded):
    rows = [csv_row("OLD", posted="2026-01-05"), csv_row("NEW", posted="2026-11-18")]
    out, s = pipeline.run_daily(seeded, client=FakeClient(), csv_rows=rows, now=NOW)
    n = out["notices"].set_index("notice_id")
    assert "OLD" not in n.index  # before the window; backfill's job
    assert n.loc["NEW", "first_seen"] == "2026-11-19" and n.loc["NEW", "posted_date"] == "2026-11-18"
    assert s["new_notices"] == 1


def test_notice_that_leaves_the_csv_is_kept_and_marked(seeded):
    nid = seeded["notices"]["notice_id"].iloc[0]
    out, s = pipeline.run_daily(seeded, client=FakeClient(), csv_rows=[], now=NOW)
    n = out["notices"].set_index("notice_id")
    assert nid in n.index and not n.loc[nid, "in_csv_now"] and n.loc[nid, "left_csv_on"] == "2026-11-19"
    assert s["left_csv"] == len(seeded["notices"])
    h = out["status_history"]
    assert ((h["notice_id"] == nid) & (h["event"] == "left_csv")).any()
    # and it comes back
    row = csv_row(nid, posted=n.loc[nid, "posted_date"])
    out2, s2 = pipeline.run_daily(out, client=FakeClient(), csv_rows=[row], now=NOW + dt.timedelta(days=1))
    n2 = out2["notices"].set_index("notice_id")
    assert n2.loc[nid, "in_csv_now"] and pd.isna(n2.loc[nid, "left_csv_on"]) and s2["returned_csv"] == 1


def test_404_marks_vanished_with_the_date_and_is_not_rechecked():
    c = FakeClient(resources={"N1": SamError(404, "Opportunity does not exist.")})
    out, s = pipeline.run_daily(empty_tables(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    n = out["notices"].set_index("notice_id").loc["N1"]
    assert n["vanished"] and n["vanished_on"] == "2026-11-19" and s["vanished_new"] == 1
    out2, s2 = pipeline.run_daily(out, client=c, csv_rows=[csv_row("N1")], now=NOW + dt.timedelta(days=1))
    assert out2["notices"].set_index("notice_id").loc["N1", "vanished_on"] == "2026-11-19"
    assert s2.get("live_checks", 0) == 0 and s2.get("pending_retried", 0) == 0


def test_live_404_also_marks_vanished():
    c = FakeClient(resources={"N1": []}, live={"N1": SamError(404, "gone")})
    out, _ = pipeline.run_daily(empty_tables(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    n = out["notices"].set_index("notice_id").loc["N1"]
    assert n["vanished"] and n["live_http"] == 404


def test_superseded_notice_is_kept_flagged_and_never_rechecked():
    live = {"N1": {"latest": False, "status": {"code": "published"}, "data2": {"type": "p", "version": 1}}}
    c = FakeClient(live=live)
    out, s = pipeline.run_daily(empty_tables(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    assert out["notices"].set_index("notice_id").loc["N1", "live_latest"] == False  # noqa: E712
    assert s["became_superseded"] == 1
    _, s2 = pipeline.run_daily(out, client=c, csv_rows=[csv_row("N1")], now=NOW + dt.timedelta(days=1))
    assert s2.get("live_checks", 0) == 0


def test_failed_listing_leaves_notice_pending_and_next_run_retries():
    c = FakeClient(resources={"N1": SamError(500, "oops")})
    out, s = pipeline.run_daily(empty_tables(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    assert pd.isna(out["notices"].set_index("notice_id").loc["N1", "attachments_fetched_at"])
    assert s["resources_errors"] == 1
    c.res["N1"] = [att("a")]
    c.files["a"] = MINIMAL_PDF
    out2, s2 = pipeline.run_daily(out, client=c, csv_rows=[csv_row("N1")], now=NOW + dt.timedelta(days=1))
    assert s2["pending_retried"] == 1 and len(out2["attachments"]) == 1


def test_limit_defers_the_rest():
    rows = [csv_row(f"N{i}", posted=f"2026-11-{10 + i}") for i in range(5)]
    out, s = pipeline.run_daily(empty_tables(), client=FakeClient(), csv_rows=rows, now=NOW, limit=2)
    assert len(out["notices"]) == 2 and s["deferred_by_limit"] == 3


def test_append_only_integrity_catches_a_lost_notice(seeded):
    out, s = pipeline.run_daily(seeded, client=FakeClient(), csv_rows=[], now=NOW)
    broken = {**out, "notices": out["notices"].iloc[1:]}
    assert any("missing now" in b for b in integrity.check(seeded, broken, s))
    assert integrity.check(seeded, out, s) == []


# ---- backfill ------------------------------------------------------------------------------
def test_backfill_meta_loads_everything_without_api_calls_and_keeps_posted_separate(seeded):
    existing = seeded["notices"]["notice_id"].iloc[0]
    rows = [csv_row(existing, naics_code="541511", primary_contact_email="a@b.gov"),
            csv_row("OLD1", posted="2016-03-04"), csv_row("OLD2", posted="2019-09-09")]
    out, s = pipeline.backfill_meta(seeded, csv_rows=rows, now=NOW)
    n = out["notices"].set_index("notice_id")
    assert n.loc["OLD1", "posted_date"] == "2016-03-04" and n.loc["OLD1", "first_seen"] == "2026-11-19"
    assert n.loc["OLD1", "source"] == "backfill" and pd.isna(n.loc["OLD1", "attachments_fetched_at"])
    assert n.loc[existing, "naics_code"] == "541511" and n.loc[existing, "source"] == "seed"  # enriched, not replaced
    assert n.loc[existing, "first_seen"] == "2026-11-18"  # unchanged
    assert s["new_notices"] == 2 and s["enriched"] == 1


def test_backfill_meta_never_overwrites_an_existing_value(seeded):
    nid = seeded["notices"]["notice_id"].iloc[0]
    first = pipeline.backfill_meta(seeded, csv_rows=[csv_row(nid, naics_code="111")], now=NOW)[0]
    second = pipeline.backfill_meta(first, csv_rows=[csv_row(nid, naics_code="222")], now=NOW)[0]
    assert second["notices"].set_index("notice_id").loc[nid, "naics_code"] == "111"


def backfilled(n):
    rows = [csv_row(f"B{i:02d}", posted=f"2019-01-{i + 1:02d}") for i in range(n)]
    return pipeline.backfill_meta(empty_tables(), csv_rows=rows, now=NOW)[0]


def test_backfill_fetch_is_resumable_in_chunks():
    t = backfilled(7)
    c = FakeClient(resources={f"B{i:02d}": [att(f"r{i}")] for i in range(7)}, files={f"r{i}": MINIMAL_PDF for i in range(7)})
    t1, s1 = pipeline.backfill_fetch(t, client=c, now=NOW, limit=3)
    assert s1["chunk_done"] == 3 and s1["pending_after"] == 4
    t2, s2 = pipeline.backfill_fetch(t1, client=c, now=NOW, limit=3)
    assert s2["chunk_done"] == 3 and s2["pending_after"] == 1
    t3, s3 = pipeline.backfill_fetch(t2, client=c, now=NOW, limit=3)
    assert s3["chunk_done"] == 1 and s3["pending_after"] == 0
    assert len(t3["attachments"]) == 7 and t3["notices"]["attachments_fetched_at"].notna().all()
    assert t3["notices"]["live_checked_at"].notna().all()
    assert sorted(c.downloaded) == sorted(set(c.downloaded))  # nothing fetched twice


def test_backfill_fetch_newest_first_and_offset():
    t = backfilled(5)
    c = FakeClient()
    t1, _ = pipeline.backfill_fetch(t, client=c, now=NOW, limit=2)
    done = set(t1["notices"].dropna(subset=["attachments_fetched_at"])["notice_id"])
    assert done == {"B04", "B03"}  # newest posted first
    t2, _ = pipeline.backfill_fetch(t, client=c, now=NOW, offset=2, limit=2)
    assert set(t2["notices"].dropna(subset=["attachments_fetched_at"])["notice_id"]) == {"B02", "B01"}


def test_backfill_fetch_stops_for_time_and_keeps_progress():
    t = backfilled(4)
    t1, s = pipeline.backfill_fetch(t, client=FakeClient(), now=NOW, limit=4, max_seconds=-1)
    assert s["stopped_for_time"] == 1 and s["chunk_done"] == 0 and s["pending_after"] == 4


def test_backfill_fetch_sample_is_deterministic():
    t = backfilled(30)
    a = pipeline.backfill_fetch(t, client=FakeClient(), now=NOW, sample=5, seed=3)[0]
    b = pipeline.backfill_fetch(t, client=FakeClient(), now=NOW, sample=5, seed=3)[0]
    pick = lambda x: set(x["notices"].dropna(subset=["attachments_fetched_at"])["notice_id"])  # noqa: E731
    assert pick(a) == pick(b) and len(pick(a)) == 5


def test_backfilled_vanished_notice_skips_live_check():
    t = backfilled(1)
    c = FakeClient(resources={"B00": SamError(404, "gone")})
    out, s = pipeline.backfill_fetch(t, client=c, now=NOW, limit=1)
    assert out["notices"].set_index("notice_id").loc["B00", "vanished"] and s.get("live_checks", 0) == 0


def test_daily_leaves_backfill_pending_alone_and_never_live_checks_it():
    t = backfilled(3)  # pending, never live-checked, source=backfill
    c = FakeClient()
    out, s = pipeline.run_daily(t, client=c, csv_rows=[csv_row(f"B{i:02d}", posted=f"2019-01-{i + 1:02d}") for i in range(3)], now=NOW)
    assert out["notices"]["attachments_fetched_at"].isna().all() and s.get("live_checks", 0) == 0
    assert c.downloaded == []


def test_limit_also_bounds_live_checks(seeded):
    # regression: a "--limit 5" proof run still made 600 live checks
    rows = [csv_row(n, posted=p) for n, p in zip(seeded["notices"]["notice_id"], seeded["notices"]["posted_date"])]
    seeded["notices"]["live_checked_at"] = None
    seeded["notices"]["live_latest"] = pd.array([None] * len(seeded["notices"]), dtype="boolean")
    _, s = pipeline.run_daily(seeded, client=FakeClient(), csv_rows=rows, now=NOW, limit=2, recheck_days=3650)
    assert s["live_checks"] == 2
