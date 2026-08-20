"""CLI entry point for the daily fetch.

    python -m pipeline.main                 # full run: fetch -> data/<today>.json
    python -m pipeline.main --dry-run        # show what would be fetched, write nothing
    python -m pipeline.main --topic ncm --dry-run --show-filtered
    python -m pipeline.main --date 2026-08-14 --no-arxiv

Summaries/images are added in later phases; this phase produces the paper list.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from .config import load_config
from .fetch import gather_candidates, select_new
from .store import Store


# Digests are dated by Korea time: the 06:00 KST cron is 21:00 UTC of the PREVIOUS
# day, so UTC dating labelled "today's" papers with yesterday's date.
KST = timezone(timedelta(hours=9))


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _window_start(run_date: str, days: int) -> str:
    d = datetime.strptime(run_date, "%Y-%m-%d") - timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily battery-paper fetch")
    ap.add_argument("--date", default=_today(), help="run date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--topic", action="append", help="restrict to topic id(s); repeatable")
    ap.add_argument("--no-arxiv", action="store_true", help="skip arXiv preprints")
    ap.add_argument("--dry-run", action="store_true", help="fetch + filter but write nothing")
    ap.add_argument("--show-filtered", action="store_true", help="print each kept candidate")
    ap.add_argument("--window-days", type=int, default=None, help="override rolling window")
    ap.add_argument("--no-journal-first", action="store_true",
                    help="skip journal-first pass (fetch allowlisted journals directly)")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.window_days is not None:
        cfg.window_days = args.window_days
    run_date = args.date
    from_date = _window_start(run_date, cfg.window_days)

    print(f"== fetch run_date={run_date} window>={from_date} "
          f"topics={args.topic or 'all'} arxiv={not args.no_arxiv} ==")

    res = gather_candidates(
        cfg, from_date,
        topic_ids=args.topic,
        use_arxiv=not args.no_arxiv,
        journal_first=not args.no_journal_first,
    )

    store = Store()
    seen = store.load_seen()
    # For dry-run we still want to see how many are "new" without persisting.
    new = select_new(res.candidates, dict(seen) if args.dry_run else seen, run_date)

    print(f"\n== candidates={len(res.candidates)} new={len(new)} "
          f"(already-seen={len(res.candidates) - len(new)}) ==")

    if args.show_filtered:
        for p in new:
            print(f"  + [{','.join(p.topics)}] {p.published}  {p.title[:80]}")
            print(f"      {p.url}  oa={p.oa_status}  abs={'Y' if p.abstract_en else 'N'}")

    if args.dry_run:
        print("\n(dry-run: nothing written)")
        return 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # merge with anything already recorded for this date (idempotent re-runs)
    existing = {p.id: p for p in store.load_day(run_date)}
    for p in new:
        existing[p.id] = p
    merged = sorted(existing.values(),
                    key=lambda p: (p.published or "", p.title), reverse=True)
    store.save_day(run_date, merged, generated_at=now)
    store.save_seen(seen)
    store.write_index(cfg.topic_meta(), updated_at=now)

    print(f"\nwrote data/{run_date}.json ({len(merged)} papers), "
          f"seen.json ({len(seen)} ids), index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
