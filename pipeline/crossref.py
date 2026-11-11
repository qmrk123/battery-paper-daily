"""Crossref fallback for journals whose OpenAlex dates are unusable.

RSC deposits Energy & Environmental Science articles with a year-only publication
date (`published: [[2026]]`); OpenAlex turns that into 2026-01-01, so the rolling
`from_publication_date` window never sees a new EES paper. Crossref's deposit
(`created`) date is exact and free to filter on, so these journals are pulled by
ISSN + `from-created-date` instead. Free API, polite pool via mailto; no key.
"""
from __future__ import annotations

import os
import re
from typing import Iterator, Optional

import requests

from .models import Paper, clean_ws

API = "https://api.crossref.org/journals/{issn}/works"
_LEAD = re.compile(r"^\s*(abstract|summary)\s*[:.]?\s*", re.I)


def _date(parts_obj: Optional[dict]) -> Optional[str]:
    parts = ((parts_obj or {}).get("date-parts") or [[None]])[0]
    if len(parts) < 3 or not all(parts):
        return None                                   # year/month-only -> unusable
    y, m, d = parts[:3]
    return f"{y:04d}-{m:02d}-{d:02d}"


def item_to_paper(item: dict, venue: str) -> Optional[Paper]:
    """Crossref work -> Paper. `published` is the full publication date when Crossref
    has one, else the deposit date (what "new" means for these journals)."""
    title = clean_ws((item.get("title") or [None])[0])
    doi = (item.get("DOI") or "").lower().strip()
    if not title or not doi:
        return None
    published = (_date(item.get("published-online")) or _date(item.get("published"))
                 or _date(item.get("created")) or "")
    abstract = clean_ws(item.get("abstract"))
    if abstract:
        abstract = _LEAD.sub("", abstract) or None
    authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x)
               for a in (item.get("author") or [])][:6]
    return Paper(
        id=f"crossref:{doi}",
        source="crossref",
        title=title,
        url=f"https://doi.org/{doi}",
        published=published,
        doi=doi,
        authors=[a for a in authors if a],
        venue=venue,                                  # config name -> passes the allowlist
        abstract_en=abstract,
    )


class CrossrefClient:
    def __init__(self, mailto: Optional[str] = None, timeout: int = 60):
        self.mailto = mailto or os.environ.get("CONTACT_EMAIL") or "noreply@example.com"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"battery-paper-daily (mailto:{self.mailto})"

    def recent(self, issn: str, venue: str, from_date: str,
               max_results: int = 1000) -> Iterator[Paper]:
        """Journal articles deposited on/after `from_date` (YYYY-MM-DD)."""
        cursor, got = "*", 0
        while cursor and got < max_results:
            resp = self.session.get(API.format(issn=issn), timeout=self.timeout, params={
                "filter": f"from-created-date:{from_date},type:journal-article",
                "rows": 200, "cursor": cursor, "mailto": self.mailto,
                "select": "DOI,title,created,published,published-online,abstract,author",
            })
            resp.raise_for_status()
            msg = resp.json()["message"]
            items = msg.get("items") or []
            if not items:
                break
            for it in items:
                p = item_to_paper(it, venue)
                if p is not None:
                    yield p
                    got += 1
            cursor = msg.get("next-cursor")
