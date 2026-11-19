"""Build tests/fixtures/sample.json: a small, real, truncated slice of the first manual pull.

    uv run python scripts/make_fixtures.py ~/Documents/repos/presol_fetch
"""
import json
import sys
from pathlib import Path

src = Path(sys.argv[1]).expanduser()
lst = {n["NoticeId"]: n for n in json.load(open(src / "list.json"))}
fin = {r["notice_id"]: r for r in json.load(open(src / "final3.json"))}
CUT = 1500


def kinds(r):
    out = set()
    for x in r["files"]:
        s = x.get("skip") or ""
        t = x.get("text")
        e = x.get("err") or ""
        if t is not None and not t.strip():
            out.add("no_text")
        elif t is not None and x["name"].lower().endswith(".docx") and "&" in t:
            out.add("docx_entity")
        elif t is not None and x["name"].lower().endswith(".docx"):
            out.add("docx")
        elif t is not None:
            out.add("pdf")
        elif s == "link" or (s.startswith("ext:") and not x["name"].strip()):
            out.add("link")
        elif s == "deleted":
            out.add("deleted")
        elif s == "controlled":
            out.add("controlled")
        elif s == "big":
            out.add("big")
        elif s.startswith("ext:"):
            out.add("format")
        elif e.startswith("HTTP Error 400"):
            out.add("err400")
        elif e.startswith("HTTP Error 401"):
            out.add("err401")
        elif "not a zip" in e:
            out.add("badzip")
    live = r.get("live") or {}
    if r.get("err"):
        out.add("vanished")
    if live.get("latest") is False:
        out.add("superseded")
    if len(r["files"]) >= 8:
        out.add("many")
    if not r["files"]:
        out.add("no_files")
    return out


want = ["no_text", "docx_entity", "docx", "pdf", "link", "deleted", "controlled", "big", "format",
        "err400", "err401", "badzip", "vanished", "superseded", "many", "no_files"]
picked, seen = [], set()
for nid, r in fin.items():
    k = kinds(r) - seen
    if k:
        picked.append(nid)
        seen |= kinds(r)
# plus a few ordinary notices and one Sol# duplicate group member
by_sol = {}
for nid, n in lst.items():
    by_sol.setdefault(n["Sol#"], []).append(nid)
dup = next(v for v in by_sol.values() if len(v) >= 2)
for nid in dup[:2] + list(fin)[:3]:
    if nid not in picked:
        picked.append(nid)
moji = [nid for nid, n in lst.items() if "â€" in n["Description"] or "Ã" in n["Title"]]
picked += [m for m in moji[:1] if m not in picked]


def cut(r):
    r = json.loads(json.dumps(r))
    for x in r["files"]:
        if x.get("text") and len(x["text"]) > CUT:
            x["text"] = x["text"][:CUT]
    return r


out = {"list": [dict(lst[n], Description=lst[n]["Description"][:3000]) for n in picked],
       "final3": [cut(fin[n]) for n in picked], "missing_kinds": sorted(set(want) - seen)}
Path("tests/fixtures/sample.json").write_text(json.dumps(out, ensure_ascii=False))
print(len(picked), "notices;", sum(len(r["files"]) for r in out["final3"]), "attachments; not found:", out["missing_kinds"])
