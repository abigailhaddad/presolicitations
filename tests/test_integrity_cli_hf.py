import io
import json
import types

import pandas as pd
import pytest

from presolicitations import cli, config, hf, integrity, pipeline, store
from conftest import NOW, FakeClient, MINIMAL_PDF, att, csv_row
from test_classify_extract_csv import csv_bytes


def run(seeded, **stats):
    after = {**seeded, "texts": store.empty("texts")}  # no new texts this run
    return integrity.check(seeded, after, {"attachment_text": 0, "attachment_no_text": 0, **stats})


def test_integrity_passes_on_unchanged_seed(seeded):
    assert run(seeded) == []


def test_integrity_duplicate_notice_ids(seeded):
    dup = {**seeded, "notices": pd.concat([seeded["notices"], seeded["notices"].head(1)], ignore_index=True)}
    assert any("duplicate notice_id" in b for b in integrity.check(seeded, dup, {"attachment_text": 0, "attachment_no_text": 0}))


def test_integrity_counts_may_not_drop(seeded):
    short = {**seeded, "attachments": seeded["attachments"].iloc[1:], "status_history": seeded["status_history"].iloc[1:]}
    bad = integrity.check(seeded, short, {"attachment_text": 0, "attachment_no_text": 0})
    assert any("attachments present before" in b for b in bad) and any("status_history shrank" in b for b in bad)


def test_integrity_empty_pdf_rate(seeded):
    assert any("came back empty" in b for b in run(seeded, pdf_fetched=100, pdf_empty=60))
    assert run(seeded, pdf_fetched=100, pdf_empty=3) == []
    assert run(seeded, pdf_fetched=5, pdf_empty=5) == []  # too few to judge


def test_integrity_live_error_rate(seeded):
    assert any("live checks failed" in b for b in run(seeded, live_checks=10, live_errors=30))


def test_integrity_partial_csv(seeded):
    bad = integrity.check(seeded, seeded, {"attachment_text": 0, "attachment_no_text": 0}, {"presolicitations": 5})
    assert any("partial download" in b for b in bad)


def test_integrity_texts_must_match_new_attachments(seeded):
    bad = integrity.check(seeded, {**seeded}, {"attachment_text": 0, "attachment_no_text": 0})
    assert any("new texts" in b for b in bad)  # seeded texts passed as 'new' without matching stats


def test_integrity_unknown_status_and_bad_date(seeded):
    a = seeded["attachments"].copy()
    a.loc[a.index[0], "status"] = "weird"
    n = seeded["notices"].copy()
    n.loc[n.index[0], "posted_date"] = "11/19/2026"
    bad = integrity.check(seeded, {**seeded, "attachments": a, "notices": n}, {"attachment_text": 0, "attachment_no_text": 0})
    assert any("unknown" in b for b in bad) and any("posted_date" in b for b in bad)


# ---- CLI end to end ------------------------------------------------------------------------
@pytest.fixture
def workdir(tmp_path, seeded):
    d = tmp_path / "dataset" / "data"
    store.save_all(d, seeded, stamp="2026-11-18")
    return d


def fake_csv(tmp_path, rows):
    p = tmp_path / "f.csv"
    p.write_bytes(csv_bytes(rows))
    return p


def row(nid, posted="2026-11-18"):
    return [nid, "t", "S", "D", "SUB", "O", f"{posted} 00:00:00.000-05", "Presolicitation", "desc", "l", "x@y.gov"]


def keep_all_seeded(seeded):
    return [row(n, p) for n, p in zip(seeded["notices"]["notice_id"], seeded["notices"]["posted_date"])]


def many(seeded):
    # a CSV with enough rows to clear the partial-download guard
    return keep_all_seeded(seeded) + [row(f"X{i}") for i in range(config.MIN_CSV_PRESOL_ROWS)]


def test_cli_daily_dry_run_writes_data_card_and_does_not_publish(workdir, tmp_path, seeded, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Client", lambda: FakeClient(resources={"X0": [att("a")]}, files={"a": MINIMAL_PDF}))
    pushed = []
    monkeypatch.setattr(hf, "push", lambda *a, **k: pushed.append(a))
    rc = cli.main(["--data-dir", str(workdir), "--csv-file", str(fake_csv(tmp_path, many(seeded))),
                   "--now", "2026-11-19T06:30:00", "daily", "--limit", "3"])
    assert rc == 0 and pushed == []
    out = json.loads(capsys.readouterr().out)
    assert out["published"] is False and out["new_notices"] == 3
    after = store.load_tables(workdir)
    assert len(after["notices"]) == len(seeded["notices"]) + 3
    assert "notices posted" in (workdir.parent / "README.md").read_text()
    assert store.text_rows(workdir) == len(seeded["texts"]) + 1


def test_cli_publish_flag_publishes_but_dry_run_wins(workdir, tmp_path, seeded, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Client", lambda: FakeClient())
    pushed = []
    monkeypatch.setattr(hf, "push", lambda *a, **k: pushed.append(a))
    base = ["--data-dir", str(workdir), "--csv-file", str(fake_csv(tmp_path, many(seeded))), "--now", "2026-11-19T06:30:00"]
    cli.main(base + ["--publish", "daily", "--limit", "1"])
    assert len(pushed) == 1
    cli.main(base + ["--publish", "--dry-run", "daily", "--limit", "1"])
    assert len(pushed) == 1


def test_cli_integrity_failure_writes_nothing_and_publishes_nothing(workdir, tmp_path, seeded, monkeypatch):
    monkeypatch.setattr(cli, "Client", lambda: FakeClient())
    pushed = []
    monkeypatch.setattr(hf, "push", lambda *a, **k: pushed.append(a))
    before = sorted((p.name, p.stat().st_mtime_ns) for p in workdir.rglob("*.parquet"))
    with pytest.raises(SystemExit, match="integrity check failed"):
        # a CSV with one row looks like a partial download
        cli.main(["--data-dir", str(workdir), "--csv-file", str(fake_csv(tmp_path, [row("ONLY")])),
                  "--publish", "daily"])
    assert sorted((p.name, p.stat().st_mtime_ns) for p in workdir.rglob("*.parquet")) == before and pushed == []


def test_cli_schema_drift_in_the_csv_fails(workdir, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_bytes(csv_bytes([["1", "t"]], header=["NoticeId", "Title"]))
    with pytest.raises(Exception, match="columns missing"):
        cli.main(["--data-dir", str(workdir), "--csv-file", str(p), "daily"])


def test_cli_backfill_meta_then_fetch_chunk(workdir, tmp_path, seeded, monkeypatch, capsys):
    old = [row(f"OLD{i}", posted=f"2017-02-{i + 1:02d}") for i in range(4)]
    csvp = fake_csv(tmp_path, many(seeded)[:len(seeded["notices"])] + old + [row(f"X{i}") for i in range(config.MIN_CSV_PRESOL_ROWS)])
    monkeypatch.setattr(cli, "Client", lambda: FakeClient())
    cli.main(["--data-dir", str(workdir), "--csv-file", str(csvp), "--now", "2026-11-19T06:30:00", "backfill", "meta"])
    t = store.load_tables(workdir)
    assert (t["notices"]["source"] == "backfill").sum() == 4 + config.MIN_CSV_PRESOL_ROWS
    assert {"2017"} <= {store.year_of(d) for d in t["notices"]["posted_date"]}
    capsys.readouterr()
    cli.main(["--data-dir", str(workdir), "--now", "2026-11-20T06:30:00", "backfill", "fetch", "--limit", "10"])
    out = json.loads(capsys.readouterr().out)
    assert out["chunk_done"] == 10 and out["pending_after"] == 4 + config.MIN_CSV_PRESOL_ROWS - 10


def test_hf_push_requires_token_and_data_dir_name(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    (tmp_path / "data").mkdir()
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        hf.push(tmp_path / "data")
    with pytest.raises(ValueError, match="named 'data'"):
        hf.push(tmp_path)


def test_hf_never_deletes_remote_files():
    assert "delete_patterns" not in hf.push.__code__.co_names and "delete_patterns" not in hf.push.__code__.co_consts


def test_flags_work_after_the_command_too(workdir, tmp_path, seeded, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Client", lambda: FakeClient())
    pushed = []
    monkeypatch.setattr(hf, "push", lambda *a, **k: pushed.append(a))
    rc = cli.main(["daily", "--dry-run", "--publish", "--limit", "1", "--data-dir", str(workdir),
                   "--csv-file", str(fake_csv(tmp_path, many(seeded))), "--now", "2026-11-19T06:30:00"])
    assert rc == 0 and pushed == [] and json.loads(capsys.readouterr().out)["published"] is False
