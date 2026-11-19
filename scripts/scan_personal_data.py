"""Scan what will be published for personal data, and list attachments that look like attendee lists
but were not excluded (a looser pattern than privacy.py). Prints counts; changes nothing.

    .venv/bin/python scripts/scan_personal_data.py dataset/data
"""
import re
import sys

import pandas as pd

from presolicitations import privacy, store

data = sys.argv[1] if len(sys.argv) > 1 else "dataset/data"
t = store.load_tables(data, with_texts=True)
n, a, x = t["notices"], t["attachments"], t["texts"]
EMAIL = privacy._EMAIL
PHONE = re.compile(r"(?<!\d)(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]\d{4}(?!\d)")
SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CONSUMER = re.compile(r"@(gmail|yahoo|hotmail|outlook|msn|aol|icloud|live|comcast|verizon|protonmail)\.", re.I)

print("notices", len(n), "| attachments", len(a), "| text rows", len(x))
print("attachments excluded (row kept, text withheld):", int((a["status"] == "excluded").sum()))
k = set(zip(a.loc[a["status"] == "excluded", "notice_id"], a.loc[a["status"] == "excluded", "resource_id"]))
print("text rows still present for an excluded attachment:",
      sum(1 for p in zip(x["notice_id"], x["resource_id"]) if p in k), "(must be 0)")

contact_cols = [c for c in n.columns if c.startswith(("primary_contact", "secondary_contact"))]
blob = n[contact_cols].fillna("").astype(str)
print("\ncontact columns (as published on SAM.gov, kept):",
      "emails", sum(len(EMAIL.findall(v)) for v in blob[[c for c in contact_cols if c.endswith("email")]].stack()),
      "| phones", int(n[[c for c in contact_cols if c.endswith("phone")]].notna().sum().sum()))

texts = x["text"].fillna("")
em = texts.map(lambda s: EMAIL.findall(s))
print("\npublished text rows:", len(x))
print("  email mentions", int(em.map(len).sum()), "| phone mentions", int(texts.map(lambda s: len(PHONE.findall(s))).sum()),
      "| consumer-domain email mentions (gmail, yahoo, msn, ...)", int(texts.map(lambda s: len(CONSUMER.findall(s))).sum()),
      "| SSN-shaped", int(texts.map(lambda s: len(SSN.findall(s))).sum()))

print("\nLooser pattern: attachments with >=10 distinct emails and <50% of them .gov/.mil, not excluded:")
names = a.set_index(["notice_id", "resource_id"])["name"].to_dict()
hits = []
for r in x.itertuples():
    nd, gov, dens = privacy.email_stats(r.text)
    if nd >= 10 and gov / nd < 0.5:
        hits.append((r.notice_id[:8], names.get((r.notice_id, r.resource_id), "")[:70], nd, gov, round(dens, 1)))
for h in sorted(set(hits), key=lambda h: -h[2]):
    print("  ", h)
print("  count:", len(set(hits)))
