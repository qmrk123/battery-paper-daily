"""Apply a summaries JSON to day/month files (set topics + summary_ko).

The JSON maps paper id -> {"topics": [...], "summary_ko": "..."} (or the older
{"relevant": bool, "summary_ko": ...}). With --date it touches one file (and
dedups by title); otherwise it applies across every day/month file by id.

    python scripts/apply_summaries.py summaries.json                 # all files
    python scripts/apply_summaries.py summaries.json --date 2026-08-14
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config
from pipeline.fetch import dedup_by_title
from pipeline.models import canonical_key
from pipeline.store import Store

VALID = {"li-metal", "na-metal", "high-ni-ncm", "li-rich"}


def _apply(paper, entry) -> None:
    if "topics" in entry:
        topics = [t for t in (entry.get("topics") or []) if t in VALID]
        paper.topics = topics
        paper.relevant = bool(topics)
    elif "relevant" in entry:
        paper.relevant = bool(entry["relevant"])
    if entry.get("summary_ko"):
        paper.summary_ko = entry["summary_ko"]


def run(summaries_path: str, date: str | None) -> None:
    sums = json.loads(Path(summaries_path).read_text(encoding="utf-8"))
    store = Store()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dates = [date] if date else store.list_dates()
    applied = 0
    for d in dates:
        papers = store.load_day(d)
        if not papers:
            continue
        if date:                                   # single-file mode also dedups
            cands = {canonical_key(p.doi, p.id): p for p in papers}
            papers = list(dedup_by_title(cands).values())
        hit = 0
        for p in papers:
            e = sums.get(p.id)
            if e:
                _apply(p, e); hit += 1
        if hit or date:
            papers.sort(key=lambda p: (p.published or "", p.title), reverse=True)
            store.save_day(d, papers, generated_at=now)
            print(f"  {d}: applied {hit}")
            applied += hit
    store.write_index(load_config().topic_meta(), updated_at=now)
    print(f"applied {applied} summaries")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("summaries", help="path to {id: {topics, summary_ko}} JSON")
    ap.add_argument("--date", default=None, help="restrict to one day/month file")
    a = ap.parse_args()
    run(a.summaries, a.date)
