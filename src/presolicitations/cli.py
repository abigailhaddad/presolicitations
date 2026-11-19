"""presol <command>. Dry-run is the default; nothing is published without --publish."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import card, config, csvsource, hf, integrity, pipeline, seed, store
from .sam import Client


def _now(args) -> dt.datetime:
    return dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _csv(args):
    """(presolicitation rows, counts). From a local file if given, else the live CSV."""
    raw = open(args.csv_file, "rb") if args.csv_file else csvsource.fetch_csv(args.csv_url or config.CSV_URL)
    try:
        return csvsource.presolicitations(raw)
    finally:
        raw.close()


def _load(args):
    data = Path(args.data_dir)
    if args.pull and not hf.pull(data, args.repo):
        raise SystemExit(f"dataset {args.repo} not found on Hugging Face; run `presol seed` and publish first")
    tables = store.load_tables(data)
    if tables["notices"].empty:
        raise SystemExit(f"no tables in {data}: run `presol seed` (or `--pull`) first")
    return tables


def _finish(args, before, after, summary, csv_counts=None):
    """Integrity check, then write; publish only if asked. Any failure writes nothing."""
    bad = integrity.check(before, after, summary, csv_counts)
    if bad:
        print(json.dumps({**summary, "integrity_failures": bad}, indent=1), file=sys.stderr)
        raise SystemExit(f"integrity check failed, nothing written: {'; '.join(bad)}")
    store.save_all(args.data_dir, after, stamp=summary["as_of"])
    full = store.load_tables(args.data_dir)
    (Path(args.data_dir).parent / "README.md").write_text(
        card.render(full, as_of=summary["as_of"], text_row_count=store.text_rows(args.data_dir)))
    summary["published"] = False
    if args.publish and not args.dry_run:
        hf.push(args.data_dir, args.repo, message=f"{args.command} {summary['as_of']}")
        summary["published"] = True
    print(json.dumps(summary, indent=1, default=str))


def cmd_seed(args):
    lst, fin = json.load(open(args.list)), json.load(open(args.final))
    tables = seed.seed_tables(lst, fin)
    store.save_all(args.data_dir, tables, stamp=seed.SEED_DATE)
    full = store.load_tables(args.data_dir)
    (Path(args.data_dir).parent / "README.md").write_text(
        card.render(full, as_of=seed.SEED_DATE, text_row_count=store.text_rows(args.data_dir)))
    print(json.dumps({k: len(v) for k, v in tables.items()}))


def cmd_csv_counts(args):
    raw = open(args.csv_file, "rb") if args.csv_file else csvsource.fetch_csv(config.CSV_URL)
    print(json.dumps(csvsource.count_by_year(raw), indent=1))


def cmd_daily(args):
    before = _load(args)
    rows, counts = _csv(args)
    after, summary = pipeline.run_daily(
        before, client=Client(), csv_rows=rows, now=_now(args), since=args.since, limit=args.limit,
        recheck_days=args.recheck_days, max_live_checks=args.max_live_checks)
    summary["csv"] = counts
    _finish(args, before, after, summary, counts)


def cmd_backfill(args):
    before = _load(args)
    if args.phase == "meta":
        rows, counts = _csv(args)
        after, summary = pipeline.backfill_meta(before, csv_rows=rows, now=_now(args))
        summary["csv"] = counts
        _finish(args, before, after, summary, counts)
    else:
        after, summary = pipeline.backfill_fetch(
            before, client=Client(), now=_now(args), offset=args.offset, limit=args.limit,
            max_seconds=args.max_seconds, sample=args.sample, seed=args.seed)
        _finish(args, before, after, summary)


def cmd_card(args):
    full = store.load_tables(args.data_dir)
    out = Path(args.data_dir).parent / "README.md"
    out.write_text(card.render(full, as_of=args.now or full["notices"]["last_seen"].max(),
                               text_row_count=store.text_rows(args.data_dir)))
    print(out)


def cmd_check(args):
    t = store.load_tables(args.data_dir)
    bad = integrity.check(t, t, {"attachment_text": 0, "attachment_no_text": 0})
    print(json.dumps({"ok": not bad, "problems": bad}, indent=1))
    return 1 if bad else 0


def cmd_pull(args):
    print("pulled" if hf.pull(args.data_dir, args.repo) else "dataset not found")


def cmd_push(args):
    hf.push(args.data_dir, args.repo)
    print("pushed")


def _common(p: argparse.ArgumentParser, sub: bool) -> None:
    """Options accepted both before and after the command (`presol daily --dry-run` works)."""
    d = (lambda v: argparse.SUPPRESS) if sub else (lambda v: v)
    p.add_argument("--data-dir", default=d("dataset/data"),
                   help="must be named data; its parent holds the dataset card (README.md)")
    p.add_argument("--repo", default=d(config.HF_REPO))
    p.add_argument("--now", default=d(None), help="ISO time override (tests)")
    p.add_argument("--csv-file", default=d(None), help="read the CSV from this file instead of downloading it")
    p.add_argument("--csv-url", default=d(None),
                   help="read a different SAM.gov CSV (e.g. a yearly archive: .../Archived%%20Data/FY2025_archived_opportunities.csv)")
    p.add_argument("--pull", action="store_true", default=d(False),
                   help="download the current tables from Hugging Face first")
    p.add_argument("--publish", action="store_true", default=d(False),
                   help="upload to Hugging Face (default: dry run)")
    p.add_argument("--dry-run", action="store_true", default=d(False), help="never publish, even with --publish")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="presol")
    _common(ap, sub=False)
    sp = ap.add_subparsers(dest="command", required=True)

    class Sub:
        def add_parser(self, *a, **k):
            p = sp.add_parser(*a, **k)
            _common(p, sub=True)
            return p

    sub = Sub()

    s = sub.add_parser("seed", help="build the tables from the first manual pull")
    s.add_argument("--list", required=True)
    s.add_argument("--final", required=True)
    s.set_defaults(fn=cmd_seed)
    sub.add_parser("csv-counts", help="read-only census of the CSV").set_defaults(fn=cmd_csv_counts)

    d = sub.add_parser("daily", help="collect new notices, fetch attachments, refresh live status")
    d.add_argument("--since", default=config.WINDOW_START)
    d.add_argument("--limit", type=int, help="process at most N notices this run")
    d.add_argument("--recheck-days", type=int, default=30)
    d.add_argument("--max-live-checks", type=int, default=600)
    d.set_defaults(fn=cmd_daily)

    b = sub.add_parser("backfill", help="load every presolicitation in the CSV, then fetch in chunks")
    b.add_argument("phase", choices=["meta", "fetch"])
    b.add_argument("--offset", type=int, default=0)
    b.add_argument("--limit", type=int, default=300)
    b.add_argument("--max-seconds", type=float, help="stop starting new notices after this long")
    b.add_argument("--sample", type=int, help="random sample of pending notices (cost estimate)")
    b.add_argument("--seed", type=int, default=7)
    b.set_defaults(fn=cmd_backfill)

    sub.add_parser("card", help="regenerate README.md").set_defaults(fn=cmd_card)
    sub.add_parser("check", help="run the integrity checks on the tables on disk").set_defaults(fn=cmd_check)
    sub.add_parser("pull").set_defaults(fn=cmd_pull)
    sub.add_parser("push").set_defaults(fn=cmd_push)

    args = ap.parse_args(argv)
    args.command = getattr(args, "command", None)
    return args.fn(args) or 0
