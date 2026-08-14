"""Orchestrate sources -> regex filter -> dedup.

`gather_candidates` is side-effect free (good for --dry-run). `select_new`
compares against the seen ledger to find genuinely new papers for a run date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .arxiv import ArxivClient
from .config import Config, Topic
from .models import Paper, canonical_key, normalize_title
from .openalex import OpenAlexClient


def _prefer(a: Paper, b: Paper) -> Paper:
    """Pick the better of two records for the same work (cross-source dup).
    Prefer a real abstract, then the journal-of-record (openalex) over arXiv."""
    if bool(a.abstract_en) != bool(b.abstract_en):
        return a if a.abstract_en else b
    if a.source != b.source:
        return a if a.source == "openalex" else b
    return a


def _prefer_edition(a: Paper, b: Paper) -> Paper:
    """Choose which of two same-title editions to keep: one with an abstract,
    then the English 'International Edition' over a language edition."""
    if bool(a.abstract_en) != bool(b.abstract_en):
        return a if a.abstract_en else b
    ai = "international edition" in (a.venue or "").lower()
    bi = "international edition" in (b.venue or "").lower()
    if ai != bi:
        return a if ai else b
    return a


def dedup_by_title(cands: dict[str, Paper]) -> dict[str, Paper]:
    """Collapse candidates that share an identical normalized title (different
    DOIs), e.g. a paper's German + International editions."""
    seen: dict[str, tuple[str, Paper]] = {}
    for key, p in cands.items():
        tk = normalize_title(p.title)
        if not tk:
            seen[f"__{key}"] = (key, p)
            continue
        if tk in seen:
            k0, p0 = seen[tk]
            win = _prefer_edition(p0, p)
            los = p if win is p0 else p0
            win.merge_topics(los.topics)
            seen[tk] = ((k0 if win is p0 else key), win)
        else:
            seen[tk] = (key, p)
    return {k: p for k, p in seen.values()}


@dataclass
class TopicStat:
    topic: str
    raw: int = 0          # returned by search (pre-filter)
    kept: int = 0         # survived regex post-filter
    new: int = 0          # not previously seen


@dataclass
class GatherResult:
    candidates: dict[str, Paper] = field(default_factory=dict)
    stats: list[TopicStat] = field(default_factory=list)


def gather_candidates(
    cfg: Config,
    from_date: str,
    topic_ids: Optional[list[str]] = None,
    use_arxiv: bool = True,
    oa_client: Optional[OpenAlexClient] = None,
    ax_client: Optional[ArxivClient] = None,
    log=print,
) -> GatherResult:
    """Search all sources for each topic, apply the regex post-filter, and
    accumulate a de-duplicated (by paper id) candidate set with merged topics."""
    oa = oa_client or OpenAlexClient()
    ax = ax_client if ax_client is not None else (ArxivClient() if use_arxiv else None)
    topics = [t for t in cfg.topics if not topic_ids or t.id in topic_ids]

    result = GatherResult()
    for topic in topics:
        stat = TopicStat(topic=topic.id)
        raw_by_id: dict[str, Paper] = {}

        # --- OpenAlex: one request per query phrase, merged ---
        for q in topic.queries:
            for p in oa.search(q, from_date, max_results=cfg.max_per_topic,
                               types=cfg.types):
                raw_by_id.setdefault(p.id, p)

        # --- arXiv preprints (optional) ---
        if ax is not None and topic.arxiv:
            try:
                for p in ax.search(topic.arxiv, from_date,
                                   max_results=min(cfg.max_per_topic, 50)):
                    raw_by_id.setdefault(p.id, p)
            except Exception as e:  # arXiv outages shouldn't kill the run
                log(f"  [arxiv] {topic.id} failed: {e}")

        stat.raw = len(raw_by_id)

        # --- regex precision filter + accumulate into candidate set ---
        # Keyed by canonical DOI so an arXiv preprint and its OpenAlex-indexed
        # copy collapse into one record.
        for p in raw_by_id.values():
            if not topic.matches(p.search_text):
                continue
            stat.kept += 1
            key = canonical_key(p.doi, p.id)
            existing = result.candidates.get(key)
            if existing:
                winner = _prefer(existing, p)
                winner.merge_topics(existing.topics + [topic.id])
                result.candidates[key] = winner
            else:
                p.merge_topics([topic.id])
                result.candidates[key] = p

        log(f"  [{topic.id}] raw={stat.raw} kept={stat.kept}")
        result.stats.append(stat)

    # collapse same-title editions (Angew German vs International, etc.)
    before = len(result.candidates)
    result.candidates = dedup_by_title(result.candidates)
    if len(result.candidates) < before:
        log(f"  title-dedup: {before} -> {len(result.candidates)}")

    return result


def select_new(
    candidates: dict[str, Paper],
    seen: dict[str, str],
    run_date: str,
) -> list[Paper]:
    """Return candidates not in the seen ledger, stamped with first_seen.
    Mutates `seen` in place to register the new ids."""
    new: list[Paper] = []
    for pid, paper in candidates.items():
        if pid in seen:
            continue
        paper.first_seen = run_date
        seen[pid] = run_date
        new.append(paper)
    # newest publication first, then title for stability
    new.sort(key=lambda p: (p.published or "", p.title), reverse=True)
    return new
