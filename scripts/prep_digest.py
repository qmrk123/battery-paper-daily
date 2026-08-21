"""Gather the most recent summarized papers into _digest_input.json — the input for
the CI "weekly brief" LLM step (see .github/workflows/daily.yml). Kept tiny and
dependency-free so it runs in the same job that has no extra installs on push."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def main(limit: int = 40) -> None:
    pool: dict[str, dict] = {}
    for f in sorted((ROOT / "data").glob("*.json")):
        if not _PERIOD.match(f.stem):
            continue
        for p in json.loads(f.read_text(encoding="utf-8")).get("papers", []):
            if p.get("relevant") is False or not p.get("summary_ko"):
                continue
            pid = p.get("id")
            if pid:
                pool[pid] = p               # dedup; monthly aggregate may repeat a daily id
    papers = sorted(pool.values(), key=lambda p: (p.get("published") or ""), reverse=True)[:limit]
    slim = [{
        "id": p["id"], "title": p.get("title"), "venue": p.get("venue"),
        "url": p.get("url"), "topics": p.get("topics") or [],
        "summary_ko": p.get("summary_ko"), "published": p.get("published"),
    } for p in papers]
    out = ROOT / "_digest_input.json"
    out.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out.name} ({len(slim)} papers)")


if __name__ == "__main__":
    main()
