"""Normalized data model shared by every pipeline stage and the static site.

The JSON produced here is the *contract* the site (site/app.js) reads, so keep
field names stable. One record = one paper (may belong to >1 topic).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

_TAG = re.compile(r"<[^>]+>")


def clean_ws(text: Optional[str]) -> Optional[str]:
    """Strip HTML tags (OpenAlex leaves <i>/<sub>/<sup> in titles/abstracts),
    unescape entities, collapse whitespace. Return None for empty."""
    if not text:
        return None
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


_LATEX_CMD = re.compile(r"\\(?:textit|textbf|emph|mathrm|mathbf|text|mbox)\s*\{([^}]*)\}")
_LATEX_LEFTOVER = re.compile(r"\\[a-zA-Z]+\s*|[{}$]")


def strip_latex(text: Optional[str]) -> Optional[str]:
    """Lightly de-TeX arXiv titles/abstracts for display: unwrap \\textit{x}
    to x, drop $...$ math delimiters and stray commands. Not a full renderer —
    just enough to keep chemistry readable (Li$_2$M -> Li2M)."""
    if not text:
        return None
    prev = None
    while prev != text:                       # unwrap nested \textit{\mathbf{x}}
        prev = text
        text = _LATEX_CMD.sub(r"\1", text)
    text = text.replace("_", "").replace("^", "")
    text = _LATEX_LEFTOVER.sub("", text)
    return clean_ws(text)


def normalize_title(title: Optional[str]) -> str:
    """Loose title key for dedup: lowercased, alphanumerics only. Catches the same
    paper published in two editions with different DOIs (e.g. Angewandte Chemie vs
    its International Edition, which share an identical title)."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def canonical_key(doi: Optional[str], pid: str) -> str:
    """Cross-source dedup key: prefer normalized DOI, else the paper id.
    arXiv DOIs (10.48550/arXiv.NNNN) are normalized so the arXiv-source record
    and the OpenAlex-indexed copy of the same preprint collapse together."""
    if doi:
        d = doi.lower().strip()
        m = re.search(r"arxiv\.(\d{4}\.\d+)", d)
        if m:
            return f"arxiv:{m.group(1)}"
        return f"doi:{d}"
    if pid.startswith("arxiv:"):
        return pid.lower()
    return pid


@dataclass
class Paper:
    id: str                      # "W4412..." (OpenAlex) or "arxiv:2508.12345"
    source: str                  # "openalex" | "arxiv"
    title: str
    url: str                     # canonical link (doi.org/... or arxiv abs)
    published: str               # publication date "YYYY-MM-DD"
    topics: list[str] = field(default_factory=list)

    doi: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    venue: Optional[str] = None
    abstract_en: Optional[str] = None

    oa_status: Optional[str] = None   # gold/green/hybrid/bronze/closed
    oa_url: Optional[str] = None
    license: Optional[str] = None     # e.g. "cc-by", "cc-by-nc", "cc0", "publisher-specific"

    journal_id: Optional[str] = None     # OpenAlex source id (e.g. "S123") for metric lookup
    journal_metric: Optional[float] = None  # OpenAlex 2yr_mean_citedness (IF-like); quality gate

    first_seen: Optional[str] = None  # date our pipeline first saw it

    # Phase 3 (images): {"src","cached","license","attribution"} or None
    image: Optional[dict] = None
    # Phase 4 (summaries): filled by the Anthropic step
    summary_ko: Optional[str] = None
    relevant: Optional[bool] = None   # LLM relevance-gate verdict

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Paper":
        known = {f for f in Paper.__dataclass_fields__}  # type: ignore[attr-defined]
        return Paper(**{k: v for k, v in d.items() if k in known})

    def merge_topics(self, more: list[str]) -> None:
        for t in more:
            if t not in self.topics:
                self.topics.append(t)

    @property
    def search_text(self) -> str:
        """Text used by the regex post-filter: title + abstract."""
        return f"{self.title or ''} {self.abstract_en or ''}"
