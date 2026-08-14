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
        dates = [
            p.stem for p in self.data_dir.glob("*.json")
            if p.stem not in ("index", "seen") and _is_date(p.stem)
        ]
        return sorted(dates, reverse=True)

    def write_index(self, topics: list[dict], updated_at: str) -> None:
        _write_json(self.data_dir / "index.json", {
            "dates": self.list_dates(),
            "topics": topics,
            "updated_at": updated_at,
        })


def _is_date(s: str) -> bool:
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit()
