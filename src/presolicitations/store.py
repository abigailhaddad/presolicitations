"""The four tables, their types, and atomic reads and writes.

Append-only is a rule of the pipeline, not of parquet: rows are added and updated, never removed.
`integrity.check` fails the run if any key present before is missing after.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

SCHEMAS: dict[str, dict[str, str]] = {
    "notices": {
        "notice_id": "string", "sol_number": "string", "title": "string", "department": "string",
        "sub_tier": "string", "office": "string", "posted_date": "string", "description": "string",
        "sam_url": "string",
        "naics_code": "string", "classification_code": "string", "set_aside": "string",
        "response_deadline": "string", "pop_city": "string", "pop_state": "string",
        "pop_zip": "string", "pop_country": "string", "organization_type": "string",
        "additional_info_link": "string",
        "primary_contact_title": "string", "primary_contact_name": "string",
        "primary_contact_email": "string", "primary_contact_phone": "string",
        "secondary_contact_title": "string", "secondary_contact_name": "string",
        "secondary_contact_email": "string", "secondary_contact_phone": "string",
        "first_seen": "string", "last_seen": "string",
        "in_csv_now": "boolean", "left_csv_on": "string",
        "live_checked_at": "string", "live_http": "Int64", "live_type": "string",
        "live_latest": "boolean", "live_deleted": "boolean", "live_archived": "boolean",
        "live_cancelled": "boolean", "live_status": "string", "live_version": "string",
        "vanished": "boolean", "vanished_on": "string",
        "attachments_fetched_at": "string", "source": "string",
    },
    "attachments": {
        "notice_id": "string", "resource_id": "string", "name": "string", "ext": "string",
        "kind": "string", "status": "string", "size_bytes": "Int64", "access": "string",
        "deleted": "boolean", "error": "string", "text_chars": "Int64", "has_text": "boolean",
        "fetched_at": "string", "source": "string", "provenance": "string",
    },
    "texts": {"notice_id": "string", "resource_id": "string", "text": "string"},
    "status_history": {
        "notice_id": "string", "checked_at": "string", "event": "string", "http": "Int64",
        "type": "string", "latest": "boolean", "deleted": "boolean", "archived": "boolean",
        "cancelled": "boolean", "status": "string", "version": "string", "note": "string",
    },
}
KEYS = {
    "notices": ["notice_id"],
    "attachments": ["notice_id", "resource_id"],
    "texts": ["notice_id", "resource_id"],
}


def empty(table: str) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in SCHEMAS[table].items()})


def normalize(df: pd.DataFrame, table: str) -> pd.DataFrame:
    cols = SCHEMAS[table]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{table}: missing columns {missing}")
    return df[list(cols)].astype(cols).reset_index(drop=True)


# ---- layout on disk and on Hugging Face -------------------------------------------------------
#   data/notices/notices-<posted year>.parquet          rewritten each run (small)
#   data/attachments/attachments-<posted year>.parquet  rewritten each run (small, no text)
#   data/status_history.parquet                         rewritten each run (small)
#   data/texts/texts-<posted year>-<run stamp>.parquet  APPEND-ONLY: a new file per run, never rewritten
# "Year" is the notice's posted year, never the year it was first seen.
SHARDED = ("notices", "attachments")


def year_of(posted_date) -> str:
    y = str(posted_date)[:4]
    return y if y.isdigit() else "unknown"


def _read_dir(d: Path, table: str) -> pd.DataFrame:
    files = sorted(d.glob("*.parquet"))
    if not files:
        return empty(table)
    return normalize(pd.concat([pd.read_parquet(f) for f in files], ignore_index=True), table)


def load(data_dir: str | Path, table: str) -> pd.DataFrame:
    d = Path(data_dir)
    if table in SHARDED or table == "texts":
        return _read_dir(d / table, table)
    p = d / f"{table}.parquet"
    return normalize(pd.read_parquet(p), table) if p.exists() else empty(table)


def load_tables(data_dir: str | Path, with_texts: bool = False) -> dict[str, pd.DataFrame]:
    """The notices, attachments and history; texts are skipped unless asked (they are big)."""
    t = {name: load(data_dir, name) for name in ("notices", "attachments", "status_history")}
    t["texts"] = load(data_dir, "texts") if with_texts else empty("texts")
    return t


def _write(df: pd.DataFrame, table: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    normalize(df, table).to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)  # atomic: a crash never leaves half a file


def save_all(data_dir: str | Path, tables: dict[str, pd.DataFrame], *, stamp: str) -> None:
    """Write the tables. `tables["texts"]` holds only NEW texts; they go to new append-only files."""
    d = Path(data_dir)
    year = {r.notice_id: year_of(r.posted_date) for r in tables["notices"].itertuples()}
    for table in SHARDED:
        df = tables[table]
        if table == "notices":
            groups = df.groupby(df["posted_date"].map(year_of))
        else:
            groups = df.groupby(df["notice_id"].map(lambda n: year.get(n, "unknown")))
        keep = set()
        for y, g in groups:
            _write(g, table, d / table / f"{table}-{y}.parquet")
            keep.add(f"{table}-{y}.parquet")
        if (d / table).exists():
            for stale in (d / table).glob("*.parquet"):
                if stale.name not in keep:
                    raise RuntimeError(f"refusing to drop shard {stale.name}: append-only")
    _write(tables["status_history"], "status_history", d / "status_history.parquet")
    new_texts = tables["texts"]
    if len(new_texts):
        for y, g in new_texts.groupby(new_texts["notice_id"].map(lambda n: year.get(n, "unknown"))):
            path = d / "texts" / f"texts-{y}-{stamp}.parquet"
            n = 1
            while path.exists():  # two runs on one day never overwrite each other
                n += 1
                path = d / "texts" / f"texts-{y}-{stamp}-{n}.parquet"
            _write(g, "texts", path)


def text_rows(data_dir: str | Path) -> int:
    """Number of text rows on disk, from parquet metadata only (no text is loaded)."""
    import pyarrow.parquet as pq

    return sum(pq.ParquetFile(f).metadata.num_rows for f in (Path(data_dir) / "texts").glob("*.parquet"))
