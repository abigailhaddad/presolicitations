"""Industry-day attendee lists keep their row but never their text (privacy.py)."""
import pandas as pd

from presolicitations import classify, pipeline, privacy, store
from conftest import NOW, FakeClient, MINIMAL_PDF, att, csv_row, make_docx


def _empty():
    return {t: store.empty(t) for t in store.SCHEMAS}


def _emails(n, domain="gmail.com"):
    return " ".join(f"person{i}@{domain}" for i in range(n))


def test_name_rule_catches_the_real_file_names():
    for name in ("Industry Day UNNO Attendees.pdf", "MINSAV_Industry_Day_Attendee_List.pdf",
                 "W519TC-26-R-A061 Industry Day Attendees 22-23-Sep-2026.pdf",
                 "Attachment 9 - UNNO Industry Day List_10-26-26.pdf", "100209_SV Sign-in Sheet.pdf",
                 "CO BR UCPO BLUE MESA ROAD Pre Ad Site Visit Sign in Sheet.pdf"):
        assert privacy.by_name(name), name


def test_name_rule_leaves_ordinary_files_alone():
    for name in ("Item 7 - Tank Ammo Industry Day Registration Form.docx", "Solicitation 36C10X27R0002.docx",
                 "Industry Day Presentation.pdf", "Statement of Work.pdf", ""):
        assert not privacy.by_name(name), name


def test_text_rule_needs_many_private_emails_densely():
    assert privacy.by_text("Company list " + _emails(30))                       # a list of 30 private emails
    assert not privacy.by_text("contacts " + _emails(30, "agency.gov"))         # agency contacts: .gov
    assert not privacy.by_text(_emails(30, "army.mil"))                          # .mil too
    assert not privacy.by_text(_emails(10))                                      # too few
    assert not privacy.by_text(("lorem ipsum " * 20000) + _emails(30))           # a big spec with 30 contacts in it


def test_plan_never_downloads_an_attendee_list_by_name():
    p = classify.plan(att("x", name="Industry Day Attendees.pdf"))
    assert p["status"] == "excluded" and p["fetch"] is False and p["reason"] == privacy.EXCLUDED_REASON


def test_pipeline_keeps_the_row_but_not_the_text_and_does_not_download_named_lists():
    atts = [att("named", name="Industry Day Attendees.pdf"), att("ok", name="sow.pdf")]
    c = FakeClient(resources={"N1": atts}, files={"named": MINIMAL_PDF, "ok": MINIMAL_PDF})
    out, s = pipeline.run_daily(_empty(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    a = out["attachments"].set_index("resource_id")
    assert a.loc["named", "status"] == "excluded" and a.loc["named", "excluded_reason"] == privacy.EXCLUDED_REASON
    assert not a.loc["named", "has_text"] and a.loc["named", "text_chars"] == 0
    assert "named" not in c.downloaded and set(out["texts"]["resource_id"]) == {"ok"}
    assert s["attachment_excluded"] == 1


def test_pipeline_drops_a_list_that_is_only_recognisable_from_its_text():
    body = "<w:t>" + _emails(40) + "</w:t>"
    c = FakeClient(resources={"N1": [att("u", name="Interest.docx")]}, files={"u": make_docx(body)})
    out, s = pipeline.run_daily(_empty(), client=c, csv_rows=[csv_row("N1")], now=NOW)
    a = out["attachments"].set_index("resource_id").loc["u"]
    assert a["status"] == "excluded" and not a["has_text"] and len(out["texts"]) == 0
    assert "u" in c.downloaded and s["excluded_by_text"] == 1  # it had to be read to know


def test_apply_exclusions_and_drop_texts_on_disk(tmp_path):
    t = _empty()
    nid = "N1"
    t["notices"] = store.normalize(pd.DataFrame([{**{c: None for c in store.SCHEMAS["notices"]},
        "notice_id": nid, "posted_date": "2026-11-01", "first_seen": "2026-11-19", "last_seen": "2026-11-19",
        "in_csv_now": True, "vanished": False, "source": "seed"}]), "notices")
    rows = [("a", "Attendees List.pdf", "x"), ("b", "sow.pdf", "plain text"), ("c", "misc.pdf", _emails(40))]
    t["attachments"] = store.normalize(pd.DataFrame([{**{c: None for c in store.SCHEMAS["attachments"]},
        "notice_id": nid, "resource_id": r, "name": n, "ext": "pdf", "kind": "file", "status": "text",
        "text_chars": len(x), "has_text": True, "deleted": False, "source": "seed"} for r, n, x in rows]),
        "attachments")
    t["texts"] = store.normalize(pd.DataFrame([{"notice_id": nid, "resource_id": r, "text": x} for r, _, x in rows]),
                                 "texts")
    att_after, keys, names = pipeline.apply_exclusions(t, t["texts"])
    assert keys == {(nid, "a"), (nid, "c")} and len(names) == 2
    assert att_after.set_index("resource_id")["status"].to_dict() == {"a": "excluded", "b": "text", "c": "excluded"}
    assert len(att_after) == 3  # rows are never dropped
    store.save_all(tmp_path / "data", {**t, "attachments": att_after}, stamp="2026-11-19")
    assert store.drop_texts(tmp_path / "data", keys) == 2
    left = store.load(tmp_path / "data", "texts")
    assert left["resource_id"].tolist() == ["b"]
