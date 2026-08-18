"""One-off historical backfill: gather journal-quality papers over a date range
and bucket them into monthly archive files (data/YYYY-MM.json).

Uses the same funnel as the daily run (search -> regex/context -> journal gate),
but with its own (usually stricter) journal threshold and NO preprints. Papers
already in seen.json are skipped, and new ones are added to it so the daily run
won't re-collect them.

    python scripts/backfill.py --from 2026-01 --to 2026-07 --metric 8.7
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config
from pipeline.fetch import gather_candidates
from pipeline.models import canonical_key
from pipeline.store import Store


def run(from_month: str, to_month: str, metric: float,
        max_per_topic: int = 3000, log=print) -> None:
    cfg = load_config()
    cfg.min_journal_metric = metric      # backfill can be stricter than the daily gate
    cfg.include_preprints = False
    cfg.max_per_topic = max_per_topic

    log(f"== backfill {from_month}..{to_month}  journal>= {metric}  (no preprints) ==")
    res = gather_candidates(cfg, from_date=f"{from_month}-01", use_arxiv=False, log=log)

    store = Store()
    seen = store.load_seen()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    buckets: dict[str, list] = collections.defaultdict(list)
    skipped_seen = 0
    for key, p in res.candidates.items():
        month = (p.published or "")[:7]
        if not (from_month <= month <= to_month):
            continue
        if key in seen:
            skipped_seen += 1
            continue
        p.first_seen = today
        seen[key] = today
        buckets[month].append(p)

    for month, papers in sorted(buckets.items()):
        existing = {p.id: p for p in store.load_day(month)}
        for p in papers:
            existing[p.id] = p
        merged = sorted(existing.values(),
                        key=lambda p: (p.published or "", p.title), reverse=True)
        store.save_day(month, merged, generated_at=now)
        log(f"  {month}: +{len(papers)} new  (file now {len(merged)})")

    store.save_seen(seen)
    store.write_index(cfg.topic_meta(), updated_at=now)
    total_new = sum(len(v) for v in buckets.values())
    log(f"== added {total_new} papers across {len(buckets)} months "
        f"(skipped {skipped_seen} already-seen); summaries still null ==")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_month", default="2026-01")
    ap.add_argument("--to", dest="to_month", default="2026-07")
    ap.add_argument("--metric", type=float, default=8.7)
    ap.add_argument("--max-per-topic", type=int, default=3000)
    a = ap.parse_args()
    run(a.from_month, a.to_month, a.metric, a.max_per_topic)
