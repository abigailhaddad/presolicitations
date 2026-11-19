import io
import shutil

import pytest

from presolicitations import classify, config, csvsource, extract
from conftest import MINIMAL_PDF, att, make_docx


def test_link_attachment_is_not_a_file():
    # regression: 815 link-type attachments were mislabelled "skipped extension"
    p = classify.plan({"type": "link", "uri": "https://www.fedconnect.net/x", "name": None})
    assert p == {"kind": "link", "status": "link", "fetch": False}
    assert classify.plan({"name": "", "uri": "https://x"})["status"] == "link"


def test_deleted_flag_wins_over_everything():
    assert classify.plan(att("r", deletedFlag="1"))["status"] == "deleted"
    assert classify.plan(att("r", deletedFlag="1", accessStatus="controlled"))["status"] == "deleted"


def test_controlled_is_never_fetched():
    p = classify.plan(att("r", accessStatus="controlled"))
    assert p["status"] == "controlled" and not p["fetch"]


@pytest.mark.parametrize("name", ["a.xlsx", "b.zip", "c.mp4", "d.doc", "e.pptx", "SPARC draft RFP", "f.rtf"])
def test_only_pdf_and_docx_are_read(name):
    assert classify.plan(att("r", name=name))["status"] == "skipped_format"


def test_big_files_are_skipped_by_metadata():
    assert classify.plan(att("r", size=config.MAX_FILE_BYTES + 1))["status"] == "big"
    assert classify.plan(att("r", size=config.MAX_FILE_BYTES))["fetch"]


def test_no_attachment_cap():
    assert all(classify.plan(att(f"r{i}"))["fetch"] for i in range(50))


def test_download_error_mapping():
    # regression: HTTP 400 on download means "The resource has been deleted"; 401 means controlled
    assert classify.status_after_error(400, '{"message":"The resource has been deleted."}') == "deleted"
    assert classify.status_after_error(400, "bad request") == "download_error"
    assert classify.status_after_error(401, "") == "controlled"
    assert classify.status_after_error(500, "") == "download_error"


def test_docx_entities_are_decoded():
    # regression: 133 of 346 DOCX texts held &amp; / &#8217; because the XML was not decoded
    xml = "<w:p><w:t>Terms &amp; Conditions &#8217;s &lt;draft&gt;</w:t></w:p><w:p><w:t>next</w:t></w:p>"
    out = extract.docx_text(make_docx(xml))
    assert "Terms & Conditions" in out and "&amp;" not in out and "&#8217;" not in out
    assert "<draft>" in out or "’s" in out
    assert "  " not in out


def test_docx_bad_zip_raises():
    with pytest.raises(Exception):
        extract.docx_text(b"not a zip")


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler not installed")
def test_pdf_text_and_scan_detection():
    assert "Hello presolicitation" in extract.pdf_text(MINIMAL_PDF)
    blank = MINIMAL_PDF.replace(b"(Hello presolicitation) Tj", b"")
    assert classify.status_after_text(extract.pdf_text(blank)) == "no_text"


def csv_bytes(rows, header=None):
    header = header or ["NoticeId", "Title", "Sol#", "Department/Ind.Agency", "Sub-Tier", "Office",
                        "PostedDate", "Type", "Description", "Link", "PrimaryContactEmail"]
    import csv
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(header)
    w.writerows(rows)
    return s.getvalue().encode("latin-1", errors="replace")


def test_csv_is_read_as_latin1_and_filters_type():
    # regression: the file is latin-1, not UTF-8; reading it as UTF-8 produced mojibake
    raw = csv_bytes([
        ["1", "Café services", "S1", "D", "S", "O", "2026-11-18 00:00:00.000-05", "Presolicitation", "d", "l", "a@b.gov"],
        ["2", "Other", "S2", "D", "S", "O", "2026-11-18", "Solicitation", "d", "l", ""],
    ])
    rows, counts = csvsource.presolicitations(io.BytesIO(raw))
    assert counts == {"rows": 2, "presolicitations": 1}
    assert rows[0]["title"] == "Café services"
    assert rows[0]["posted_date"] == "2026-11-18"
    assert rows[0]["primary_contact_email"] == "a@b.gov"
    assert rows[0]["naics_code"] == ""  # optional column absent from this file


def test_csv_missing_column_fails_loudly():
    raw = csv_bytes([["1", "t"]], header=["NoticeId", "Title"])
    with pytest.raises(csvsource.CsvSchemaError):
        csvsource.presolicitations(io.BytesIO(raw))


def test_csv_census():
    raw = csv_bytes([
        ["1", "t", "s", "d", "s", "o", "2025-01-02", "Presolicitation", "text", "l", ""],
        ["2", "t", "s", "d", "s", "o", "2026-01-02", "Presolicitation", "", "l", ""],
        ["3", "t", "s", "d", "s", "o", "2026-02-02", "Sources Sought", "x", "l", ""],
    ])
    c = csvsource.count_by_year(io.BytesIO(raw))
    assert c["presolicitations"] == 2 and c["by_posted_year"] == {"2025": 1, "2026": 1}
    assert c["with_description_text"] == {"2025": 1, "2026": 0}


def test_fetch_csv_stream_survives_gc_and_end_of_data(monkeypatch):
    # regressions from the first live run: the Response was garbage collected and closed the stream,
    # and the raw stream closed itself at end of data, so the next read raised "closed file"
    import gc

    payload = csv_bytes([["1", "t", "s", "d", "s", "o", "2026-11-18", "Presolicitation", "x", "l", ""]] * 50)

    class Resp:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            self.alive = True
            for i in range(0, len(payload), 37):  # awkward small chunks
                yield payload[i:i + 37]
            self.alive = False  # a real stream is closed once exhausted

        def __del__(self):
            pass

    monkeypatch.setattr(csvsource.requests, "get", lambda *a, **k: Resp())
    raw = csvsource.fetch_csv("http://example.invalid/x.csv")
    gc.collect()
    rows, counts = csvsource.presolicitations(raw)
    assert counts == {"rows": 50, "presolicitations": 50}
