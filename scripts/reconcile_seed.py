"""Compare the seeded tables on disk with the first manual pull, field by field.

    uv run python scripts/reconcile_seed.py ~/Documents/repos/presol_fetch dataset/data
"""
import json
import sys
from pathlib import Path

from presolicitations import seed, store

src, data = Path(sys.argv[1]).expanduser(), sys.argv[2]
lst = json.load(open(src / "list.json"))
fin = json.load(open(src / "final3.json"))
t = store.load_tables(data, with_texts=True)
bad = seed.reconcile(t, lst, fin)
a = t["attachments"]
print("notices", len(t["notices"]), "| attachments", len(a), "| text rows", len(t["texts"]),
      "| has_text (non-blank)", int(a["has_text"].sum()))
print(a["status"].value_counts().to_string())
print("DISAGREEMENTS:", len(bad))
for b in bad[:20]:
    print(" -", b)
sys.exit(1 if bad else 0)
