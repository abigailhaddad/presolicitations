import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from presolicitations import seed
from presolicitations.sam import SamError

FIXTURE = Path(__file__).parent / "fixtures" / "sample.json"
NOW = dt.datetime(2026, 11, 19, 6, 30, 0)


@pytest.fixture(scope="session")
def sample():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def seeded(sample):
    return seed.seed_tables(sample["list"], sample["final3"])


def csv_row(nid, posted="2026-11-19", sol="SOL-1", title="T", desc="D", **kw):
    row = {"notice_id": nid, "title": title, "sol_number": sol, "department": "DEPT OF TEST",
           "sub_tier": "SUB", "office": "OFFICE", "posted_date": posted, "_type": "Presolicitation",
           "description": desc, "_link": ""}
    row.update(kw)
    from presolicitations import csvsource
    for c in csvsource.EXTRA_COLUMNS:
        row.setdefault(c, "")
    return row


class FakeClient:
    """Same three methods as sam.Client. `resources` maps notice id -> attachment list or a SamError."""

    def __init__(self, resources=None, live=None, files=None):
        self.res = resources or {}
        self.liv = live or {}
        self.files = files or {}
        self.downloaded = []

    def resources(self, nid):
        r = self.res.get(nid, [])
        if isinstance(r, SamError):
            raise r
        return r

    def live(self, nid):
        r = self.liv.get(nid, {"latest": True, "deleted": False, "archived": False, "cancelled": False,
                               "status": {"code": "published"}, "data2": {"type": "p", "version": 1}})
        if isinstance(r, SamError):
            raise r
        return r

    def download(self, rid):
        self.downloaded.append(rid)
        r = self.files.get(rid, b"")
        if isinstance(r, SamError):
            raise r
        return r


def att(rid, name="a.pdf", **kw):
    d = {"resourceId": rid, "name": name, "type": "file", "deletedFlag": "0", "accessStatus": "public",
         "size": 1000}
    d.update(kw)
    return d


MINIMAL_PDF = (
    b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 50 Td (Hello presolicitation) Tj ET\nendstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R/Size 6>>\n%%EOF\n"
)


def make_docx(xml_body: str) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{xml_body}</w:body></w:document>")
    return buf.getvalue()
