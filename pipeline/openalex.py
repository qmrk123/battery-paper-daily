"""OpenAlex client — primary source for newly published journal papers.

Key facts (probed live 2026-08-14, see DESIGN.md):
  * `from_publication_date` filter and `sort=publication_date:desc` are FREE.
    (`from_created_date` / `from_updated_date` / `sort=created_date` now require
    a paid plan — we deliberately avoid them.)
  * No API key required for this volume; a `mailto` puts us in the polite pool.
    An optional OPENALEX_API_KEY env var is honored if set.
  * Abstracts come as an inverted index (word -> [positions]); reconstruct them.
"""
from __future__ import annotations

import os
import time
from typing import Iterable, Iterator, Optional

import requests

from .models import Paper, clean_ws

API = "https://api.openalex.org/works"
SELECT = ",".join([
    "id", "doi", "title", "publication_date", "type",
    "primary_location", "open_access", "authorships",
    "abstract_inverted_index",
])
CONTACT = os.environ.get("CONTACT_EMAIL", "qmrk123@hanyang.ac.kr")
USER_AGENT = f"battery-paper-daily/0.1 (mailto:{CONTACT})"


def reconstruct_abstract(inv: Optional[dict]) -> Optional[str]:
    """Rebuild plaintext from OpenAlex's abstract_inverted_index."""
    if not inv:
        return None
    positions: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    if not positions:
        return None
    text = " ".join(positions[i] for i in sorted(positions))
    return clean_ws(text)


def _short_id(openalex_id: str) -> str:
    # "https://openalex.org/W4412..." -> "W4412..."
    return (openalex_id or "").rsplit("/", 1)[-1]


def _clean_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    return doi.replace("https://doi.org/", "").strip() or None


class OpenAlexClient:
    def __init__(self, mailto: str = CONTACT, api_key: Optional[str] = None,
                 min_interval: float = 0.2, timeout: int = 30):
        self.mailto = mailto
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY")
        self.min_interval = min_interval
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last = 0.0

    def _throttle(self) -> None:
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()

    def _get(self, params: dict, retries: int = 4) -> dict:
        params = dict(params)
        params.setdefault("mailto", self.mailto)
        if self.api_key:
            params["api_key"] = self.api_key
        backoff = 1.0
        for attempt in range(retries):
            self._throttle()
            resp = self.session.get(API, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise RuntimeError(
                f"OpenAlex {resp.status_code}: {resp.text[:200]}"
            )
        raise RuntimeError("OpenAlex: retries exhausted")

    def search(self, query: str, from_date: str, per_page: int = 200,
               max_results: int = 200, types: Optional[Iterable[str]] = None
               ) -> Iterator[Paper]:
        """Yield Papers matching `query` in title/abstract published on/after
        `from_date` (YYYY-MM-DD), newest first. Client-side type filter."""
        types = set(types) if types else None
        yielded = 0
        cursor = "*"
        while cursor and yielded < max_results:
            data = self._get({
                "filter": f"from_publication_date:{from_date},"
                          f"title_and_abstract.search:{query}",
                "sort": "publication_date:desc",
                "per-page": min(per_page, max_results - yielded),
                "select": SELECT,
                "cursor": cursor,
            })
            results = data.get("results") or []
            if not results:
                break
            for r in results:
                if types and r.get("type") not in types:
                    continue
                paper = self._to_paper(r)
                if paper is not None:
                    yield paper
                    yielded += 1
                    if yielded >= max_results:
                        break
            cursor = (data.get("meta") or {}).get("next_cursor")

    def _to_paper(self, r: dict) -> Optional[Paper]:
        title = clean_ws(r.get("title"))
        if not title:
            return None
        pl = r.get("primary_location") or {}
        src = pl.get("source") or {}
        oa = r.get("open_access") or {}
        doi = _clean_doi(r.get("doi"))
        url = (f"https://doi.org/{doi}" if doi
               else pl.get("landing_page_url") or r.get("id"))
        authors = [
            a["author"]["display_name"]
            for a in (r.get("authorships") or [])
            if a.get("author", {}).get("display_name")
        ][:6]
        return Paper(
            id=_short_id(r.get("id")),
            source="openalex",
            title=title,
            url=url,
            published=r.get("publication_date") or "",
            doi=doi,
            authors=authors,
            venue=clean_ws(src.get("display_name")),
            abstract_en=reconstruct_abstract(r.get("abstract_inverted_index")),
            oa_status=oa.get("oa_status"),
            oa_url=oa.get("oa_url"),
        )
