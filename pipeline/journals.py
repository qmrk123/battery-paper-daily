"""Journal-quality gate: keep only papers from journals at/above a metric
threshold (OpenAlex 2yr_mean_citedness, an impact-factor-like measure).

Metrics are cached in data/journals.json (committed) so we don't re-query the
same journals every day. arXiv/preprints have no journal metric and are dropped
unless `include_preprints` is set.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable, Optional

import requests

from .models import Paper

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "journals.json"
API = "https://api.openalex.org/sources"
CONTACT = os.environ.get("CONTACT_EMAIL", "qmrk123@hanyang.ac.kr")
UA = f"battery-paper-daily/0.1 (mailto:{CONTACT})"


def load_cache(path: Path = CACHE_PATH) -> dict[str, dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict, path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True),
                    encoding="utf-8")


def fetch_source_metrics(source_ids: Iterable[str], session: requests.Session,
                         batch: int = 40) -> dict[str, dict]:
    """Batch-fetch {source_id: {name, metric}} from OpenAlex."""
    ids = [s for s in source_ids if s]
    out: dict[str, dict] = {}
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        params = {
            "filter": "ids.openalex:" + "|".join(chunk),
            "select": "id,display_name,summary_stats",
            "per-page": batch,
            "mailto": CONTACT,
        }
        try:
            r = session.get(API, params=params, timeout=30)
            if r.status_code != 200:
                continue
            for src in (r.json().get("results") or []):
                sid = (src.get("id") or "").rsplit("/", 1)[-1]
                metric = (src.get("summary_stats") or {}).get("2yr_mean_citedness")
                out[sid] = {"name": src.get("display_name"),
                            "metric": round(metric, 2) if metric is not None else None}
        except Exception:
            pass
        time.sleep(0.2)
    return out


def enrich_and_filter(candidates: dict[str, Paper], threshold: float,
                      include_preprints: bool = False,
                      session: Optional[requests.Session] = None,
                      cache: Optional[dict] = None,
                      log=print) -> tuple[dict[str, Paper], int]:
    """Attach journal_metric to each paper and drop those below `threshold`
    (and arXiv preprints unless include_preprints). Returns (kept, dropped).

    Pass `cache` to skip all network/disk (used by tests)."""
    offline = cache is not None
    if not offline:
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = UA
        cache = load_cache()
        need = sorted({p.journal_id for p in candidates.values()
                       if p.journal_id and p.journal_id not in cache})
        if need:
            cache.update(fetch_source_metrics(need, session))
            # remember ids we looked up even if unresolved, so we don't retry daily
            for sid in need:
                cache.setdefault(sid, {"name": None, "metric": None})
            save_cache(cache)

    kept: dict[str, Paper] = {}
    dropped = 0
    for key, p in candidates.items():
        entry = cache.get(p.journal_id or "", {})
        p.journal_metric = entry.get("metric")

        if p.source == "arxiv" and not include_preprints:
            dropped += 1
            continue
        if p.journal_metric is not None and p.journal_metric < threshold:
            dropped += 1
            continue
        kept[key] = p
    return kept, dropped
