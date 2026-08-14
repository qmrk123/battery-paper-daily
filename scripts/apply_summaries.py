"""Apply a summaries JSON to a day file (dedup by title, set relevant + summary_ko).

Useful for offline/manual summaries or overriding the LLM output. The JSON maps
paper id -> {"relevant": bool, "summary_ko": str}.

    python scripts/apply_summaries.py --date 2026-08-14 summaries.json
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


def run(date: str, summaries_path: str) -> None:
    store = Store()
    papers = store.load_day(date)
    if not papers:
        print(f"no data for {date}"); return

    cands = {canonical_key(p.doi, p.id): p for p in papers}
    deduped = list(dedup_by_title(cands).values())

    sums = json.loads(Path(summaries_path).read_text(encoding="utf-8"))
    applied = 0
    for p in deduped:
        s = sums.get(p.id)
        if s:
            p.relevant = bool(s.get("relevant", True))
            p.summary_ko = s.get("summary_ko")
            applied += 1

    deduped.sort(key=lambda p: (p.published or "", p.title), reverse=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.save_day(date, deduped, generated_at=now)
    store.write_index(load_config().topic_meta(), updated_at=now)
    print(f"papers {len(papers)} -> deduped {len(deduped)}; summaries applied {applied}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("summaries", help="path to {id: {relevant, summary_ko}} JSON")
    args = ap.parse_args()
    run(args.date, args.summaries)
