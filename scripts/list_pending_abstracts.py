"""List visible '(제목 기반 추정)' papers whose abstract is still missing, newest first,
so their abstracts can be recovered from a logged-in campus browser session.

Publisher abstracts (Elsevier/Springer) aren't in OpenAlex/Crossref/S2 and our API keys
aren't entitled to them, so recovery needs the browser (Claude-in-Chrome): navigate to
each DOI and read the abstract (Nature #Abs1-content; ScienceDirect/Joule div.abstract
whose heading is Abstract/Summary). This CANNOT run in CI (no browser session).

    python scripts/list_pending_abstracts.py                 # all publishers
    python scripts/list_pending_abstracts.py --prefix 10.1016 --limit 20   # recent Elsevier
    python scripts/list_pending_abstracts.py --since 2026-09-15            # today-onward only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="", help="DOI prefix filter, e.g. 10.1016 (Elsevier), 10.1038 (Nature)")
    ap.add_argument("--since", default="", help="only papers published on/after this YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    seen: dict[str, dict] = {}
    for f in glob.glob(os.path.join(_ROOT, "data", "*.json")):
        stem = os.path.basename(f)[:-5]
        if not _PERIOD.match(stem):
            continue
        for p in json.load(open(f, encoding="utf-8")).get("papers", []):
            seen.setdefault(p["id"], p)

    pend = [p for p in seen.values()
            if p.get("doi") and p.get("summary_ko") and "제목 기반" in p["summary_ko"]
            and not (p.get("abstract_en") or "").strip() and p.get("relevant") is not False
            and p["doi"].startswith(a.prefix)
            and (not a.since or (p.get("published") or "") >= a.since)]
    pend.sort(key=lambda p: (p.get("published") or ""), reverse=True)

    print(f"pending abstract recovery: {len(pend)} (prefix={a.prefix or 'any'} since={a.since or 'any'})")
    for p in pend[:a.limit]:
        print(f"{p.get('published')}  {p['doi']}  {(p.get('venue') or '')[:28]:28}  {(p.get('title') or '')[:55]}")


if __name__ == "__main__":
    main()
