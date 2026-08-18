"""Retro-apply the journal-quality gate to existing day files.

New pipeline runs gate by OpenAlex source id; older day files predate that, so
here we look each paper's journal up by venue NAME, attach journal_metric, and
drop journals below the threshold (and arXiv preprints).

    python scripts/apply_journal_filter.py 2026-08-14 2026-08-18
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config
from pipeline.store import Store

UA = "battery-paper-daily/0.1 (mailto:qmrk123@hanyang.ac.kr)"
API = "https://api.openalex.org/sources"


def venue_metric(session: requests.Session, name: str):
    try:
        r = session.get(API, params={
            "filter": f"display_name.search:{name}",
            "select": "display_name,summary_stats,works_count,type",
            "per-page": 5, "mailto": "qmrk123@hanyang.ac.kr"}, timeout=20)
        res = r.json().get("results", [])
        journals = [x for x in res if x.get("type") == "journal"] or res
        exact = [x for x in journals if x["display_name"].lower() == name.lower()]
        x = (exact or sorted(journals, key=lambda z: z.get("works_count", 0),
                             reverse=True))[0] if journals else None
        if not x:
            return None
        m = (x.get("summary_stats") or {}).get("2yr_mean_citedness")
        return round(m, 2) if m is not None else None
    except Exception:
        return None
    finally:
        time.sleep(0.15)


def run(dates: list[str]) -> None:
    cfg = load_config()
    thr, keep_pre = cfg.min_journal_metric, cfg.include_preprints
    store = Store()
    session = requests.Session()
    session.headers["User-Agent"] = UA
    name_cache: dict[str, float | None] = {}

    for date in dates:
        papers = store.load_day(date)
        if not papers:
            print(f"{date}: no data"); continue
        kept, dropped = [], []
        for p in papers:
            if p.source == "arxiv" and not keep_pre:
                dropped.append((p.venue or "arXiv", None)); continue
            if p.journal_metric is None and p.venue:
                if p.venue not in name_cache:
                    name_cache[p.venue] = venue_metric(session, p.venue)
                p.journal_metric = name_cache[p.venue]
            if p.journal_metric is not None and p.journal_metric < thr:
                dropped.append((p.venue, p.journal_metric)); continue
            kept.append(p)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.save_day(date, kept, generated_at=now)
        print(f"{date}: {len(papers)} -> {len(kept)} kept, {len(dropped)} dropped")
        for v, m in sorted(dropped, key=lambda z: (z[1] is None, z[1] or 0)):
            print(f"    drop [{m if m is not None else 'preprint'}] {v}")

    store.write_index(cfg.topic_meta(),
                      updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


if __name__ == "__main__":
    run(sys.argv[1:] or [])
