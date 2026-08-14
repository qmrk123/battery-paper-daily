"""arXiv client — secondary source for same-day cond-mat preprints.

arXiv's API returns Atom XML sorted by submittedDate; no key needed, but the
Terms of Use ask for <=1 request / 3 s, so we throttle hard. We only keep
entries published within the rolling window.
"""
from __future__ import annotations

import time
from typing import Iterator, Optional
from xml.etree import ElementTree as ET

import requests

from .models import Paper, clean_ws, strip_latex

API = "http://export.arxiv.org/api/query"
# Narrow to materials + chemical physics; cond-mat.mes-hall was too broad
# (brought in magnetism/transport physics false positives).
CATS = "cat:cond-mat.mtrl-sci OR cat:physics.chem-ph"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
USER_AGENT = "battery-paper-daily/0.1 (mailto:qmrk123@hanyang.ac.kr)"


class ArxivClient:
    def __init__(self, min_interval: float = 3.0, timeout: int = 30):
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

    def search(self, query: str, from_date: str, max_results: int = 50
               ) -> Iterator[Paper]:
        """Yield preprint Papers matching `query` submitted on/after from_date."""
        self._throttle()
        resp = self.session.get(API, params={
            "search_query": f"({CATS}) AND ({query})",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        }, timeout=self.timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for entry in root.findall("a:entry", NS):
            paper = self._to_paper(entry)
            if paper is None:
                continue
            if paper.published < from_date:   # sorted desc -> rest are older
                break
            yield paper

    def _to_paper(self, e: ET.Element) -> Optional[Paper]:
        title = strip_latex(_text(e, "a:title"))
        if not title:
            return None
        abs_url = _text(e, "a:id") or ""
        # "http://arxiv.org/abs/2508.12345v1" -> "2508.12345"
        arxiv_id = abs_url.rsplit("/", 1)[-1].split("v")[0]
        published = (_text(e, "a:published") or "")[:10]
        authors = [
            clean_ws(_text(a, "a:name"))
            for a in e.findall("a:author", NS)
        ]
        authors = [a for a in authors if a][:6]
        doi = _text(e, "arxiv:doi")
        return Paper(
            id=f"arxiv:{arxiv_id}",
            source="arxiv",
            title=title,
            url=abs_url,
            published=published,
            doi=doi,
            authors=authors,
            venue="arXiv preprint",
            abstract_en=strip_latex(_text(e, "a:summary")),
            oa_status="green",
            oa_url=abs_url,
        )


def _text(el: ET.Element, path: str) -> Optional[str]:
    node = el.find(path, NS)
    return node.text if node is not None else None
