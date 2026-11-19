import re

import pandas as pd
import pytest

from presolicitations import card, classify, seed, store


def test_seed_reconciles_with_source_field_by_field(sample, seeded):
    assert seed.reconcile(seeded, sample["list"], sample["final3"]) == []


def test_seed_counts_and_every_status_is_known(sample, seeded):
    assert len(seeded["notices"]) == len(sample["list"])
    assert len(seeded["attachments"]) == sum(len(r["files"]) for r in sample["final3"])
    assert set(seeded["attachments"]["status"]) <= set(classify.STATUSES)
    # text rows are exactly the attachments the source had text for (including whitespace-only)
    assert len(seeded["texts"]) == sum(1 for r in sample["final3"] for x in r["files"] if x.get("text") is not None)


def test_seed_statuses_cover_the_traps(seeded):
    got = set(seeded["attachments"]["status"])
    assert {"text", "no_text", "link", "deleted", "controlled", "big", "skipped_format", "extract_error"} <= got


def test_seed_vanished_notices_are_recorded_not_dropped(seeded):
    n = seeded["notices"]
    v = n[n["vanished"]]
    assert len(v) >= 1 and (v["vanished_on"] == seed.SEED_DATE).all() and (v["live_http"] == 404).all()


def test_seed_keeps_superseded_and_flags_them(seeded):
    assert (seeded["notices"]["live_latest"] == False).sum() >= 1  # noqa: E712


def test_seed_attachment_text_chars_match_text(seeded):
    t = seeded["texts"].set_index(["notice_id", "resource_id"])["text"]
    for r in seeded["attachments"].itertuples():
        k = (r.notice_id, r.resource_id)
        want = len(t[k].strip()) if k in t.index else 0
        assert r.text_chars == want and r.has_text == (want > 0)


def test_reconcile_catches_a_corrupted_text(sample, seeded):
    t = seeded["texts"].copy()
    t.loc[t.index[0], "text"] = t.loc[t.index[0], "text"] + " X"
    bad = seed.reconcile({**seeded, "texts": t}, sample["list"], sample["final3"])
    assert any("text differs" in b for b in bad)


def test_store_round_trip_shards_by_posted_year(tmp_path, seeded):
    store.save_all(tmp_path, seeded, stamp="2026-11-18")
    years = {store.year_of(d) for d in seeded["notices"]["posted_date"]}
    assert {p.name for p in (tmp_path / "notices").glob("*.parquet")} == {f"notices-{y}.parquet" for y in years}
    back = store.load_tables(tmp_path, with_texts=True)
    for t in store.SCHEMAS:
        assert len(back[t]) == len(seeded[t])
    assert store.text_rows(tmp_path) == len(seeded["texts"])


def test_texts_are_append_only_new_file_per_run(tmp_path, seeded):
    store.save_all(tmp_path, seeded, stamp="2026-11-18")
    before = {p.name: p.stat().st_mtime_ns for p in (tmp_path / "texts").glob("*.parquet")}
    new = {**store.load_tables(tmp_path), "texts": seeded["texts"].head(2)}
    store.save_all(tmp_path, new, stamp="2026-11-18")  # same stamp: must not overwrite
    after = {p.name: p.stat().st_mtime_ns for p in (tmp_path / "texts").glob("*.parquet")}
    assert set(before) < set(after) and all(after[k] == v for k, v in before.items())


def test_store_refuses_to_drop_a_year_shard(tmp_path, seeded):
    store.save_all(tmp_path, seeded, stamp="2026-11-18")
    tables = store.load_tables(tmp_path)
    store.empty("notices").to_parquet(tmp_path / "notices" / "notices-1999.parquet")  # a year the run no longer has
    with pytest.raises(RuntimeError, match="append-only"):
        store.save_all(tmp_path, tables, stamp="2026-11-19")


def test_card_numbers_come_from_the_data(seeded):
    text = card.render(seeded, as_of="2026-11-18", text_row_count=len(seeded["texts"]))
    n = seeded["notices"]
    assert f"{len(n):,} notices posted {n['posted_date'].min()} to {n['posted_date'].max()}" in text
    assert f"{len(seeded['attachments']):,} attachments" in text
    # change the data, the card changes
    smaller = {**seeded, "notices": n.head(3), "attachments": seeded["attachments"].iloc[:0]}
    assert card.render(smaller, as_of="2026-11-18") != text
    assert "3 notices posted" in card.render(smaller, as_of="2026-11-18")


def test_card_says_what_it_must(seeded):
    text = card.render(seeded, as_of="2026-11-18")
    assert text.startswith("---\nlicense: cc0-1.0")
    for must in ("as published on SAM.gov", "first_seen", "posted_date", "Append-only", "scans", "Controlled",
                 "undercounted", "configs:"):
        assert must in text, must


def test_card_year_table_rows_sum_to_total(seeded):
    text = card.render(seeded, as_of="2026-11-18")
    rows = re.findall(r"^\| (\d{4}|unknown) \| ([\d,]+) \|", text, re.M)
    assert "not yet*, not *none" in text
    assert sum(int(c.replace(",", "")) for _, c in rows) == len(seeded["notices"])
