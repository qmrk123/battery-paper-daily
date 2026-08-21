"""One-off: re-bucket every August daily file by PUBLICATION date (the date shown
on each card) so the daily archive reflects when papers actually came out, instead
of the single 2026-08-21 catch-up lump (all first_seen on the 30-day backfill run).

August-published -> data/2026-08-DD.json (one file per publication day).
Earlier-month-published stragglers -> that month's monthly archive (data/2026-07.json).
Going-forward daily runs keep filing by first_seen (correct for surfacing new
papers the day they're discovered); this only cleans up the historical lump.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone

from pipeline.config import load_config
from pipeline.store import Store

DATA = "data"
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path):
    return json.load(open(path, encoding="utf-8"))


def dump(path, date_key, papers):
    papers = sorted(papers, key=lambda p: (p.get("published") or "", p.get("title") or ""),
                    reverse=True)
    json.dump({"date": date_key, "generated_at": NOW, "count": len(papers),
               "papers": papers},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def richer(a, b):
    """Prefer the record carrying a Korean summary / cached image."""
    score = lambda p: (bool(p.get("summary_ko")), bool(p.get("image") and p["image"].get("cached")))
    return a if score(a) >= score(b) else b


# 1) gather every paper currently in an August daily file
aug_files = sorted(glob.glob(os.path.join(DATA, "2026-08-*.json")))
pool: dict[str, dict] = {}
for f in aug_files:
    for p in load(f).get("papers", []):
        pid = p["id"]
        pool[pid] = richer(pool[pid], p) if pid in pool else p
print(f"collected {len(pool)} papers from {len(aug_files)} August daily files")

# 2) bucket by publication day
aug_buckets: dict[str, list] = {}
older: dict[str, list] = {}          # month-key -> papers (fold into monthly archive)
for p in pool.values():
    day = (p.get("published") or p.get("first_seen") or "")[:10]
    if day.startswith("2026-08"):
        aug_buckets.setdefault(day, []).append(p)
    else:
        older.setdefault(day[:7], []).append(p)

# 3) delete the old August daily files, then write fresh per-publication-day files
for f in aug_files:
    os.remove(f)
for day, papers in sorted(aug_buckets.items()):
    dump(os.path.join(DATA, f"{day}.json"), day, papers)
    print(f"  wrote {day}.json ({len(papers)})")

# 4) fold earlier-month stragglers into their monthly archive (dedup by id)
for month, papers in sorted(older.items()):
    mp = os.path.join(DATA, f"{month}.json")
    existing = {q["id"]: q for q in (load(mp).get("papers", []) if os.path.exists(mp) else [])}
    for p in papers:
        existing[p["id"]] = richer(existing[p["id"]], p) if p["id"] in existing else p
    dump(mp, month, list(existing.values()))
    print(f"  folded {len(papers)} straggler(s) into {month}.json (now {len(existing)})")

# 5) rebuild index
cfg = load_config()
Store().write_index(cfg.topic_meta(), updated_at=NOW)
print("index rebuilt")
