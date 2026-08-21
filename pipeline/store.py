"""Persistence for the pipeline: seen-ids ledger + per-day JSON + index.

Layout (all committed to the repo — this IS the site's content):
    data/seen.json          {paper_id: first_seen_date}   dedup ledger
    data/YYYY-MM-DD.json     {date, generated_at, papers:[...]}
    data/index.json          {dates:[...], topics:[...], updated_at}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Paper

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )


class Store:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.seen_path = data_dir / "seen.json"

    # ---- seen ledger -------------------------------------------------
    def load_seen(self) -> dict[str, str]:
        return _read_json(self.seen_path, {})

    def save_seen(self, seen: dict[str, str]) -> None:
        _write_json(self.seen_path, seen)

    # ---- daily files -------------------------------------------------
    def day_path(self, date: str) -> Path:
        return self.data_dir / f"{date}.json"

    def load_day(self, date: str) -> list[Paper]:
        obj = _read_json(self.day_path(date), None)
        if not obj:
            return []
        return [Paper.from_dict(p) for p in obj.get("papers", [])]

    def save_day(self, date: str, papers: list[Paper], generated_at: str) -> None:
        _write_json(self.day_path(date), {
            "date": date,
            "generated_at": generated_at,
            "count": len(papers),
            "papers": [p.to_dict() for p in papers],
        })

    def list_dates(self) -> list[str]:
        keys = [
            p.stem for p in self.data_dir.glob("*.json")
            if p.stem not in ("index", "seen", "journals") and _is_period(p.stem)
        ]
        return sorted(keys, reverse=True)

    def write_index(self, topics: list[dict], updated_at: str) -> None:
        _write_json(self.data_dir / "index.json", {
            "dates": self.list_dates(),
            "topics": topics,
            "updated_at": updated_at,
        })

    def rebuild_month(self, month: str, generated_at: str) -> int:
        """Aggregate all data/<month>-DD.json daily files into data/<month>.json so
        the running month can be browsed at once, alongside its per-day files (older
        months already exist only as standalone monthly archives from the backfill).
        Dedup by id, preferring the record that carries a summary / cached image.
        `month` is a YYYY-MM key; returns the aggregated paper count."""
        pool: dict[str, dict] = {}
        for f in sorted(self.data_dir.glob(f"{month}-*.json")):
            obj = _read_json(f, None) or {}
            for p in obj.get("papers", []):
                pid = p.get("id")
                if not pid:
                    continue
                pool[pid] = _richer(pool[pid], p) if pid in pool else p
        papers = sorted(pool.values(),
                        key=lambda p: (p.get("published") or "", p.get("title") or ""),
                        reverse=True)
        _write_json(self.data_dir / f"{month}.json", {
            "date": month,
            "generated_at": generated_at,
            "count": len(papers),
            "papers": papers,
        })
        return len(papers)


def _richer(a: dict, b: dict) -> dict:
    """Prefer the record carrying a Korean summary / cached image (used when the same
    paper id shows up in more than one daily file)."""
    def score(p: dict) -> tuple[bool, bool]:
        return bool(p.get("summary_ko")), bool(p.get("image") and p["image"].get("cached"))
    return a if score(a) >= score(b) else b


def _is_period(s: str) -> bool:
    """Accept a day key YYYY-MM-DD or a month key YYYY-MM."""
    parts = s.split("-")
    return (len(s) in (7, 10) and all(p.isdigit() for p in parts)
            and len(parts[0]) == 4 and (len(parts) in (2, 3)))
